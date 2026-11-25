"""アカウント・セッション・家族コード（標準ライブラリのみ）。

- パスワードは hashlib.pbkdf2_hmac（SHA-256、ユーザーごとのランダム salt）でハッシュ化して保存する。
  形式: "pbkdf2_sha256$<iterations>$<salt hex>$<hash hex>"（反復回数を変えても古いハッシュを検証できる）
- セッショントークンは secrets.token_urlsafe で作る不透明な文字列。サーバーには SHA-256 だけを保存する
  （DB が漏れても、そのままトークンとしては使えない）。
- 家族コードは紛らわしい文字（0/O, 1/I/L）を除いた 31 文字から 12 文字（約 59 bit）。
- パスワード・トークンはログに出さない・レスポンスに含めない（トークンは発行時の1回だけ返す）。
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from .storage import DuplicateError, Store

PBKDF2_ITERATIONS = 600_000   # OWASP（2023）の PBKDF2-HMAC-SHA256 推奨値
SALT_BYTES = 16
SESSION_DAYS = 30
PASSWORD_MIN = 8
PASSWORD_MAX = 256
DISPLAY_NAME_MAX = 30
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

FAMILY_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
FAMILY_CODE_LEN = 12

# デモアカウント。ID を旧固定ユーザー（mother / daughter）と同じにして、既存データをそのまま引き継ぐ
DEMO_ACCOUNTS = [
    {"id": "mother", "email": "haha@demo.local", "password": "demo-mama-2026", "display_name": "母さん"},
    {"id": "daughter", "email": "musume@demo.local", "password": "demo-musume-2026", "display_name": "娘さん"},
    {"id": "son", "email": "musuko@demo.local", "password": "demo-musuko-2026", "display_name": "息子さん"},
]


# ---- パスワード ------------------------------------------------------------

def hash_password(password: str, iterations: int | None = None) -> str:
    iterations = iterations or PBKDF2_ITERATIONS
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        algo, iters, salt_hex, hash_hex = (stored or "").split("$")
        if algo != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), hash_hex)


_DUMMY_HASH: str | None = None


def burn_time(password: str) -> None:
    """存在しないメールでも検証と同じだけ時間をかけ、応答時間で登録の有無が分からないようにする。"""
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password("dummy-password")
    verify_password(password, _DUMMY_HASH)


# ---- 入力チェック ------------------------------------------------------------

def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def validate_registration(display_name: str, email: str, password: str) -> str | None:
    """問題があれば利用者向けのメッセージを返す。"""
    name = (display_name or "").strip()
    if not name:
        return "表示名を入れてください"
    if len(name) > DISPLAY_NAME_MAX:
        return f"表示名は{DISPLAY_NAME_MAX}文字までです"
    if not EMAIL_RE.match(normalize_email(email)) or len(email) > 254:
        return "メールアドレスの形式を確かめてください"
    if len(password or "") < PASSWORD_MIN:
        return f"パスワードは{PASSWORD_MIN}文字以上にしてください"
    if len(password) > PASSWORD_MAX:
        return f"パスワードは{PASSWORD_MAX}文字までです"
    return None


# ---- トークン・家族コード ------------------------------------------------------

def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()


def new_family_code() -> str:
    raw = "".join(secrets.choice(FAMILY_CODE_ALPHABET) for _ in range(FAMILY_CODE_LEN))
    return "-".join(raw[i:i + 4] for i in range(0, FAMILY_CODE_LEN, 4))


def normalize_family_code(code: str) -> str:
    """入力ゆれ（小文字・空白・ハイフンの有無）を吸収して XXXX-XXXX-XXXX に揃える。"""
    raw = re.sub(r"[\s\-]", "", (code or "")).upper()
    if len(raw) != FAMILY_CODE_LEN:
        return ""
    return "-".join(raw[i:i + 4] for i in range(0, FAMILY_CODE_LEN, 4))


# ---- アカウント作成 ------------------------------------------------------------

def create_account(store: Store, display_name: str, email: str, password: str,
                   user_id: str | None = None) -> dict:
    """アカウントを作る。メールが使用済みなら DuplicateError。家族コードの衝突は作り直す。"""
    pw_hash = hash_password(password)
    for _ in range(5):
        try:
            return store.create_user({
                "id": user_id or secrets.token_hex(16),
                "email": normalize_email(email),
                "display_name": display_name.strip(),
                "password_hash": pw_hash,
                "family_code": new_family_code(),
            })
        except DuplicateError as e:
            if e.field != "family_code":
                raise
    raise RuntimeError("家族コードを発行できませんでした")


def ensure_demo_accounts(store: Store) -> list[dict]:
    """デモアカウント（母さん・娘さん・息子さん）を用意して、全員を互いに連携する。

    既にあれば作り直さない（パスワードも変えない）。連携も既にあればそのまま（冪等）。
    登録 API を通らないので REGISTRATION_ENABLED の影響は受けない。
    """
    users = []
    for acc in DEMO_ACCOUNTS:
        user = store.get_user(acc["id"])
        if user is None:
            user = create_account(store, acc["display_name"], acc["email"], acc["password"], user_id=acc["id"])
        users.append(user)
    for i, a in enumerate(users):
        for b in users[i + 1:]:
            store.add_family_link(a["id"], b["id"])
    return users


def migrate_legacy_users(store: Store) -> bool:
    """旧版（ログインなしの固定ユーザー mother / daughter）のデータがあれば、デモアカウントへ引き継ぐ。

    記録・共有の user_id はそのまま（デモアカウントの ID が mother / daughter）なので、
    行の書き換えはせずアカウントを作るだけで済む。旧データがない新しいDBでは何もしない。
    """
    legacy = [a["id"] for a in DEMO_ACCOUNTS if store.has_data_for(a["id"]) and store.get_user(a["id"]) is None]
    if not legacy:
        return False
    ensure_demo_accounts(store)
    return True
