"""きょうのかがみ — backend API.

Run:  uvicorn app.main:app --reload --port 8000
"""
from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analysis as analysis_mod
from . import auth
from .storage import DuplicateError, SQLiteStore, Store

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.environ.get("APP_UPLOAD_DIR") or os.path.join(BASE_DIR, "..", "data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

JST = ZoneInfo("Asia/Tokyo")

store: Store = SQLiteStore()
# 旧版（固定ユーザー mother / daughter）のデータがあれば、デモアカウントに引き継ぐ
auth.migrate_legacy_users(store)

app = FastAPI(title="きょうのかがみ API", version="0.01")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

STAMPS = ["ありがとう", "うれしい", "元気そうで安心", "電話するね", "また聞かせて"]

PLACEHOLDER_DRAFT = "（AI下書きはまだありません。記録すると、ここに下書きができます）"

# H-6: MIME 許可リストから拡張子を決める（利用者のファイル名は使わない）
PHOTO_MIMES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
AUDIO_MIMES = {"audio/webm": ".webm", "audio/mp4": ".m4a", "audio/ogg": ".ogg"}
EXT_TO_AUDIO_MIME = {ext: mime for mime, ext in AUDIO_MIMES.items()}
MAX_BYTES = {"photo": 10 * 1024 * 1024, "audio": 5 * 1024 * 1024}


def today_jst() -> date:
    """H-7: 日付は常にサーバー側で JST から決める。"""
    return datetime.now(JST).date()


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
        rec[key.replace("_path", "_url")] = f"/uploads/{os.path.basename(path)}" if path else None
    return rec


def _base_mime(content_type: str | None) -> str:
    return (content_type or "").split(";")[0].strip().lower()


async def _read_upload(kind: str, upload: UploadFile) -> tuple[bytes, str]:
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
    return data, allowed[mime]


def _remove_file(path: str | None) -> None:
    if not path:
        return
    try:
        real = os.path.realpath(path)
        if real.startswith(os.path.realpath(UPLOAD_DIR) + os.sep) and os.path.isfile(real):
            os.remove(real)
    except OSError:
        pass


# --------------------------------------------------------------------------
# Basic
# --------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "app": "きょうのかがみ", "version": "0.01", "today": today_jst().isoformat()}


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
    pending: dict[str, tuple[bytes, str]] = {}
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
    for kind, (data, ext) in pending.items():
        path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}{ext}")
        with open(path, "wb") as f:
            f.write(data)
        saved[f"{kind}_path"] = path

    rec, old = store.upsert_record({
        "user_id": user_id, "date": today, "gratitude": gratitude.strip(),
        "mood": mood.strip(), "transcript": new_transcript,
        "audio_path": saved["audio_path"], "photo_path": saved["photo_path"],
    })
    if old:
        # 旧形式（H-6 修正前）のファイル名は同じ日の複数記録で共有されていることがあるので、
        # 他の記録から参照されていないときだけ消す
        in_use = {r.get(k) for r in store.list_records(user_id) for k in ("audio_path", "photo_path")}
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

def run_analysis(rec: dict) -> dict:
    """記録1件を解析して保存する（API と scripts/seed_demo.py の共通処理）。"""
    user_id = rec["user_id"]
    baseline_day = store.distinct_record_days(user_id, rec["date"])
    previous = store.previous_record(user_id, rec["id"])
    history = [r for r in store.list_records(user_id) if r["date"] <= rec["date"]]
    audio = None
    path = rec.get("audio_path")
    if path and os.path.isfile(path):
        mime = EXT_TO_AUDIO_MIME.get(os.path.splitext(path)[1].lower())
        if mime:
            with open(path, "rb") as f:
                audio = (f.read(), mime)
    result = analysis_mod.analyze(rec, baseline_day, previous, history, audio)
    if result.get("transcript") and not (rec.get("transcript") or "").strip():
        store.update_record(rec["id"], {"transcript": result["transcript"]})
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
# Sharing （共有の確認 / 届いた便り） — opt-in only, editable draft, stamps
# --------------------------------------------------------------------------

class DraftIn(BaseModel):
    record_id: int | None = None
    to_user: str | None = None   # 連携相手が1人なら省略可


class EditIn(BaseModel):
    final_text: str


class SendIn(BaseModel):
    final_text: str | None = None


class StampIn(BaseModel):
    stamp: str


def _with_names(share: dict) -> dict:
    """便り・スタンプに表示名を付ける（フロントが相手の名前を出すため）。"""
    share = dict(share)
    share["from_name"] = _display_name(share["from_user"])
    share["to_name"] = _display_name(share["to_user"])
    if "stamps" in share:
        share["stamps"] = [{**st, "from_name": _display_name(st["from_user"])} for st in share["stamps"]]
    return share


def _pick_recipient(user_id: str, to_user: str | None) -> str:
    family = [f["id"] for f in store.list_family(user_id)]
    if to_user:
        if to_user not in family:
            raise HTTPException(403, "連携している家族にだけ送れます。設定で家族コードを入れて連携してください")
        return to_user
    if not family:
        raise HTTPException(400, "まだ家族と連携していません。設定で家族コードを入れると、便りを送れるようになります")
    if len(family) > 1:
        raise HTTPException(400, "送る相手を選んでください")
    return family[0]


def _owned_draft(share_id: int, user_id: str) -> dict:
    share = store.get_share(share_id)
    if not share:
        raise HTTPException(404, "share not found")
    if share["from_user"] != user_id:
        raise HTTPException(403, "この下書きを編集・送信できるのは、書いた本人だけです")
    if share["status"] != "draft":
        raise HTTPException(409, "この便りはもう送信済みのため、変更できません")
    return share


def _is_sendable(text: str) -> bool:
    t = (text or "").strip()
    return bool(t) and t != PLACEHOLDER_DRAFT and not t.startswith("（AI下書きはまだありません")


@app.post("/api/shares/draft")
def create_draft(body: DraftIn, user_id: str = Depends(_uid)):
    ana = None
    if body.record_id:
        rec = store.get_record(body.record_id)
        if rec and rec["user_id"] != user_id:
            raise HTTPException(403, "自分の記録だけを共有できます")
        ana = store.analysis_for_record(body.record_id)
    to_user = _pick_recipient(user_id, body.to_user)
    if ana is None:
        ana = store.latest_analysis(user_id)
    word = ana["todays_word"] if ana else None
    draft = f"今日の一言：{word}" if word else PLACEHOLDER_DRAFT
    share = store.create_share({
        "from_user": user_id,
        "to_user": to_user,
        "record_id": ana["record_id"] if ana else None,  # L-8: 実際に使った解析の記録IDに揃える
        "draft_text": draft,
    })
    return _with_names(share)


@app.patch("/api/shares/{share_id}")
def edit_share(share_id: int, body: EditIn, user_id: str = Depends(_uid)):
    _owned_draft(share_id, user_id)
    return _with_names(store.update_share(share_id, {"final_text": body.final_text}))


@app.post("/api/shares/{share_id}/send")
def send_share(share_id: int, body: SendIn, user_id: str = Depends(_uid)):
    share = _owned_draft(share_id, user_id)
    if not store.are_linked(user_id, share["to_user"]):
        raise HTTPException(403, "この相手とは連携が解除されています。設定で連携してから送ってください")
    text = body.final_text if body.final_text is not None else share["final_text"]
    if not _is_sendable(text):
        raise HTTPException(400, "送る内容が空か、下書きの案内文のままです。ひとこと書いてから送ってください。")
    return _with_names(store.update_share(share_id, {
        "final_text": text.strip(), "status": "sent",
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }))


@app.get("/api/letters")
def letters(user_id: str = Depends(_uid)):
    """received: 自分に届いた便り / sent: 自分が送った便りと、それへのスタンプ返信（H-3）。"""
    return {
        "received": [_with_names(s) for s in store.list_letters_for(user_id)],
        "sent": [_with_names(s) for s in store.list_letters_sent_by(user_id)],
    }


@app.post("/api/letters/{share_id}/stamps")
def add_stamp(share_id: int, body: StampIn, user_id: str = Depends(_uid)):
    if body.stamp not in STAMPS:
        raise HTTPException(400, f"unknown stamp: {body.stamp}")
    share = store.get_share(share_id)
    if not share or share["status"] != "sent":
        raise HTTPException(404, "letter not found")
    if user_id != share["to_user"]:
        raise HTTPException(403, "スタンプを返せるのは、便りを受け取った人だけです")
    if not store.are_linked(user_id, share["from_user"]):
        raise HTTPException(403, "この相手とは連携が解除されています")
    return store.add_stamp(share_id, user_id, body.stamp)
