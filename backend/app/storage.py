"""Storage layer.

All persistence goes through `Store`. Implementations:
- `SQLiteStore` (local file) — local development and tests (default)
- `MySQLStore` (`storage_mysql.py`) — production on Cloud SQL for MySQL

`create_store()` picks one from the `DB_BACKEND` environment variable
(`mysql` → MySQL, anything else → SQLite). To add another backend (e.g.
Firestore), implement the same methods in a new class — no API or frontend
changes needed.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator


class DuplicateError(Exception):
    """一意であるべき値（メールアドレス・家族コードなど）が既に使われている。"""

    def __init__(self, field: str):
        super().__init__(f"duplicate: {field}")
        self.field = field


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    """Interface for the app storage. Swap implementations freely."""

    # accounts（返す dict に password_hash は含めない。照合用は get_password_hash だけ）
    def create_user(self, user: dict) -> dict:
        """user: id, email, display_name, password_hash, family_code。重複は DuplicateError(field)。"""
        raise NotImplementedError
    def get_user(self, user_id: str) -> dict | None: raise NotImplementedError
    def find_user_by_email(self, email: str) -> dict | None: raise NotImplementedError
    def find_user_by_family_code(self, code: str) -> dict | None: raise NotImplementedError
    def get_password_hash(self, user_id: str) -> str | None: raise NotImplementedError
    def has_data_for(self, user_id: str) -> bool:
        """記録・共有のどれかにこの user_id が使われているか（旧データの移行判定用）。"""
        raise NotImplementedError

    # sessions（トークン本体ではなくハッシュで保存する）
    def create_session(self, token_hash: str, user_id: str, expires_at: str) -> None: raise NotImplementedError
    def get_session(self, token_hash: str) -> dict | None: raise NotImplementedError
    def delete_session(self, token_hash: str) -> None: raise NotImplementedError

    # family links（向きのない1対1の連携）
    def add_family_link(self, user_a: str, user_b: str) -> bool:
        """連携を追加する。新しく追加したら True、既に連携済みなら False。"""
        raise NotImplementedError
    def remove_family_link(self, user_a: str, user_b: str) -> bool: raise NotImplementedError
    def are_linked(self, user_a: str, user_b: str) -> bool: raise NotImplementedError
    def list_family(self, user_id: str) -> list[dict]: raise NotImplementedError

    # records
    def create_record(self, rec: dict) -> dict: raise NotImplementedError
    def upsert_record(self, rec: dict) -> tuple[dict, dict | None]:
        """1ユーザー1日1件。同じ (user_id, date) があれば上書きし、(新, 旧) を返す。"""
        raise NotImplementedError
    def find_record(self, user_id: str, date: str) -> dict | None: raise NotImplementedError
    def get_record(self, record_id: int) -> dict | None: raise NotImplementedError
    def update_record(self, record_id: int, fields: dict) -> dict | None: raise NotImplementedError
    def list_records(self, user_id: str) -> list[dict]: raise NotImplementedError
    def distinct_record_days(self, user_id: str, upto_date: str) -> int: raise NotImplementedError
    def previous_record(self, user_id: str, before_id: int) -> dict | None: raise NotImplementedError
    def weekly_summary(self, user_id: str, week_dates: list[str]) -> dict: raise NotImplementedError

    # analyses
    def save_analysis(self, ana: dict) -> dict: raise NotImplementedError
    def latest_analysis(self, user_id: str) -> dict | None: raise NotImplementedError
    def analysis_for_record(self, record_id: int) -> dict | None: raise NotImplementedError

    # shares / letters / stamps
    def create_share(self, share: dict) -> dict: raise NotImplementedError
    def get_share(self, share_id: int) -> dict | None: raise NotImplementedError
    def update_share(self, share_id: int, fields: dict) -> dict | None: raise NotImplementedError
    def list_letters_for(self, user_id: str) -> list[dict]: raise NotImplementedError
    def list_letters_sent_by(self, user_id: str) -> list[dict]: raise NotImplementedError
    def add_stamp(self, share_id: int, from_user: str, stamp: str) -> dict: raise NotImplementedError
    def list_stamps(self, share_id: int) -> list[dict]: raise NotImplementedError


SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    gratitude TEXT NOT NULL DEFAULT '',
    mood TEXT NOT NULL DEFAULT '',
    transcript TEXT NOT NULL DEFAULT '',
    audio_path TEXT,
    photo_path TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analyses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id INTEGER NOT NULL,
    user_id TEXT NOT NULL,
    date TEXT NOT NULL,
    todays_word TEXT NOT NULL,
    observations TEXT NOT NULL DEFAULT '[]',
    engine TEXT NOT NULL,
    baseline_day INTEGER NOT NULL,
    request_json TEXT NOT NULL DEFAULT '{}',
    response_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_user TEXT NOT NULL,
    to_user TEXT NOT NULL,
    record_id INTEGER,
    draft_text TEXT NOT NULL DEFAULT '',
    final_text TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    sent_at TEXT
);
CREATE TABLE IF NOT EXISTS stamps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    share_id INTEGER NOT NULL,
    from_user TEXT NOT NULL,
    stamp TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_records_user_date ON records (user_id, date);
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    family_code TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS family_links (
    user_a TEXT NOT NULL,
    user_b TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_a, user_b)
);
"""

_USER_PUBLIC_COLUMNS = "id, email, display_name, family_code, created_at"

# 既存DBへの後方互換マイグレーション（列が無ければ追加）
_ANALYSIS_EXTRA_COLUMNS = {
    "kind": "TEXT NOT NULL DEFAULT 'word'",          # 'word' | 'voice_notice'
    "notice": "TEXT NOT NULL DEFAULT ''",            # kind=voice_notice のときの案内文
    "fallback_reason": "TEXT NOT NULL DEFAULT ''",   # '' | no_api_key | gemini_error | guard_*
}


class SQLiteStore(Store):
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or os.environ.get(
            "APP_DB_PATH",
            os.path.join(os.path.dirname(__file__), "..", "data", "app.db"),
        )
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._lock = threading.RLock()
        with self._conn() as c:
            c.executescript(SCHEMA)
            cols = {r["name"] for r in c.execute("PRAGMA table_info(analyses)").fetchall()}
            for name, ddl in _ANALYSIS_EXTRA_COLUMNS.items():
                if name not in cols:
                    c.execute(f"ALTER TABLE analyses ADD COLUMN {name} {ddl}")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """commit on success, rollback on error, always close (L-4)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ---- accounts ------------------------------------------------------
    def create_user(self, user: dict) -> dict:
        try:
            with self._lock, self._conn() as c:
                c.execute(
                    "INSERT INTO users (id, email, display_name, password_hash, family_code, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (user["id"], user["email"], user["display_name"], user["password_hash"],
                     user["family_code"], _now()),
                )
        except sqlite3.IntegrityError as e:
            msg = str(e)
            field = "email" if "users.email" in msg else "family_code" if "users.family_code" in msg else "id"
            raise DuplicateError(field) from None
        return self.get_user(user["id"])  # type: ignore[return-value]

    def _user_where(self, where: str, value: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(f"SELECT {_USER_PUBLIC_COLUMNS} FROM users WHERE {where} = ?", (value,)).fetchone()
        return dict(row) if row else None

    def get_user(self, user_id: str) -> dict | None:
        return self._user_where("id", user_id)

    def find_user_by_email(self, email: str) -> dict | None:
        return self._user_where("email", email)

    def find_user_by_family_code(self, code: str) -> dict | None:
        return self._user_where("family_code", code)

    def get_password_hash(self, user_id: str) -> str | None:
        with self._conn() as c:
            row = c.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["password_hash"] if row else None

    def has_data_for(self, user_id: str) -> bool:
        with self._conn() as c:
            row = c.execute(
                "SELECT EXISTS(SELECT 1 FROM records WHERE user_id = ?)"
                " OR EXISTS(SELECT 1 FROM shares WHERE from_user = ? OR to_user = ?) AS found",
                (user_id, user_id, user_id),
            ).fetchone()
        return bool(row["found"])

    # ---- sessions ------------------------------------------------------
    def create_session(self, token_hash: str, user_id: str, expires_at: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                      (token_hash, user_id, _now(), expires_at))

    def get_session(self, token_hash: str) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM sessions WHERE token_hash = ?", (token_hash,)).fetchone()
        return dict(row) if row else None

    def delete_session(self, token_hash: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    # ---- family links ----------------------------------------------------
    @staticmethod
    def _pair(a: str, b: str) -> tuple[str, str]:
        return (a, b) if a < b else (b, a)

    def add_family_link(self, user_a: str, user_b: str) -> bool:
        with self._lock, self._conn() as c:
            cur = c.execute("INSERT OR IGNORE INTO family_links (user_a, user_b, created_at) VALUES (?, ?, ?)",
                            (*self._pair(user_a, user_b), _now()))
            return cur.rowcount > 0

    def remove_family_link(self, user_a: str, user_b: str) -> bool:
        with self._lock, self._conn() as c:
            cur = c.execute("DELETE FROM family_links WHERE user_a = ? AND user_b = ?", self._pair(user_a, user_b))
            return cur.rowcount > 0

    def are_linked(self, user_a: str, user_b: str) -> bool:
        with self._conn() as c:
            row = c.execute("SELECT 1 FROM family_links WHERE user_a = ? AND user_b = ?",
                            self._pair(user_a, user_b)).fetchone()
        return row is not None

    def list_family(self, user_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT u.id, u.display_name FROM family_links f"
                " JOIN users u ON u.id = CASE WHEN f.user_a = ? THEN f.user_b ELSE f.user_a END"
                " WHERE f.user_a = ? OR f.user_b = ? ORDER BY f.created_at, u.id",
                (user_id, user_id, user_id),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- records -------------------------------------------------------
    def create_record(self, rec: dict) -> dict:
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO records (user_id, date, gratitude, mood, transcript, audio_path, photo_path, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (rec["user_id"], rec["date"], rec.get("gratitude", ""), rec.get("mood", ""),
                 rec.get("transcript", ""), rec.get("audio_path"), rec.get("photo_path"),
                 rec.get("created_at") or _now()),
            )
            row = c.execute("SELECT * FROM records WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def find_record(self, user_id: str, date: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM records WHERE user_id = ? AND date = ? ORDER BY id DESC LIMIT 1",
                (user_id, date),
            ).fetchone()
        return dict(row) if row else None

    def upsert_record(self, rec: dict) -> tuple[dict, dict | None]:
        with self._lock:
            old = self.find_record(rec["user_id"], rec["date"])
            if old is None:
                return self.create_record(rec), None
            with self._conn() as c:
                c.execute(
                    "UPDATE records SET gratitude = ?, mood = ?, transcript = ?, audio_path = ?,"
                    " photo_path = ?, created_at = ? WHERE id = ?",
                    (rec.get("gratitude", ""), rec.get("mood", ""), rec.get("transcript", ""),
                     rec.get("audio_path"), rec.get("photo_path"),
                     rec.get("created_at") or _now(), old["id"]),
                )
            return self.get_record(old["id"]), old  # type: ignore[return-value]

    def get_record(self, record_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
        return dict(row) if row else None

    def update_record(self, record_id: int, fields: dict) -> dict | None:
        allowed = {"transcript"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if sets:
            clause = ", ".join(f"{k} = ?" for k in sets)
            with self._lock, self._conn() as c:
                c.execute(f"UPDATE records SET {clause} WHERE id = ?", (*sets.values(), record_id))
        return self.get_record(record_id)

    def list_records(self, user_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM records WHERE user_id = ? ORDER BY date DESC, id DESC", (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def distinct_record_days(self, user_id: str, upto_date: str) -> int:
        with self._conn() as c:
            row = c.execute(
                "SELECT COUNT(DISTINCT date) AS n FROM records WHERE user_id = ? AND date <= ?",
                (user_id, upto_date),
            ).fetchone()
        return int(row["n"])

    def previous_record(self, user_id: str, before_id: int) -> dict | None:
        """直前の「日」の記録（同じ日の別記録は前回扱いしない）。"""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM records WHERE user_id = ?"
                " AND date < (SELECT date FROM records WHERE id = ?)"
                " ORDER BY date DESC, id DESC LIMIT 1",
                (user_id, before_id),
            ).fetchone()
        return dict(row) if row else None

    def weekly_summary(self, user_id: str, week_dates: list[str]) -> dict:
        if not week_dates:
            return {"days_recorded": 0, "total_records": 0}
        placeholders = ",".join("?" for _ in week_dates)
        with self._conn() as c:
            row = c.execute(
                f"SELECT COUNT(DISTINCT date) AS days, COUNT(*) AS total FROM records"
                f" WHERE user_id = ? AND date IN ({placeholders})",
                (user_id, *week_dates),
            ).fetchone()
        return {"days_recorded": int(row["days"]), "total_records": int(row["total"])}

    # ---- analyses ------------------------------------------------------
    def save_analysis(self, ana: dict) -> dict:
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO analyses (record_id, user_id, date, todays_word, observations, engine,"
                " baseline_day, request_json, response_json, created_at, kind, notice, fallback_reason)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ana["record_id"], ana["user_id"], ana["date"], ana.get("todays_word") or "",
                 json.dumps(ana.get("observations", []), ensure_ascii=False), ana["engine"],
                 ana["baseline_day"], json.dumps(ana.get("request", {}), ensure_ascii=False),
                 json.dumps(ana.get("response", {}), ensure_ascii=False), _now(),
                 ana.get("kind", "word"), ana.get("notice", ""), ana.get("fallback_reason", "")),
            )
            row = c.execute("SELECT * FROM analyses WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _analysis_row(row)

    def latest_analysis(self, user_id: str) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM analyses WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
            ).fetchone()
        return _analysis_row(row) if row else None

    def analysis_for_record(self, record_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM analyses WHERE record_id = ? ORDER BY id DESC LIMIT 1", (record_id,)
            ).fetchone()
        return _analysis_row(row) if row else None

    # ---- shares / stamps -------------------------------------------------
    def create_share(self, share: dict) -> dict:
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO shares (from_user, to_user, record_id, draft_text, final_text, status, created_at, sent_at)"
                " VALUES (?, ?, ?, ?, ?, 'draft', ?, NULL)",
                (share["from_user"], share["to_user"], share.get("record_id"),
                 share.get("draft_text", ""), share.get("draft_text", ""), _now()),
            )
            row = c.execute("SELECT * FROM shares WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def get_share(self, share_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM shares WHERE id = ?", (share_id,)).fetchone()
        return dict(row) if row else None

    def update_share(self, share_id: int, fields: dict) -> dict | None:
        allowed = {"final_text", "status", "sent_at"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return self.get_share(share_id)
        clause = ", ".join(f"{k} = ?" for k in sets)
        with self._lock, self._conn() as c:
            c.execute(f"UPDATE shares SET {clause} WHERE id = ?", (*sets.values(), share_id))
        return self.get_share(share_id)

    def _letters(self, where: str, user_id: str) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                f"SELECT * FROM shares WHERE {where} = ? AND status = 'sent' ORDER BY id DESC",
                (user_id,),
            ).fetchall()
        letters = []
        for r in rows:
            letter = dict(r)
            letter["stamps"] = self.list_stamps(letter["id"])
            letters.append(letter)
        return letters

    def list_letters_for(self, user_id: str) -> list[dict]:
        return self._letters("to_user", user_id)

    def list_letters_sent_by(self, user_id: str) -> list[dict]:
        return self._letters("from_user", user_id)

    def add_stamp(self, share_id: int, from_user: str, stamp: str) -> dict:
        with self._lock, self._conn() as c:
            cur = c.execute(
                "INSERT INTO stamps (share_id, from_user, stamp, created_at) VALUES (?, ?, ?, ?)",
                (share_id, from_user, stamp, _now()),
            )
            row = c.execute("SELECT * FROM stamps WHERE id = ?", (cur.lastrowid,)).fetchone()
        return dict(row)

    def list_stamps(self, share_id: int) -> list[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM stamps WHERE share_id = ? ORDER BY id ASC", (share_id,)
            ).fetchall()
        return [dict(r) for r in rows]


def create_store() -> Store:
    """DB_BACKEND=mysql なら MySQLStore（MYSQL_* で接続）、それ以外は SQLiteStore（APP_DB_PATH）。"""
    if os.environ.get("DB_BACKEND", "").strip().lower() == "mysql":
        from .storage_mysql import MySQLStore  # PyMySQL はこのときだけ import する
        return MySQLStore()
    return SQLiteStore()


def _analysis_row(row) -> dict:
    """sqlite3.Row / dict（MySQL の DictCursor）のどちらでも受け付ける。"""
    d = dict(row)
    d["observations"] = json.loads(d.get("observations") or "[]")
    d["request"] = json.loads(d.pop("request_json") or "{}")
    d["response"] = json.loads(d.pop("response_json") or "{}")
    d.setdefault("kind", "word")
    d.setdefault("notice", "")
    d.setdefault("fallback_reason", "")
    if d["kind"] != "word":
        # 案内文は「今日の一言」ではないので、区別できるよう todays_word は null にする
        d["todays_word"] = None
    return d
