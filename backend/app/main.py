"""きょうのかがみ — backend API.

Run:  uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import analysis as analysis_mod
from . import auth
from . import uploads
from .storage import AlreadySentError, DuplicateError, Store, create_store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.environ.get("APP_UPLOAD_DIR") or os.path.join(BASE_DIR, "..", "data", "uploads")
# UPLOAD_BACKEND=gcs なら Cloud Storage（GCS_UPLOAD_BUCKET）、それ以外は UPLOAD_DIR（uploads.create_upload_storage）
upload_storage: uploads.UploadStorage = uploads.create_upload_storage(UPLOAD_DIR)

JST = ZoneInfo("Asia/Tokyo")

# DB_BACKEND=mysql なら Cloud SQL（MySQL）、それ以外は SQLite（storage.create_store）
store: Store = create_store()
# 旧版（固定ユーザー mother / daughter）のデータがあれば、デモアカウントに引き継ぐ
auth.migrate_legacy_users(store)

app = FastAPI(title="きょうのかがみ API", version="0.01")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# /uploads/<名前>：ローカルは StaticFiles、GCS はバックエンドが読んで返すプロキシ（どちらも認証なし・uuid 名）
upload_storage.mount(app)

# 届いた便りへのリアクション（付け外しできる）。旧版のスタンプはそのままリアクションの種類として統合し、
# 先頭に「いいね」を足した。保存先は従来どおり stamps 表（旧データのスタンプもリアクションとして見える）
STAMPS = ["いいね", "ありがとう", "うれしい", "元気そうで安心", "電話するね", "また聞かせて"]
REPLY_MAX_CHARS = 200

# 便りに添える本人のコメント（任意。空でも送れる）の上限。返信と同じ長さにそろえる
COMMENT_MAX_CHARS = 200

# H-6: MIME 許可リストから拡張子を決める（利用者のファイル名は使わない。定義は uploads.py）
PHOTO_MIMES = uploads.PHOTO_MIMES
AUDIO_MIMES = uploads.AUDIO_MIMES
EXT_TO_AUDIO_MIME = {ext: mime for mime, ext in AUDIO_MIMES.items()}
EXT_TO_PHOTO_MIME = {ext: mime for mime, ext in PHOTO_MIMES.items()}
MAX_BYTES = {"photo": 10 * 1024 * 1024, "audio": 5 * 1024 * 1024}


def today_jst() -> date:
    """H-7: 日付は常にサーバー側で JST から決める。"""
    return datetime.now(JST).date()


# 本番向けガード（#34）
REGISTRATION_CLOSED_MESSAGE = "現在、新規登録は受け付けていません。デモアカウントでログインしてください"
DEFAULT_GEMINI_DAILY_LIMIT_PER_USER = 3
DEFAULT_GEMINI_DAILY_LIMIT_TOTAL = 100
_FALSY = {"0", "false", "no", "off"}


def registration_enabled() -> bool:
    """REGISTRATION_ENABLED が 0 / false / no / off なら新規登録を止める（未設定・それ以外は有効）。"""
    return os.environ.get("REGISTRATION_ENABLED", "").strip().lower() not in _FALSY


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip())
    except ValueError:
        return default


def gemini_daily_limits() -> tuple[int, int]:
    """(1ユーザーあたり, 全体) の1日の実 Gemini 呼び出し上限。環境変数で上書きできる。"""
    return (_env_int("GEMINI_DAILY_LIMIT_PER_USER", DEFAULT_GEMINI_DAILY_LIMIT_PER_USER),
            _env_int("GEMINI_DAILY_LIMIT_TOTAL", DEFAULT_GEMINI_DAILY_LIMIT_TOTAL))


# --------------------------------------------------------------------------
# 認証：Authorization: Bearer <token>。health と登録・ログイン以外の /api はすべてこれを通す
# --------------------------------------------------------------------------

def _bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def _unauthorized() -> HTTPException:
    return HTTPException(401, "ログインしてください", headers={"WWW-Authenticate": "Bearer"})


def current_user(authorization: str | None = Header(None)) -> dict:
    token = _bearer(authorization)
    if not token:
        raise _unauthorized()
    th = auth.token_hash(token)
    session = store.get_session(th)
    if not session:
        raise _unauthorized()
    expires = _parse_ts(session.get("expires_at"))
    if not expires or expires <= datetime.now(timezone.utc):
        store.delete_session(th)
        raise _unauthorized()
    user = store.get_user(session["user_id"])
    if not user:
        raise _unauthorized()
    return user


def _uid(user: dict = Depends(current_user)) -> str:
    return user["id"]


def _display_name(user_id: str) -> str:
    user = store.get_user(user_id)
    return user["display_name"] if user else "（退会したアカウント）"


def _owned_record(record_id: int, user_id: str) -> dict:
    rec = store.get_record(record_id)
    if not rec:
        raise HTTPException(404, "record not found")
    if rec["user_id"] != user_id:
        raise HTTPException(403, "自分の記録だけを見られます")
    return rec


def _public_record(rec: dict) -> dict:
    """M-7: サーバーの実パスは返さず、公開URLだけを返す。"""
    rec = dict(rec)
    for key in ("audio_path", "photo_path"):
        path = rec.pop(key, None)
        rec[key.replace("_path", "_url")] = uploads.public_url(path)
    return rec


def _base_mime(content_type: str | None) -> str:
    return (content_type or "").split(";")[0].strip().lower()


async def _read_upload(kind: str, upload: UploadFile) -> tuple[bytes, str, str]:
    allowed = PHOTO_MIMES if kind == "photo" else AUDIO_MIMES
    mime = _base_mime(upload.content_type)
    if mime not in allowed:
        raise HTTPException(415, f"{kind}: 対応していない形式です（{mime or '不明'}）。"
                                 f"使える形式: {', '.join(allowed)}")
    limit = MAX_BYTES[kind]
    data = await upload.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, f"{kind}: ファイルが大きすぎます（上限 {limit // (1024 * 1024)}MB）")
    if not data:
        raise HTTPException(400, f"{kind}: ファイルが空です")
    return data, allowed[mime], mime


def _remove_file(path: str | None) -> None:
    """保存先（ローカルの UPLOAD_DIR 配下／自分のバケット）のファイルだけを消す。"""
    upload_storage.delete(path)


# --------------------------------------------------------------------------
# Basic
# --------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "app": "きょうのかがみ", "version": "0.01", "today": today_jst().isoformat(),
            "registration_enabled": registration_enabled()}


@app.get("/api/stamps")
def stamps(_: str = Depends(_uid)):
    return STAMPS


# --------------------------------------------------------------------------
# Accounts（登録・ログイン・ログアウト・自分の情報）
# --------------------------------------------------------------------------

class RegisterIn(BaseModel):
    display_name: str
    email: str
    password: str


class LoginIn(BaseModel):
    email: str
    password: str


def _public_user(user: dict) -> dict:
    """自分自身に返す情報。password_hash はそもそも Store から出てこないが、念のため項目を限定する。"""
    return {k: user[k] for k in ("id", "email", "display_name", "family_code")}


def _start_session(user: dict) -> dict:
    token = auth.new_token()
    store.create_session(auth.token_hash(token), user["id"], auth.session_expiry())
    # トークンを返すのは発行時のこの1回だけ（サーバーにはハッシュしか残らない）
    return {"token": token, "user": _public_user(user)}


@app.post("/api/auth/register")
def register(body: RegisterIn):
    if not registration_enabled():
        raise HTTPException(403, REGISTRATION_CLOSED_MESSAGE)
    problem = auth.validate_registration(body.display_name, body.email, body.password)
    if problem:
        raise HTTPException(400, problem)
    try:
        user = auth.create_account(store, body.display_name, body.email, body.password)
    except DuplicateError:
        raise HTTPException(409, "このメールアドレスは登録済みです。ログインしてください") from None
    return _start_session(user)


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = store.find_user_by_email(auth.normalize_email(body.email))
    if not user:
        auth.burn_time(body.password)
        raise HTTPException(401, "メールアドレスかパスワードが違います")
    if not auth.verify_password(body.password, store.get_password_hash(user["id"])):
        raise HTTPException(401, "メールアドレスかパスワードが違います")
    return _start_session(user)


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(None), _: dict = Depends(current_user)):
    store.delete_session(auth.token_hash(_bearer(authorization) or ""))
    return {"ok": True}


@app.get("/api/me")
def me(user: dict = Depends(current_user)):
    return {"user": _public_user(user), "family": store.list_family(user["id"])}


# --------------------------------------------------------------------------
# Family（家族コードで連携。共有・便り・スタンプは連携済みの相手とだけ）
# --------------------------------------------------------------------------

class LinkIn(BaseModel):
    code: str


@app.post("/api/family/link")
def link_family(body: LinkIn, user: dict = Depends(current_user)):
    code = auth.normalize_family_code(body.code)
    other = store.find_user_by_family_code(code) if code else None
    if not other:
        raise HTTPException(404, "その家族コードは見つかりませんでした。もう一度確かめてください")
    if other["id"] == user["id"]:
        raise HTTPException(400, "自分の家族コードです。家族のコードを入れてください")
    added = store.add_family_link(user["id"], other["id"])
    return {"linked": {"id": other["id"], "display_name": other["display_name"]},
            "already_linked": not added, "family": store.list_family(user["id"])}


@app.delete("/api/family/links/{other_id}")
def unlink_family(other_id: str, uid: str = Depends(_uid)):
    if not store.remove_family_link(uid, other_id):
        raise HTTPException(404, "連携していない相手です")
    return {"family": store.list_family(uid)}


# --------------------------------------------------------------------------
# Records (M1)
# --------------------------------------------------------------------------

@app.post("/api/records")
async def create_record(
    user_id: str = Depends(_uid),
    gratitude: str = Form(""),
    mood: str = Form(""),
    transcript: str = Form(""),
    audio: UploadFile | None = File(None),
    photo: UploadFile | None = File(None),
):
    """1ユーザー1日1件（M-2）。同じ日の2回目は上書きする。日付はサーバーで決める（H-7）。"""
    # 先に全部検証してから書き込む（片方だけ保存される事故を防ぐ）
    pending: dict[str, tuple[bytes, str, str]] = {}
    for kind, upload in (("audio", audio), ("photo", photo)):
        if upload is not None and (upload.filename or upload.size):
            pending[kind] = await _read_upload(kind, upload)

    today = today_jst().isoformat()
    # 同じ日の上書き（M-2）では、新しく付けなかった声・写真（と声の文字起こし）は残す
    prev = store.find_record(user_id, today)
    if not gratitude.strip() and not mood.strip() and not transcript.strip() \
            and "audio" not in pending and not (prev and prev.get("audio_path")):
        raise HTTPException(400, "質問への答えを、声か文字で残してください。")

    saved: dict[str, str | None] = {
        "audio_path": prev.get("audio_path") if prev else None,
        "photo_path": prev.get("photo_path") if prev else None,
    }
    new_transcript = transcript.strip()
    if "audio" not in pending and prev and not new_transcript:
        new_transcript = prev.get("transcript") or ""
    written: list[str] = []
    for kind, (data, ext, mime) in pending.items():
        try:
            ref = upload_storage.save(uploads.new_name(ext), data, mime)
        except Exception as e:  # noqa: BLE001
            if upload_storage.backend != "gcs":
                raise  # ローカルディスクの失敗は従来どおり
            # GCS の失敗：途中まで書いたものを片付けて 503（記録は保存しない）
            for w in written:
                upload_storage.delete(w)
            uploads.log.warning("upload save failed: %s", type(e).__name__)
            raise HTTPException(503, "写真・声を保存できませんでした。少し時間をおいて、もう一度保存してください") from None
        written.append(ref)
        saved[f"{kind}_path"] = ref

    rec, old = store.upsert_record({
        "user_id": user_id, "date": today, "gratitude": gratitude.strip(),
        "mood": mood.strip(), "transcript": new_transcript,
        "audio_path": saved["audio_path"], "photo_path": saved["photo_path"],
    })
    if old:
        # 旧形式（H-6 修正前）のファイル名は同じ日の複数記録で共有されていることがあるので、
        # 他の記録から参照されていないときだけ消す。写真つきで送った便りが参照している写真も残す
        in_use = {r.get(k) for r in store.list_records(user_id) for k in ("audio_path", "photo_path")}
        in_use |= {s.get("photo_path") for s in store.list_letters_sent_by(user_id)}
        for key in ("audio_path", "photo_path"):
            if old.get(key) and old[key] != rec.get(key) and old[key] not in in_use:
                _remove_file(old[key])
    out = _public_record(rec)
    out["replaced"] = old is not None
    return out


def _parse_ts(value: str | None) -> datetime | None:
    try:
        ts = datetime.fromisoformat(value or "")
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _current_analysis(rec: dict) -> dict | None:
    """記録の内容に対応する解析。上書き（M-2）後にまだ解析し直していなければ None。

    上書き時は records.created_at が保存時刻に更新されるので、それより古い解析は前の内容のもの。
    """
    ana = store.analysis_for_record(rec["id"])
    if not ana:
        return None
    rec_ts, ana_ts = _parse_ts(rec.get("created_at")), _parse_ts(ana.get("created_at"))
    if rec_ts and ana_ts and ana_ts < rec_ts:
        return None
    return ana


def _list_word(rec: dict) -> tuple[str | None, str | None]:
    ana = _current_analysis(rec)
    if not ana:
        return None, None
    return ana["todays_word"], ana.get("kind")


@app.get("/api/records")
def list_records(user_id: str = Depends(_uid)):
    out = []
    for rec in store.list_records(user_id):
        rec = _public_record(rec)
        rec["todays_word"], rec["word_kind"] = _list_word(rec)
        out.append(rec)
    return out


@app.get("/api/records/{record_id}")
def get_record(record_id: int, user_id: str = Depends(_uid)):
    return _public_record(_owned_record(record_id, user_id))


# --------------------------------------------------------------------------
# Analysis (M2)
# --------------------------------------------------------------------------

def _load_media(path: str | None, ext_to_mime: dict[str, str]) -> tuple[bytes, str] | None:
    """記録の音声・写真の中身と MIME（拡張子から決める）。無い・読めないときは None。"""
    mime = ext_to_mime.get(os.path.splitext(path)[1].lower()) if path else None
    if not mime:
        return None
    data = upload_storage.read(path)
    return (data, mime) if data is not None else None


def input_fingerprint(rec: dict) -> str:
    """解析の入力の同一性（2問の回答・文字起こし・声・写真）。同じなら同じ値になる。"""
    fields = [rec.get(k) or "" for k in ("gratitude", "mood", "transcript", "audio_path", "photo_path")]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False).encode("utf-8")).hexdigest()


def _gemini_answered(ana: dict) -> bool:
    """前回の解析で実 Gemini が応答していたか（ガードで差し替えた場合も含む）。
    Gemini エラー・回数上限・キー未設定でモックになったものは、もう一度 Gemini を試せるよう再利用しない。"""
    reason = ana.get("fallback_reason") or ""
    return reason == "" or reason.startswith("guard_")


def _reuse_analysis(rec: dict, fingerprint: str) -> dict | None:
    """内容が変わっていない記録の再解析（#34）。前回の結果を写して保存し直す（実 Gemini は呼ばない）。

    保存し直すのは、上書き保存（M-2）で records.created_at が新しくなると前回の解析が「古い」扱いになるため。"""
    prev = store.analysis_for_record(rec["id"])
    if not prev or prev.get("request", {}).get("input_fingerprint") != fingerprint or not _gemini_answered(prev):
        return None
    response = {**prev["response"], "reused": {
        "reason": "unchanged_input", "from_analysis_id": prev["id"], "original_created_at": prev["created_at"],
    }}
    return store.save_analysis({
        "record_id": rec["id"], "user_id": rec["user_id"], "date": rec["date"],
        "todays_word": prev["todays_word"], "observations": prev["observations"],
        "engine": prev["engine"], "baseline_day": prev["baseline_day"],
        "request": prev["request"], "response": response,
        "kind": prev["kind"], "notice": prev["notice"], "fallback_reason": prev["fallback_reason"],
    })


def _gemini_quota(user_id: str):
    """analysis.analyze に渡す回数ガード。実 Gemini を呼ぶ直前に1回だけ呼ばれる（JST の今日で数える）。"""
    def consume() -> dict | None:
        day = today_jst().isoformat()
        per_user, total = gemini_daily_limits()
        scope = store.consume_gemini_quota(day, user_id, per_user, total)
        if scope is None:
            return None
        return {"scope": scope, "day": day, "limit": per_user if scope == "user" else total}
    return consume


def run_analysis(rec: dict) -> dict:
    """記録1件を解析して保存する（API と scripts/seed_demo.py の共通処理）。"""
    user_id = rec["user_id"]
    gemini_on = bool(os.environ.get("GEMINI_API_KEY", "").strip())
    if gemini_on:
        reused = _reuse_analysis(rec, input_fingerprint(rec))
        if reused:
            return reused
    baseline_day = store.distinct_record_days(user_id, rec["date"])
    previous = store.previous_record(user_id, rec["id"])
    history = [r for r in store.list_records(user_id) if r["date"] <= rec["date"]]
    audio = _load_media(rec.get("audio_path"), EXT_TO_AUDIO_MIME)
    photo = _load_media(rec.get("photo_path"), EXT_TO_PHOTO_MIME)
    result = analysis_mod.analyze(rec, baseline_day, previous, history, audio, photo,
                                  quota=_gemini_quota(user_id))
    if result.get("transcript") and not (rec.get("transcript") or "").strip():
        rec = store.update_record(rec["id"], {"transcript": result["transcript"]}) or rec
    # 保存後の記録（文字起こしを書き込んだ後）の内容で同一性を残す。次の再解析はこれと比べる
    result["request"]["input_fingerprint"] = input_fingerprint(rec)
    return store.save_analysis({
        "record_id": rec["id"], "user_id": user_id, "date": rec["date"],
        "todays_word": result["todays_word"], "observations": result["observations"],
        "engine": result["engine"], "baseline_day": baseline_day,
        "request": result["request"], "response": result["response"],
        "kind": result["kind"], "notice": result["notice"],
        "fallback_reason": result["fallback_reason"],
    })


@app.post("/api/records/{record_id}/analyze")
def analyze_record(record_id: int, user_id: str = Depends(_uid)):
    return run_analysis(_owned_record(record_id, user_id))


@app.get("/api/today-word")
def today_word(user_id: str = Depends(_uid)):
    today = today_jst().isoformat()
    rec = store.find_record(user_id, today)
    ana = _current_analysis(rec) if rec else None
    if not ana:
        return {"todays_word": None, "kind": None, "date": today, "has_record": rec is not None,
                "record_id": rec["id"] if rec else None}
    return {**ana, "has_record": True}


@app.get("/api/dev/latest-analysis")
def dev_latest_analysis(user_id: str = Depends(_uid)):
    """For the 仕組みを見る (developer view) screen."""
    ana = store.latest_analysis(user_id)
    return {
        "analysis": ana,
        "gemini_configured": bool(os.environ.get("GEMINI_API_KEY", "").strip()),
        "gemini_model": analysis_mod.gemini_model(),
    }


# --------------------------------------------------------------------------
# Reflection （振り返り） — positive weekly counts, never streaks
# --------------------------------------------------------------------------

@app.get("/api/weekly")
def weekly(user_id: str = Depends(_uid)):
    today = today_jst()
    monday = today - timedelta(days=today.weekday())
    week_dates = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    summary = store.weekly_summary(user_id, week_dates)
    days = summary["days_recorded"]
    message = (
        f"今週は{days}日できました" if days > 0
        else "今週もここから。1分だけ、声か文字で残してみませんか"
    )
    return {
        "user_id": user_id,
        "week_start": week_dates[0],
        "week_end": week_dates[-1],
        **summary,
        "message": message,
    }


# --------------------------------------------------------------------------
# Sharing （家族への共有 / 家族の日記） — opt-in only
# 便りの中身は「その日の AI フィードバック（今日の一言＋観察）の引用」＋「本人のコメント」（#35）。
# 引用は送信時点のスナップショットで、だれも編集できない。本人が書けるのはコメントだけ。
# 生の記録（2問の回答・文字起こし）は便りに入れない。
# --------------------------------------------------------------------------

class ShareIn(BaseModel):
    record_id: int
    to_users: list[str] = []      # 届ける家族（連携中の相手。複数可。宛先ごとに別の便りになる）
    comment: str = ""             # 本人のコメント（任意）
    include_photo: bool = False   # 「写真も一緒に届ける」。既定は届けない（本人がオンにしたときだけ）


class StampIn(BaseModel):
    stamp: str


class ReactionIn(BaseModel):
    reaction: str


class ReplyIn(BaseModel):
    text: str


def _unique_stamps(stamps: list[dict]) -> list[dict]:
    """人×種類ごとに1つ（旧版の add_stamp で重なったものは最初の1つだけ残す）。"""
    seen: set[tuple[str, str]] = set()
    out = []
    for st in stamps:
        key = (st["from_user"], st["stamp"])
        if key not in seen:
            seen.add(key)
            out.append(st)
    return out


def _load_snapshot(raw: str | None) -> dict | None:
    if not raw:
        return None
    try:
        snap = json.loads(raw)
    except ValueError:
        return None
    return snap if isinstance(snap, dict) else None


def _with_names(share: dict) -> dict:
    """便り・リアクション・返信に表示名を付ける（フロントが相手の名前を出すため）。

    feedback は送信時点のフィードバックの引用（旧版の便りは null で、本文は final_text）。
    写真は保存先の参照（photo_path）を出さず、本人が「写真も一緒に届ける」を選んで送った便りだけ公開URLを付ける。
    """
    share = dict(share)
    share["from_name"] = _display_name(share["from_user"])
    share["to_name"] = _display_name(share["to_user"])
    share["feedback"] = _load_snapshot(share.pop("snapshot_json", None))
    share["comment"] = share.get("comment") or ""
    photo = share.pop("photo_path", None)
    share["photo_url"] = uploads.public_url(photo) if share.get("status") == "sent" and photo else None
    if "stamps" in share:
        share["stamps"] = [{**st, "from_name": _display_name(st["from_user"])}
                           for st in _unique_stamps(share["stamps"])]
    if "replies" in share:
        share["replies"] = [{**rp, "from_name": _display_name(rp["from_user"])} for rp in share["replies"]]
    return share


def _feedback_snapshot(rec: dict) -> dict | None:
    """その日（記録）の AI フィードバックの引用。今日の一言と観察を、そのままの文で写し取る。

    記録を上書きしてまだ解析し直していない日・一言の代わりに案内文が出た日（voice_notice）は None（共有できない）。
    回答・文字起こしそのものは入れない。
    """
    ana = _current_analysis(rec)
    if not ana or ana.get("kind") != "word" or not (ana.get("todays_word") or "").strip():
        return None
    return {
        "date": rec["date"],
        "todays_word": ana["todays_word"],
        "observations": [o for o in (ana.get("observations") or []) if isinstance(o, str) and o.strip()],
    }


NO_FEEDBACK_MESSAGE = "この日は、まだ鏡の言葉（今日の一言）がありません。今日の一言ができた日を選んでください"


@app.get("/api/shares/preview")
def share_preview(record_id: int, user_id: str = Depends(_uid)):
    """共有画面用：選んだ日（自分の記録）のフィードバックの引用と、届けられる家族（送信済みかどうか）。"""
    rec = _owned_record(record_id, user_id)
    feedback = _feedback_snapshot(rec)
    already = set(store.sent_recipients(user_id, rec["id"]))
    photo = rec.get("photo_path")
    return {
        "record_id": rec["id"],
        "date": rec["date"],
        "feedback": feedback,
        "unavailable": "" if feedback else NO_FEEDBACK_MESSAGE,
        "record_photo_url": uploads.public_url(photo) if photo else None,
        "recipients": [{**f, "already_sent": f["id"] in already} for f in store.list_family(user_id)],
    }


def _names(user_ids: list[str]) -> str:
    return "・".join(_display_name(u) for u in user_ids)


@app.post("/api/shares")
def send_letters(body: ShareIn, user_id: str = Depends(_uid)):
    """選んだ日のフィードバックの引用＋コメントを、選んだ家族それぞれに1通ずつ届ける。

    同じ日（記録）の便りは、同じ相手には1回だけ（取り消した便りは数えない）。1人でも送信済みなら、だれにも送らない。
    """
    rec = _owned_record(body.record_id, user_id)
    to_users = list(dict.fromkeys(u for u in body.to_users if u))
    family = {f["id"] for f in store.list_family(user_id)}
    if not family:
        raise HTTPException(400, "まだ家族と連携していません。設定で家族コードを入れると、便りを送れるようになります")
    if not to_users:
        raise HTTPException(400, "届ける家族を選んでください")
    if any(u not in family for u in to_users):
        raise HTTPException(403, "連携している家族にだけ送れます。設定で家族コードを入れて連携してください")
    comment = (body.comment or "").strip()
    if len(comment) > COMMENT_MAX_CHARS:
        raise HTTPException(400, f"コメントは{COMMENT_MAX_CHARS}字までです（いま{len(comment)}字）")
    feedback = _feedback_snapshot(rec)
    if feedback is None:
        raise HTTPException(400, NO_FEEDBACK_MESSAGE)
    # 写真は本人が「写真も一緒に届ける」を選んだときだけ、送った時点の記録の写真を便りに写し取る
    # （あとで同じ日の記録を撮り直しても、届いた便りの写真は送ったときのまま）
    photo = (rec.get("photo_path") or None) if body.include_photo else None
    try:
        letters = store.create_letters(user_id, rec["id"], to_users, {
            "snapshot_json": json.dumps(feedback, ensure_ascii=False),
            "comment": comment,
            "photo_path": photo,
        })
    except AlreadySentError as e:
        raise HTTPException(409, f"{_names(e.to_users)}には、この日の便りをもう送っています。"
                                 "送り直すときは、家族の日記で前の便りを取り消してから送ってください") from None
    return {"letters": [_with_names(l) for l in letters]}


@app.get("/api/letters")
def letters(user_id: str = Depends(_uid)):
    """received: 自分に届いた便り / sent: 自分が送った便りと、それへのリアクション・返信（H-3）。"""
    return {
        "received": [_with_names(s) for s in store.list_letters_for(user_id)],
        "sent": [_with_names(s) for s in store.list_letters_sent_by(user_id)],
    }


def _received_letter(share_id: int, user_id: str, what: str) -> dict:
    """リアクション・返信ができるのは、その便りを受け取った、いま連携中の相手だけ。"""
    share = store.get_share(share_id)
    if not share or share["status"] != "sent":
        raise HTTPException(404, "letter not found")
    if user_id != share["to_user"]:
        raise HTTPException(403, f"{what}できるのは、便りを受け取った人だけです")
    if not store.are_linked(user_id, share["from_user"]):
        raise HTTPException(403, "この相手とは連携が解除されています")
    return share


def _letter_view(share_id: int) -> dict:
    share = dict(store.get_share(share_id) or {})
    share["stamps"] = store.list_stamps(share_id)
    share["replies"] = store.list_replies(share_id)
    return _with_names(share)


@app.post("/api/letters/{share_id}/stamps")
def add_stamp(share_id: int, body: StampIn, user_id: str = Depends(_uid)):
    """旧版のスタンプ返信（付けるだけ）。いまはリアクションと同じもので、同じ種類は1つまで（付いていれば何もしない）。"""
    if body.stamp not in STAMPS:
        raise HTTPException(400, f"unknown stamp: {body.stamp}")
    _received_letter(share_id, user_id, "スタンプを返す")
    for st in store.list_stamps(share_id):
        if st["from_user"] == user_id and st["stamp"] == body.stamp:
            return st
    return store.add_stamp(share_id, user_id, body.stamp)


@app.post("/api/letters/{share_id}/reactions")
def toggle_reaction(share_id: int, body: ReactionIn, user_id: str = Depends(_uid)):
    """リアクションの付け外し（トグル）。付いていなければ付け、付いていれば外す。"""
    if body.reaction not in STAMPS:
        raise HTTPException(400, f"unknown reaction: {body.reaction}")
    _received_letter(share_id, user_id, "リアクション")
    on = store.toggle_stamp(share_id, user_id, body.reaction)
    return {"reaction": body.reaction, "on": on, "letter": _letter_view(share_id)}


@app.post("/api/letters/{share_id}/replies")
def add_reply(share_id: int, body: ReplyIn, user_id: str = Depends(_uid)):
    _received_letter(share_id, user_id, "返信")
    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "返信が空です。ひとこと書いてから送ってください")
    if len(text) > REPLY_MAX_CHARS:
        raise HTTPException(400, f"返信は{REPLY_MAX_CHARS}字までです（いま{len(text)}字）")
    reply = store.add_reply(share_id, user_id, text)
    return {"reply": {**reply, "from_name": _display_name(user_id)}, "letter": _letter_view(share_id)}


@app.delete("/api/letters/{share_id}")
def retract_letter(share_id: int, user_id: str = Depends(_uid)):
    """送信の取り消し：送った本人だけが、送信済みの便りを消せる。家族の画面からも消え、
    付いたリアクション・返信も一緒に消える。連携を解除したあとでも取り消せる。"""
    share = store.get_share(share_id)
    if not share or share["status"] != "sent":
        raise HTTPException(404, "letter not found")
    if share["from_user"] != user_id:
        raise HTTPException(403, "便りを取り消せるのは、送った本人だけです")
    store.delete_share(share_id)
    # 便りが写し取っていた写真は、記録・ほかの便りから参照されていなければ片付ける
    photo = share.get("photo_path")
    if photo:
        in_use = {r.get("photo_path") for r in store.list_records(user_id)}
        in_use |= {s.get("photo_path") for s in store.list_letters_sent_by(user_id)}
        if photo not in in_use:
            _remove_file(photo)
    return {"ok": True, "deleted": share_id}


@app.delete("/api/letters/{share_id}/replies/{reply_id}")
def delete_reply(share_id: int, reply_id: int, user_id: str = Depends(_uid)):
    """自分の返信だけを削除できる（相手の返信は消せない）。連携を解除したあとでも消せる。"""
    reply = store.get_reply(reply_id)
    if not reply or reply["share_id"] != share_id:
        raise HTTPException(404, "reply not found")
    if reply["from_user"] != user_id:
        raise HTTPException(403, "削除できるのは、自分の返信だけです")
    store.delete_reply(reply_id)
    return {"ok": True, "deleted": reply_id, "letter": _letter_view(share_id)}
