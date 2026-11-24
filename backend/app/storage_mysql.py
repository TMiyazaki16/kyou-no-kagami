"""MySQL（Cloud SQL for MySQL）版の Store。

`storage.Store` と同じメソッドを持つ。`DB_BACKEND=mysql` のときだけ `storage.create_store()` から使われ、
PyMySQL もそのときだけ import する（SQLite だけの環境ではドライバが無くても動く）。

接続情報は環境変数から受け取る（値はログ・例外メッセージ・repr に出さない）:
  MYSQL_HOST      ホスト名、または Unix ソケットのパス（`/` で始まる。Cloud Run では `/cloudsql/<接続名>`）
  MYSQL_PORT      TCP のポート（既定 3306。ソケット接続では使わない）
  MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE

SQLite 版との方言差（意図して変えている点）:
- プレースホルダは `%s`。SQL 中にリテラルの `%` を書かない（書くなら `%%`）
- 主キーは `BIGINT AUTO_INCREMENT`。索引・一意制約に使う列は TEXT ではなく VARCHAR/CHAR
- 長文（回答・下書き・JSON）は TEXT / MEDIUMTEXT。MySQL では TEXT にリテラルの DEFAULT を付けられない
  （8.0.13 未満）ので、既定値はアプリ側で必ず渡す
- 日時は SQLite と同じくタイムゾーン付き ISO 8601 文字列（VARCHAR）で保存する。DATETIME にすると
  タイムゾーンが落ち、main.py の `_parse_ts`（記録と解析の新旧比較・セッション期限）が変わるため
- 文字コードは utf8mb4、照合順序は utf8mb4_bin（SQLite と同じバイナリ比較。既定の *_ai_ci だと
  メールアドレスや家族コード、トークンハッシュの照合が大文字小文字・濁点を区別しなくなる）
- 記録は (user_id, date) に UNIQUE を付け、upsert を INSERT ... ON DUPLICATE KEY UPDATE で行う
  （Cloud Run の複数インスタンスではプロセス内ロックが効かないため。SQLite 版は旧データ互換で UNIQUE なし）
- 「INSERT OR IGNORE」は使わず、一意制約違反（1062）を捕まえて判定する（INSERT IGNORE は他のエラーも警告に落とすため）
"""
from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from typing import Any, Iterator

from .storage import AlreadySentError, DuplicateError, Store, _analysis_row, _now

_ER_DUP_ENTRY = 1062
_TABLE_OPTS = "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin"

MYSQL_SCHEMA = [
    f"""CREATE TABLE IF NOT EXISTS records (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    date CHAR(10) NOT NULL,
    gratitude TEXT NOT NULL,
    mood TEXT NOT NULL,
    transcript TEXT NOT NULL,
    audio_path VARCHAR(512) NULL,
    photo_path VARCHAR(512) NULL,
    created_at VARCHAR(40) NOT NULL,
    UNIQUE KEY uq_records_user_date (user_id, date)
) {_TABLE_OPTS}""",
    f"""CREATE TABLE IF NOT EXISTS analyses (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    record_id BIGINT NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    date CHAR(10) NOT NULL,
    todays_word TEXT NOT NULL,
    observations MEDIUMTEXT NOT NULL,
    engine VARCHAR(32) NOT NULL,
    baseline_day INT NOT NULL,
    request_json MEDIUMTEXT NOT NULL,
    response_json MEDIUMTEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    kind VARCHAR(32) NOT NULL DEFAULT 'word',
    notice TEXT NOT NULL,
    fallback_reason VARCHAR(64) NOT NULL DEFAULT '',
    KEY idx_analyses_record (record_id),
    KEY idx_analyses_user (user_id)
) {_TABLE_OPTS}""",
    f"""CREATE TABLE IF NOT EXISTS shares (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    from_user VARCHAR(64) NOT NULL,
    to_user VARCHAR(64) NOT NULL,
    record_id BIGINT NULL,
    draft_text TEXT NOT NULL,
    final_text TEXT NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'draft',
    created_at VARCHAR(40) NOT NULL,
    sent_at VARCHAR(40) NULL,
    photo_path VARCHAR(512) NULL,
    snapshot_json MEDIUMTEXT NULL,
    comment TEXT NULL,
    KEY idx_shares_from (from_user, status),
    KEY idx_shares_to (to_user, status)
) {_TABLE_OPTS}""",
    f"""CREATE TABLE IF NOT EXISTS stamps (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    share_id BIGINT NOT NULL,
    from_user VARCHAR(64) NOT NULL,
    stamp VARCHAR(64) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    KEY idx_stamps_share (share_id)
) {_TABLE_OPTS}""",
    f"""CREATE TABLE IF NOT EXISTS letter_replies (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    share_id BIGINT NOT NULL,
    from_user VARCHAR(64) NOT NULL,
    body TEXT NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    KEY idx_letter_replies_share (share_id)
) {_TABLE_OPTS}""",
    f"""CREATE TABLE IF NOT EXISTS users (
    id VARCHAR(64) NOT NULL PRIMARY KEY,
    email VARCHAR(254) NOT NULL,
    display_name VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    family_code VARCHAR(32) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    UNIQUE KEY uq_users_email (email),
    UNIQUE KEY uq_users_family_code (family_code)
) {_TABLE_OPTS}""",
    f"""CREATE TABLE IF NOT EXISTS sessions (
    token_hash CHAR(64) NOT NULL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    expires_at VARCHAR(40) NOT NULL,
    KEY idx_sessions_user (user_id)
) {_TABLE_OPTS}""",
    f"""CREATE TABLE IF NOT EXISTS family_links (
    user_a VARCHAR(64) NOT NULL,
    user_b VARCHAR(64) NOT NULL,
    created_at VARCHAR(40) NOT NULL,
    PRIMARY KEY (user_a, user_b),
    KEY idx_family_links_b (user_b)
) {_TABLE_OPTS}""",
]

# 既存DBへの後方互換マイグレーション（storage._ANALYSIS_EXTRA_COLUMNS の MySQL 版）。
# 新しい列を足すときは両方に足す。TEXT 列はリテラル DEFAULT を付けられないので VARCHAR か、
# DEFAULT なしの TEXT（アプリが必ず値を渡す）にする。
_MYSQL_ANALYSIS_EXTRA_COLUMNS = {
    "kind": "VARCHAR(32) NOT NULL DEFAULT 'word'",
    "notice": "TEXT NOT NULL",
    "fallback_reason": "VARCHAR(64) NOT NULL DEFAULT ''",
}
# storage._SHARE_EXTRA_COLUMNS の MySQL 版
_MYSQL_SHARE_EXTRA_COLUMNS = {
    "photo_path": "VARCHAR(512) NULL",
    "snapshot_json": "MEDIUMTEXT NULL",   # NULL は旧版の便り
    "comment": "TEXT NULL",               # TEXT にリテラル DEFAULT を付けられないので NULL 可（アプリが必ず渡す）
}

APP_TABLES = ("letter_replies", "stamps", "shares", "analyses", "records", "family_links", "sessions", "users")


def mysql_settings_from_env() -> dict[str, Any]:
    """接続設定を環境変数から組み立てる。パスワードを含むので、この dict をログに出さない。"""
    host = os.environ.get("MYSQL_HOST", "").strip() or "127.0.0.1"
    settings: dict[str, Any] = {
        "user": os.environ.get("MYSQL_USER", ""),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DATABASE", ""),
    }
    if host.startswith("/"):
        settings["unix_socket"] = host  # Cloud Run: /cloudsql/<PROJECT:REGION:INSTANCE>
    else:
        settings["host"] = host
        settings["port"] = int(os.environ.get("MYSQL_PORT", "") or 3306)
    missing = [k for k in ("user", "database") if not settings[k]]
    if missing:
        raise RuntimeError("DB_BACKEND=mysql ですが MYSQL_USER / MYSQL_DATABASE が未設定です")
    return settings


class MySQLStore(Store):
    def __init__(self, settings: dict[str, Any] | None = None, connect_wait_sec: float | None = None):
        import pymysql  # DB_BACKEND=mysql のときだけ必要
        import pymysql.cursors

        self._pymysql = pymysql
        self._settings = dict(settings or mysql_settings_from_env())
        self._settings.update({
            "charset": "utf8mb4",
            "cursorclass": pymysql.cursors.DictCursor,
            "autocommit": False,
            "connect_timeout": 10,
        })
        if connect_wait_sec is None:
            connect_wait_sec = float(os.environ.get("MYSQL_CONNECT_WAIT_SEC", "") or 30)
        self._wait_until_ready(connect_wait_sec)
        self._ensure_schema()

    def __repr__(self) -> str:  # 接続情報（特にパスワード）を出さない
        return "MySQLStore()"

    def describe(self) -> str:
        """ログ用の接続先（パスワード・ユーザー名は含めない）。"""
        s = self._settings
        where = f"socket {s['unix_socket']}" if "unix_socket" in s else f"{s['host']}:{s['port']}"
        return f"MySQL {where} / {s['database']}"

    # ---- connection ----------------------------------------------------
    def _connect(self):
        return self._pymysql.connect(**self._settings)

    def _wait_until_ready(self, wait_sec: float) -> None:
        """起動直後の MySQL（compose）や Cloud SQL のソケット準備待ち。認証エラーなどは待たずに上げる。"""
        deadline = time.monotonic() + max(wait_sec, 0)
        while True:
            try:
                self._connect().close()
                return
            except self._pymysql.err.OperationalError as e:
                code = e.args[0] if e.args else None
                # 2003/2002: 接続できない（まだ起動していない）。それ以外（1045 認証失敗等）は即座に失敗
                if code not in (2002, 2003, 2013) or time.monotonic() >= deadline:
                    raise RuntimeError(f"MySQL に接続できません（エラーコード {code}）") from None
                time.sleep(1)

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        """1操作 = 1接続・1トランザクション。成功で commit、例外で rollback、必ず close。"""
        conn = self._connect()
        try:
            with conn.cursor() as cur:
                yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self._conn() as cur:
            for ddl in MYSQL_SCHEMA:
                cur.execute(ddl)
            for table, extra in (("analyses", _MYSQL_ANALYSIS_EXTRA_COLUMNS),
                                 ("shares", _MYSQL_SHARE_EXTRA_COLUMNS)):
                cur.execute(
                    "SELECT COLUMN_NAME AS name FROM information_schema.COLUMNS"
                    " WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s", (table,)
                )
                cols = {r["name"] for r in cur.fetchall()}
                for name, ddl in extra.items():
                    if name not in cols:
                        cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def reset_all(self) -> None:
        """全テーブルを空にする（seed_demo.py --reset 用。Store インターフェース外）。"""
        with self._conn() as cur:
            for t in APP_TABLES:
                cur.execute(f"DELETE FROM {t}")
            for t in APP_TABLES:
                if t not in ("family_links", "sessions", "users"):
                    cur.execute(f"ALTER TABLE {t} AUTO_INCREMENT = 1")

    def _is_dup(self, e: Exception) -> bool:
        return isinstance(e, self._pymysql.err.IntegrityError) and bool(e.args) and e.args[0] == _ER_DUP_ENTRY

    def _one(self, sql: str, args: tuple = ()) -> dict | None:
        with self._conn() as cur:
            cur.execute(sql, args)
            return cur.fetchone()

    def _all(self, sql: str, args: tuple = ()) -> list[dict]:
        with self._conn() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())

    # ---- accounts ------------------------------------------------------
    def create_user(self, user: dict) -> dict:
        try:
            with self._conn() as cur:
                cur.execute(
                    "INSERT INTO users (id, email, display_name, password_hash, family_code, created_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (user["id"], user["email"], user["display_name"], user["password_hash"],
                     user["family_code"], _now()),
                )
        except Exception as e:
            if not self._is_dup(e):
                raise
            msg = str(e.args[1]) if len(e.args) > 1 else ""
            field = "email" if "uq_users_email" in msg else "family_code" if "uq_users_family_code" in msg else "id"
            raise DuplicateError(field) from None
        return self.get_user(user["id"])  # type: ignore[return-value]

    def _user_where(self, column: str, value: str) -> dict | None:
        assert column in ("id", "email", "family_code")
        return self._one(
            f"SELECT id, email, display_name, family_code, created_at FROM users WHERE {column} = %s", (value,)
        )

    def get_user(self, user_id: str) -> dict | None:
        return self._user_where("id", user_id)

    def find_user_by_email(self, email: str) -> dict | None:
        return self._user_where("email", email)

    def find_user_by_family_code(self, code: str) -> dict | None:
        return self._user_where("family_code", code)

    def get_password_hash(self, user_id: str) -> str | None:
        row = self._one("SELECT password_hash FROM users WHERE id = %s", (user_id,))
        return row["password_hash"] if row else None

    def has_data_for(self, user_id: str) -> bool:
        row = self._one(
            "SELECT (EXISTS(SELECT 1 FROM records WHERE user_id = %s)"
            " OR EXISTS(SELECT 1 FROM shares WHERE from_user = %s OR to_user = %s)) AS found",
            (user_id, user_id, user_id),
        )
        return bool(row and row["found"])

    # ---- sessions ------------------------------------------------------
    def create_session(self, token_hash: str, user_id: str, expires_at: str) -> None:
        with self._conn() as cur:
            cur.execute(
                "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (%s, %s, %s, %s)",
                (token_hash, user_id, _now(), expires_at),
            )

    def get_session(self, token_hash: str) -> dict | None:
        return self._one("SELECT * FROM sessions WHERE token_hash = %s", (token_hash,))

    def delete_session(self, token_hash: str) -> None:
        with self._conn() as cur:
            cur.execute("DELETE FROM sessions WHERE token_hash = %s", (token_hash,))

    # ---- family links ----------------------------------------------------
    @staticmethod
    def _pair(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a < b else (b, a)

    def add_family_link(self, user_a: str, user_b: str) -> bool:
        try:
            with self._conn() as cur:
                cur.execute("INSERT INTO family_links (user_a, user_b, created_at) VALUES (%s, %s, %s)",
                            (*self._pair(user_a, user_b), _now()))
            return True
        except Exception as e:
            if self._is_dup(e):
                return False
            raise

    def remove_family_link(self, user_a: str, user_b: str) -> bool:
        with self._conn() as cur:
            n = cur.execute("DELETE FROM family_links WHERE user_a = %s AND user_b = %s", self._pair(user_a, user_b))
        return n > 0

    def are_linked(self, user_a: str, user_b: str) -> bool:
        return self._one("SELECT 1 AS x FROM family_links WHERE user_a = %s AND user_b = %s",
                         self._pair(user_a, user_b)) is not None

    def list_family(self, user_id: str) -> list[dict]:
        return self._all(
            "SELECT u.id, u.display_name FROM family_links f"
            " JOIN users u ON u.id = CASE WHEN f.user_a = %s THEN f.user_b ELSE f.user_a END"
            " WHERE f.user_a = %s OR f.user_b = %s ORDER BY f.created_at, u.id",
            (user_id, user_id, user_id),
        )

    # ---- records -------------------------------------------------------
    @staticmethod
    def _record_values(rec: dict) -> tuple:
        return (rec.get("gratitude", ""), rec.get("mood", ""), rec.get("transcript", ""),
                rec.get("audio_path"), rec.get("photo_path"), rec.get("created_at") or _now())

    def create_record(self, rec: dict) -> dict:
        with self._conn() as cur:
            cur.execute(
                "INSERT INTO records (user_id, date, gratitude, mood, transcript, audio_path, photo_path, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (rec["user_id"], rec["date"], *self._record_values(rec)),
            )
            cur.execute("SELECT * FROM records WHERE id = %s", (cur.lastrowid,))
            return cur.fetchone()

    def find_record(self, user_id: str, date: str) -> dict | None:
        return self._one(
            "SELECT * FROM records WHERE user_id = %s AND date = %s ORDER BY id DESC LIMIT 1", (user_id, date)
        )

    def upsert_record(self, rec: dict) -> tuple[dict, dict | None]:
        """1トランザクションで旧行を読み（行ロック）、INSERT ... ON DUPLICATE KEY UPDATE で上書きする。"""
        values = self._record_values(rec)
        with self._conn() as cur:
            cur.execute("SELECT * FROM records WHERE user_id = %s AND date = %s FOR UPDATE",
                        (rec["user_id"], rec["date"]))
            old = cur.fetchone()
            cur.execute(
                "INSERT INTO records (user_id, date, gratitude, mood, transcript, audio_path, photo_path, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
                " ON DUPLICATE KEY UPDATE gratitude = %s, mood = %s, transcript = %s,"
                " audio_path = %s, photo_path = %s, created_at = %s",
                (rec["user_id"], rec["date"], *values, *values),
            )
            cur.execute("SELECT * FROM records WHERE user_id = %s AND date = %s", (rec["user_id"], rec["date"]))
            new = cur.fetchone()
        return new, old

    def get_record(self, record_id: int) -> dict | None:
        return self._one("SELECT * FROM records WHERE id = %s", (record_id,))

    def update_record(self, record_id: int, fields: dict) -> dict | None:
        allowed = {"transcript"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if sets:
            clause = ", ".join(f"{k} = %s" for k in sets)
            with self._conn() as cur:
                cur.execute(f"UPDATE records SET {clause} WHERE id = %s", (*sets.values(), record_id))
        return self.get_record(record_id)

    def list_records(self, user_id: str) -> list[dict]:
        return self._all("SELECT * FROM records WHERE user_id = %s ORDER BY date DESC, id DESC", (user_id,))

    def distinct_record_days(self, user_id: str, upto_date: str) -> int:
        row = self._one(
            "SELECT COUNT(DISTINCT date) AS n FROM records WHERE user_id = %s AND date <= %s", (user_id, upto_date)
        )
        return int(row["n"]) if row else 0

    def previous_record(self, user_id: str, before_id: int) -> dict | None:
        """直前の「日」の記録。MySQL は同じテーブルを参照するサブクエリを持てるが、読みやすさのため2段で引く。"""
        with self._conn() as cur:
            cur.execute("SELECT date FROM records WHERE id = %s", (before_id,))
            base = cur.fetchone()
            if not base:
                return None
            cur.execute(
                "SELECT * FROM records WHERE user_id = %s AND date < %s ORDER BY date DESC, id DESC LIMIT 1",
                (user_id, base["date"]),
            )
            return cur.fetchone()

    def weekly_summary(self, user_id: str, week_dates: list[str]) -> dict:
        if not week_dates:
            return {"days_recorded": 0, "total_records": 0}
        placeholders = ",".join("%s" for _ in week_dates)
        row = self._one(
            f"SELECT COUNT(DISTINCT date) AS days, COUNT(*) AS total FROM records"
            f" WHERE user_id = %s AND date IN ({placeholders})",
            (user_id, *week_dates),
        )
        return {"days_recorded": int(row["days"]), "total_records": int(row["total"])}  # type: ignore[index]

    # ---- analyses ------------------------------------------------------
    def save_analysis(self, ana: dict) -> dict:
        with self._conn() as cur:
            cur.execute(
                "INSERT INTO analyses (record_id, user_id, date, todays_word, observations, engine,"
                " baseline_day, request_json, response_json, created_at, kind, notice, fallback_reason)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (ana["record_id"], ana["user_id"], ana["date"], ana.get("todays_word") or "",
                 json.dumps(ana.get("observations", []), ensure_ascii=False), ana["engine"],
                 ana["baseline_day"], json.dumps(ana.get("request", {}), ensure_ascii=False),
                 json.dumps(ana.get("response", {}), ensure_ascii=False), _now(),
                 ana.get("kind", "word"), ana.get("notice", ""), ana.get("fallback_reason", "")),
            )
            cur.execute("SELECT * FROM analyses WHERE id = %s", (cur.lastrowid,))
            row = cur.fetchone()
        return _analysis_row(row)

    def latest_analysis(self, user_id: str) -> dict | None:
        row = self._one("SELECT * FROM analyses WHERE user_id = %s ORDER BY id DESC LIMIT 1", (user_id,))
        return _analysis_row(row) if row else None

    def analysis_for_record(self, record_id: int) -> dict | None:
        row = self._one("SELECT * FROM analyses WHERE record_id = %s ORDER BY id DESC LIMIT 1", (record_id,))
        return _analysis_row(row) if row else None

    # ---- shares / stamps -------------------------------------------------
    def create_letters(self, from_user: str, record_id: int, to_users: list[str], letter: dict) -> list[dict]:
        now = _now()
        sent_at = letter.get("sent_at") or now   # seed_demo.py が過去の日付で作るときだけ指定する
        with self._conn() as cur:   # 1つの接続＝1トランザクションで、判定と作成をまとめる
            marks = ", ".join("%s" for _ in to_users)
            # FOR UPDATE：同じ送り手の便りの行（索引 idx_shares_from の範囲）をロックし、同時の二重送信を直列にする
            cur.execute(
                f"SELECT to_user FROM shares WHERE from_user = %s AND status = 'sent' AND record_id = %s"
                f" AND to_user IN ({marks}) FOR UPDATE", (from_user, record_id, *to_users))
            dup = sorted({r["to_user"] for r in cur.fetchall()})
            if dup:
                raise AlreadySentError(dup)
            ids = []
            for to_user in to_users:
                cur.execute(
                    "INSERT INTO shares (from_user, to_user, record_id, draft_text, final_text, status, created_at,"
                    " sent_at, photo_path, snapshot_json, comment)"
                    " VALUES (%s, %s, %s, '', %s, 'sent', %s, %s, %s, %s, %s)",
                    (from_user, to_user, record_id, letter.get("comment", ""), now, sent_at,
                     letter.get("photo_path"), letter["snapshot_json"], letter.get("comment", "")),
                )
                ids.append(cur.lastrowid)
            out = []
            for i in ids:
                cur.execute("SELECT * FROM shares WHERE id = %s", (i,))
                out.append(cur.fetchone())
            return out

    def sent_recipients(self, from_user: str, record_id: int) -> list[str]:
        rows = self._all("SELECT DISTINCT to_user FROM shares WHERE from_user = %s AND status = 'sent'"
                         " AND record_id = %s", (from_user, record_id))
        return [r["to_user"] for r in rows]

    def get_share(self, share_id: int) -> dict | None:
        return self._one("SELECT * FROM shares WHERE id = %s", (share_id,))

    def delete_share(self, share_id: int) -> bool:
        with self._conn() as cur:   # 1つの接続＝1トランザクションで、反応・返信ごと消す
            cur.execute("DELETE FROM stamps WHERE share_id = %s", (share_id,))
            cur.execute("DELETE FROM letter_replies WHERE share_id = %s", (share_id,))
            return bool(cur.execute("DELETE FROM shares WHERE id = %s", (share_id,)))

    def _letters(self, column: str, user_id: str) -> list[dict]:
        assert column in ("to_user", "from_user")
        rows = self._all(f"SELECT * FROM shares WHERE {column} = %s AND status = 'sent' ORDER BY id DESC", (user_id,))
        for letter in rows:
            letter["stamps"] = self.list_stamps(letter["id"])
            letter["replies"] = self.list_replies(letter["id"])
        return rows

    def list_letters_for(self, user_id: str) -> list[dict]:
        return self._letters("to_user", user_id)

    def list_letters_sent_by(self, user_id: str) -> list[dict]:
        return self._letters("from_user", user_id)

    def add_stamp(self, share_id: int, from_user: str, stamp: str) -> dict:
        with self._conn() as cur:
            cur.execute("INSERT INTO stamps (share_id, from_user, stamp, created_at) VALUES (%s, %s, %s, %s)",
                        (share_id, from_user, stamp, _now()))
            cur.execute("SELECT * FROM stamps WHERE id = %s", (cur.lastrowid,))
            return cur.fetchone()

    def list_stamps(self, share_id: int) -> list[dict]:
        return self._all("SELECT * FROM stamps WHERE share_id = %s ORDER BY id ASC", (share_id,))

    def toggle_stamp(self, share_id: int, from_user: str, stamp: str) -> bool:
        with self._conn() as cur:
            # 旧版の add_stamp で重なったものも含めてまとめて外す。stamps 表は旧データに重複がありうるので
            # 一意制約は付けていない（同時の二度押しで重なっても、表示側で人×種類ごとに1つにまとめる）
            removed = cur.execute("DELETE FROM stamps WHERE share_id = %s AND from_user = %s AND stamp = %s",
                                  (share_id, from_user, stamp))
            if removed:
                return False
            cur.execute("INSERT INTO stamps (share_id, from_user, stamp, created_at) VALUES (%s, %s, %s, %s)",
                        (share_id, from_user, stamp, _now()))
            return True

    # ---- letter replies --------------------------------------------------
    def add_reply(self, share_id: int, from_user: str, body: str) -> dict:
        with self._conn() as cur:
            cur.execute("INSERT INTO letter_replies (share_id, from_user, body, created_at) VALUES (%s, %s, %s, %s)",
                        (share_id, from_user, body, _now()))
            cur.execute("SELECT * FROM letter_replies WHERE id = %s", (cur.lastrowid,))
            return cur.fetchone()

    def list_replies(self, share_id: int) -> list[dict]:
        return self._all("SELECT * FROM letter_replies WHERE share_id = %s ORDER BY id ASC", (share_id,))

    def get_reply(self, reply_id: int) -> dict | None:
        return self._one("SELECT * FROM letter_replies WHERE id = %s", (reply_id,))

    def delete_reply(self, reply_id: int) -> bool:
        with self._conn() as cur:
            return bool(cur.execute("DELETE FROM letter_replies WHERE id = %s", (reply_id,)))
