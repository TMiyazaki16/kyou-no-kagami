"""バックエンド検証（FastAPI TestClient + unittest、追加ライブラリ不要）。

実行（backend ディレクトリで）:
  .venv/bin/python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest import mock

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="fudan_test_")
os.environ["APP_DB_PATH"] = os.path.join(_TMP, "import.db")
os.environ["APP_UPLOAD_DIR"] = os.path.join(_TMP, "uploads")
os.environ.pop("GEMINI_API_KEY", None)
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient  # noqa: E402

from app import analysis, auth, main  # noqa: E402
from app.storage import SQLiteStore  # noqa: E402

TODAY = date(2026, 11, 22)
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def fake_gemini(payload: dict):
    """_call_gemini の差し替え。呼び出し引数を記録し、指定の JSON を返す。"""
    calls: list = []

    def _call(api_key, model, parts, timeout=None):
        calls.append({"api_key": api_key, "model": model, "parts": parts})
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(payload, ensure_ascii=False)}]}}]}

    return _call, calls


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(dir=_TMP)
        main.store = SQLiteStore(os.path.join(self.dir, "app.db"))
        self.store = main.store
        self.today_patch = mock.patch.object(main, "today_jst", lambda: self.today)
        self.today = TODAY
        self.today_patch.start()
        self.env_patch = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patch.start()
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GEMINI_MODEL", None)
        os.environ.pop("GEMINI_FALLBACK_MODEL", None)
        # Gemini の再試行の待ちは実時間で眠らず、待った秒数だけ記録する
        self.sleeps: list = []
        self.sleep_patch = mock.patch.object(analysis, "_sleep", self.sleeps.append)
        self.sleep_patch.start()
        # テストではハッシュの反復回数を下げて速くする（既定値は TestAuth で確認）
        self.iter_patch = mock.patch.object(auth, "PBKDF2_ITERATIONS", 1000)
        self.iter_patch.start()
        self.anon = TestClient(main.app)
        # 母・娘のアカウントを登録して連携する。self.c = 母、self.d = 娘（認証済みクライアント）
        self.ids: dict[str, str] = {}
        self.clients: dict[str, TestClient] = {}
        for key, name in (("mother", "母"), ("daughter", "娘")):
            self.clients[key], self.ids[key] = self.register(name, f"{key}@example.com")
        self.c, self.d = self.clients["mother"], self.clients["daughter"]
        code = self.d.get("/api/me").json()["user"]["family_code"]
        self.assertEqual(self.c.post("/api/family/link", json={"code": code}).status_code, 200)

    def tearDown(self):
        self.today_patch.stop()
        self.env_patch.stop()
        self.iter_patch.stop()
        self.sleep_patch.stop()

    def register(self, name, email, password="password-123"):
        r = self.anon.post("/api/auth/register",
                           json={"display_name": name, "email": email, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        return self.client_for(body["token"]), body["user"]["id"]

    @staticmethod
    def client_for(token):
        return TestClient(main.app, headers={"Authorization": f"Bearer {token}"})

    def post_record(self, user="mother", gratitude="", mood="", files=None, **extra):
        data = {"gratitude": gratitude, "mood": mood, **extra}
        return self.clients[user].post("/api/records", data=data, files=files or {})

    def seed_days(self, user, entries, end=None):
        """entries[i] = (gratitude, mood)。end を最終日として過去にさかのぼって記録・解析。"""
        end = end or self.today
        start = end - timedelta(days=len(entries) - 1)
        out = []
        for i, (g, m) in enumerate(entries):
            rec, _ = self.store.upsert_record({
                "user_id": self.ids[user], "date": (start + timedelta(days=i)).isoformat(),
                "gratitude": g, "mood": m, "transcript": "", "audio_path": None, "photo_path": None,
            })
            out.append(main.run_analysis(rec))
        return out


# --------------------------------------------------------------------------
class TestRecordFlow(Base):
    def test_create_analyze_list(self):
        r = self.post_record(gratitude="娘が電話をくれたこと", mood="少し疲れたけど元気")
        self.assertEqual(r.status_code, 200, r.text)
        rec = r.json()
        self.assertEqual(rec["date"], "2026-11-22")
        self.assertNotIn("audio_path", rec)
        self.assertNotIn("photo_path", rec)

        a = self.c.post(f"/api/records/{rec['id']}/analyze").json()
        self.assertEqual(a["kind"], "word")
        self.assertIn("娘が電話をくれたこと", a["todays_word"])
        self.assertEqual(a["engine"], "mock")
        self.assertEqual(a["fallback_reason"], "no_api_key")
        self.assertTrue(any("覚えている期間" in o for o in a["observations"]))

        lst = self.c.get("/api/records").json()
        self.assertEqual(len(lst), 1)
        self.assertEqual(lst[0]["todays_word"], a["todays_word"])
        tw = self.c.get("/api/today-word").json()
        self.assertEqual(tw["todays_word"], a["todays_word"])

        dev = self.c.get("/api/dev/latest-analysis").json()
        self.assertEqual(dev["analysis"]["fallback_reason"], "no_api_key")
        self.assertFalse(dev["gemini_configured"])
        self.assertEqual(dev["gemini_model"], "gemini-3.5-flash-lite")

    def test_empty_record_rejected(self):
        self.assertEqual(self.post_record().status_code, 400)

    def test_upsert_one_record_per_day(self):
        first = self.post_record(gratitude="一回目", mood="ふつう").json()
        second = self.post_record(gratitude="二回目", mood="元気").json()
        self.assertEqual(first["id"], second["id"])
        self.assertTrue(second["replaced"])
        lst = self.c.get("/api/records").json()
        self.assertEqual(len(lst), 1)
        self.assertEqual(lst[0]["gratitude"], "二回目")
        # 別ユーザーは別記録
        self.post_record(user="daughter", gratitude="娘の記録")
        self.assertEqual(len(self.d.get("/api/records").json()), 1)

    def test_stale_word_hidden_after_overwrite(self):
        rec = self.post_record(gratitude="一回目のこと", mood="ふつう").json()
        self.c.post(f"/api/records/{rec['id']}/analyze")
        self.post_record(gratitude="二回目のこと", mood="元気")  # 上書きしたが、まだ解析していない
        tw = self.c.get("/api/today-word").json()
        self.assertIsNone(tw["kind"])
        self.assertTrue(tw["has_record"])
        self.assertIsNone(self.c.get("/api/records").json()[0]["todays_word"])
        self.c.post(f"/api/records/{rec['id']}/analyze")
        tw = self.c.get("/api/today-word").json()
        self.assertIn("二回目のこと", tw["todays_word"])

    def test_overwrite_keeps_legacy_shared_file(self):
        # 旧形式：同じ日の2記録が同じファイルを指している
        shared = os.path.join(main.UPLOAD_DIR, "mother_2026-11-22_audio.webm")
        with open(shared, "wb") as f:
            f.write(b"0" * 10)
        for _ in range(2):
            self.store.create_record({"user_id": self.ids["mother"], "date": "2026-11-22", "gratitude": "旧",
                                      "mood": "旧", "audio_path": shared})
        files = {"audio": ("memo.webm", b"\x1aE\xdf\xa3" + b"0" * 10, "audio/webm")}
        self.assertEqual(self.post_record(mood="新", files=files).status_code, 200)
        self.assertTrue(os.path.exists(shared))  # もう1件の旧記録がまだ参照している

    def test_voice_only_mock_returns_notice(self):
        files = {"audio": ("memo.webm", b"\x1aE\xdf\xa3" + b"0" * 100, "audio/webm")}
        rec = self.post_record(files=files).json()
        a = self.c.post(f"/api/records/{rec['id']}/analyze").json()
        self.assertEqual(a["kind"], "voice_notice")
        self.assertIsNone(a["todays_word"])
        self.assertEqual(a["notice"], analysis.VOICE_NOTICE)
        self.assertNotIn("—", json.dumps(a["observations"], ensure_ascii=False))
        lst = self.c.get("/api/records").json()
        self.assertIsNone(lst[0]["todays_word"])
        self.assertEqual(lst[0]["word_kind"], "voice_notice")
        # 文字を足して上書きしても、声の記録は残る
        rec2 = self.post_record(mood="元気です").json()
        self.assertIsNotNone(rec2["audio_url"])
        a2 = self.c.post(f"/api/records/{rec2['id']}/analyze").json()
        self.assertEqual(a2["kind"], "word")
        self.assertIn("元気です", a2["todays_word"])

    def test_weekly_zero_days_message(self):
        w = self.d.get("/api/weekly").json()
        self.assertEqual(w["days_recorded"], 0)
        self.assertNotIn("0日できました", w["message"])
        self.post_record(user="daughter", gratitude="夕焼け")
        w = self.d.get("/api/weekly").json()
        self.assertEqual(w["message"], "今週は1日できました")


# --------------------------------------------------------------------------
class TestTimezone(Base):
    def test_today_jst_uses_tokyo(self):
        self.today_patch.stop()  # 本物の today_jst を使う
        try:
            class FakeDT(datetime):
                @classmethod
                def now(cls, tz=None):
                    # UTC 11/21 23:30 = JST 11/22 08:30（JST 朝9時前）
                    return datetime(2026, 11, 21, 23, 30, tzinfo=timezone.utc).astimezone(tz)

            with mock.patch.object(main, "datetime", FakeDT):
                self.assertEqual(main.today_jst(), date(2026, 11, 22))
                # クライアントが date を送ってきても無視され、サーバーの JST 日付になる
                rec = self.post_record(gratitude="朝の散歩", date="2026-11-21").json()
                self.assertEqual(rec["date"], "2026-11-22")
                self.c.post(f"/api/records/{rec['id']}/analyze")
                tw = self.c.get("/api/today-word").json()
                self.assertIsNotNone(tw["todays_word"])
                w = self.c.get("/api/weekly").json()
                self.assertEqual(w["days_recorded"], 1)
                self.assertEqual(w["week_start"], "2026-11-16")  # 11/22(日) の週の月曜
        finally:
            self.today_patch.start()


# --------------------------------------------------------------------------
class TestUploads(Base):
    def test_mime_allowlist(self):
        for name, ctype in (("x.html", "text/html"), ("x.svg", "image/svg+xml"), ("x.gif", "image/gif")):
            r = self.post_record(gratitude="写真", files={"photo": (name, b"<svg></svg>", ctype)})
            self.assertEqual(r.status_code, 415, (name, r.text))
        r = self.post_record(gratitude="声", files={"audio": ("a.wav", b"RIFF0000", "audio/wav")})
        self.assertEqual(r.status_code, 415)
        self.assertEqual(self.c.get("/api/records").json(), [])

    def test_extension_from_mime_and_uuid_name(self):
        r = self.post_record(gratitude="写真", files={"photo": ("evil.html", PNG, "image/png")})
        self.assertEqual(r.status_code, 200, r.text)
        url = r.json()["photo_url"]
        name = url.rsplit("/", 1)[1]
        self.assertTrue(name.endswith(".png"))
        self.assertRegex(name, r"^[0-9a-f]{32}\.png$")
        self.assertNotIn(main.UPLOAD_DIR, json.dumps(r.json()))
        self.assertEqual(self.c.get(url).content, PNG)
        # Safari の録音（audio/mp4; codecs 付き）も受け付け、拡張子は MIME から決まる
        r = self.post_record(user="daughter", gratitude="声", files={
            "audio": ("memo.webm", b"0" * 50, "audio/mp4;codecs=mp4a.40.2")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(r.json()["audio_url"].endswith(".m4a"))

    def test_size_limits(self):
        big_photo = b"0" * (10 * 1024 * 1024 + 1)
        r = self.post_record(gratitude="写真", files={"photo": ("p.jpg", big_photo, "image/jpeg")})
        self.assertEqual(r.status_code, 413)
        big_audio = b"0" * (5 * 1024 * 1024 + 1)
        r = self.post_record(files={"audio": ("a.webm", big_audio, "audio/webm")})
        self.assertEqual(r.status_code, 413)
        ok_audio = b"0" * (5 * 1024 * 1024)
        r = self.post_record(files={"audio": ("a.webm", ok_audio, "audio/webm")})
        self.assertEqual(r.status_code, 200)

    def test_same_day_reupload_does_not_overwrite_other_files(self):
        r1 = self.post_record(gratitude="1", files={"photo": ("a.png", PNG, "image/png")}).json()
        r2 = self.post_record(gratitude="2", files={"photo": ("a.png", PNG + b"x", "image/png")}).json()
        self.assertNotEqual(r1["photo_url"], r2["photo_url"])
        old_file = os.path.join(main.UPLOAD_DIR, r1["photo_url"].rsplit("/", 1)[1])
        self.assertFalse(os.path.exists(old_file))  # 置きかえた古いファイルは片付ける


# --------------------------------------------------------------------------
class TestSharing(Base):
    def _draft(self, user="mother"):
        cl = self.clients[user]
        rec = self.post_record(user=user, gratitude="庭のバラ", mood="元気").json()
        cl.post(f"/api/records/{rec['id']}/analyze")
        return cl.post("/api/shares/draft", json={}).json()

    def test_draft_edit_send_lock(self):
        s = self._draft()
        self.assertEqual(s["status"], "draft")
        self.assertIn("今日の一言", s["draft_text"])
        self.assertEqual(s["to_user"], self.ids["daughter"])  # 連携相手が1人なら自動で宛先になる
        self.assertEqual(s["to_name"], "娘")
        # 他人（受け取る予定の娘でも）は編集・送信できない
        r = self.d.patch(f"/api/shares/{s['id']}", json={"final_text": "x"})
        self.assertEqual(r.status_code, 403)
        r = self.d.post(f"/api/shares/{s['id']}/send", json={})
        self.assertEqual(r.status_code, 403)
        # 本文に user_id を入れても、認証ユーザー以外にはなれない
        r = self.d.post(f"/api/shares/{s['id']}/send", json={"user_id": self.ids["mother"]})
        self.assertEqual(r.status_code, 403)
        # 本人の編集
        r = self.c.patch(f"/api/shares/{s['id']}", json={"final_text": "バラが咲いたよ"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["final_text"], "バラが咲いたよ")
        # 空文字は送れない
        r = self.c.post(f"/api/shares/{s['id']}/send", json={"final_text": "  "})
        self.assertEqual(r.status_code, 400)
        # 送信
        r = self.c.post(f"/api/shares/{s['id']}/send", json={"final_text": "バラが咲いたよ"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "sent")
        sent_at = r.json()["sent_at"]
        # 送信後ロック
        r = self.c.patch(f"/api/shares/{s['id']}", json={"final_text": "書きかえ"})
        self.assertEqual(r.status_code, 409)
        r = self.c.post(f"/api/shares/{s['id']}/send", json={})
        self.assertEqual(r.status_code, 409)
        got = self.store.get_share(s["id"])
        self.assertEqual(got["final_text"], "バラが咲いたよ")
        self.assertEqual(got["sent_at"], sent_at)

    def test_placeholder_cannot_be_sent(self):
        s = self.d.post("/api/shares/draft", json={}).json()
        self.assertEqual(s["final_text"], main.PLACEHOLDER_DRAFT)
        r = self.d.post(f"/api/shares/{s['id']}/send", json={})
        self.assertEqual(r.status_code, 400)
        r = self.d.post(f"/api/shares/{s['id']}/send", json={"final_text": main.PLACEHOLDER_DRAFT})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.c.get("/api/letters").json()["received"], [])

    def test_draft_is_not_visible_until_sent(self):
        s = self._draft()
        self.assertEqual(self.d.get("/api/letters").json()["received"], [])
        self.c.post(f"/api/shares/{s['id']}/send", json={"final_text": "ok"})
        self.assertEqual(len(self.d.get("/api/letters").json()["received"]), 1)

    def test_stamp_reply_visible_to_sender(self):
        s = self._draft()
        self.c.post(f"/api/shares/{s['id']}/send", json={"final_text": "バラが咲いたよ"})
        # 値域チェック
        r = self.d.post(f"/api/letters/{s['id']}/stamps", json={"stamp": "<b>hi</b>"})
        self.assertEqual(r.status_code, 400)
        # 送った本人はスタンプを押せない（受け取った人だけ）
        r = self.c.post(f"/api/letters/{s['id']}/stamps", json={"stamp": "うれしい"})
        self.assertEqual(r.status_code, 403)
        r = self.d.post(f"/api/letters/{s['id']}/stamps", json={"stamp": "電話するね"})
        self.assertEqual(r.status_code, 200)

        mother = self.c.get("/api/letters").json()
        self.assertEqual(mother["received"], [])
        self.assertEqual(len(mother["sent"]), 1)
        self.assertEqual([x["stamp"] for x in mother["sent"][0]["stamps"]], ["電話するね"])
        self.assertEqual(mother["sent"][0]["stamps"][0]["from_user"], self.ids["daughter"])
        self.assertEqual(mother["sent"][0]["stamps"][0]["from_name"], "娘")
        daughter = self.d.get("/api/letters").json()
        self.assertEqual(daughter["received"][0]["stamps"][0]["stamp"], "電話するね")
        self.assertEqual(daughter["received"][0]["from_name"], "母")

    def test_stamp_on_unsent_draft_rejected(self):
        s = self._draft()
        r = self.d.post(f"/api/letters/{s['id']}/stamps", json={"stamp": "うれしい"})
        self.assertEqual(r.status_code, 404)

    def test_cannot_share_with_unlinked_user(self):
        stranger, stranger_id = self.register("他人", "stranger@example.com")
        # 連携していない相手を宛先にはできない
        r = self.c.post("/api/shares/draft", json={"to_user": stranger_id})
        self.assertEqual(r.status_code, 403)
        # 誰とも連携していない人は、下書きも作れない（送り先がない）
        r = stranger.post("/api/shares/draft", json={})
        self.assertEqual(r.status_code, 400)
        r = stranger.post("/api/shares/draft", json={"to_user": self.ids["mother"]})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.c.get("/api/letters").json()["received"], [])

    def test_multiple_family_requires_choice(self):
        _, son_id = self.register("息子", "son@example.com")
        son_code = self.store.get_user(son_id)["family_code"]
        self.c.post("/api/family/link", json={"code": son_code})
        self.assertEqual(self.c.post("/api/shares/draft", json={}).status_code, 400)
        s = self.c.post("/api/shares/draft", json={"to_user": son_id}).json()
        self.assertEqual(s["to_user"], son_id)

    def test_unlink_blocks_send_and_stamp(self):
        s = self._draft()
        sent = self._draft()
        self.c.post(f"/api/shares/{sent['id']}/send", json={"final_text": "前に送った便り"})
        r = self.c.delete(f"/api/family/links/{self.ids['daughter']}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["family"], [])
        self.assertEqual(self.d.get("/api/me").json()["family"], [])  # 相手側からも外れる
        # 連携解除後は、作りかけの下書きも送れない
        r = self.c.post(f"/api/shares/{s['id']}/send", json={"final_text": "届く？"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.store.get_share(s["id"])["status"], "draft")
        # 前に届いた便りは残るが、スタンプは返せない
        self.assertEqual(len(self.d.get("/api/letters").json()["received"]), 1)
        r = self.d.post(f"/api/letters/{sent['id']}/stamps", json={"stamp": "うれしい"})
        self.assertEqual(r.status_code, 403)


# --------------------------------------------------------------------------
class TestGeminiPath(Base):
    def setUp(self):
        super().setUp()
        os.environ["GEMINI_API_KEY"] = "test-key-SECRET"

    def _analyze(self, payload, files=None, **fields):
        call, calls = fake_gemini(payload)
        rec = self.post_record(files=files, **fields).json()
        with mock.patch.object(analysis, "_call_gemini", call):
            a = self.c.post(f"/api/records/{rec['id']}/analyze").json()
        return a, calls, rec

    def test_model_env_override(self):
        os.environ["GEMINI_MODEL"] = "gemini-2.5-flash"
        a, calls, _ = self._analyze(
            {"todays_word": "「庭のバラ」がきれいだった一日。", "observations": ["バラの話"]},
            gratitude="庭のバラ", mood="元気")
        self.assertEqual(calls[0]["model"], "gemini-2.5-flash")
        self.assertEqual(a["engine"], "gemini")
        self.assertEqual(a["request"]["model"], "gemini-2.5-flash")

    def test_guard_pass_and_baseline_observations_replaced(self):
        a, calls, _ = self._analyze(
            {"todays_word": "「庭のバラ」に心が動いた今日。", "observations": ["最近元気がなさそうです"]},
            gratitude="庭のバラ", mood="元気")
        self.assertEqual(a["engine"], "gemini")
        self.assertEqual(a["fallback_reason"], "")
        self.assertEqual(a["todays_word"], "「庭のバラ」に心が動いた今日。")
        self.assertNotIn("最近元気がなさそうです", a["observations"])  # (c) 差し替え
        self.assertTrue(any("覚えている期間" in o for o in a["observations"]))
        self.assertEqual(calls[0]["api_key"], "test-key-SECRET")

    def test_ng_word_rejected(self):
        for word in ("「庭のバラ」の日。念のため病院に行ってみては。", "「庭のバラ」の日。受診をおすすめします。",
                     "「庭のバラ」の日。お薬は飲みましたか。"):
            a, _, _ = self._analyze({"todays_word": word, "observations": []},
                                    gratitude="庭のバラ", mood="元気")
            self.assertEqual(a["engine"], "mock (guard fallback)")
            self.assertEqual(a["fallback_reason"], "guard_ng_word")
            self.assertNotEqual(a["todays_word"], word)
            self.assertIn("庭のバラ", a["todays_word"])

    def test_ng_word_in_observations_rejected(self):
        a, _, _ = self._analyze(
            {"todays_word": "「庭のバラ」の日。", "observations": ["睡眠障害の可能性があります"]},
            gratitude="庭のバラ", mood="元気")
        self.assertEqual(a["fallback_reason"], "guard_ng_word")

    def test_quote_check(self):
        a, _, _ = self._analyze({"todays_word": "今日もよくがんばりましたね。", "observations": []},
                                gratitude="庭のバラ", mood="元気")
        self.assertEqual(a["fallback_reason"], "guard_no_quote")
        self.assertIn("庭のバラ", a["todays_word"])
        # 「今日」のようなありふれた語だけの一致は引用とみなさない
        self.assertIsNone(analysis.has_quote(["今日はいい日"], "今日もおつかれさま"))
        self.assertEqual(analysis.has_quote(["娘が電話をくれた"], "「電話」がうれしかった"), "電話")

    def test_audio_inline_and_transcript_saved(self):
        audio = b"OggS" + b"0" * 200
        a, calls, rec = self._analyze(
            {"transcript": "孫から手紙が届いてうれしかった。少し眠い。",
             "todays_word": "「孫から手紙が届いて」うれしかった一日ですね。", "observations": []},
            files={"audio": ("memo.ogg", audio, "audio/ogg")})
        parts = calls[0]["parts"]
        self.assertEqual(len(calls), 1)  # 文字起こしと生成を1回で
        inline = [p for p in parts if "inline_data" in p]
        self.assertEqual(inline[0]["inline_data"]["mime_type"], "audio/ogg")
        self.assertEqual(a["engine"], "gemini")
        self.assertEqual(a["kind"], "word")
        stored = self.store.get_record(rec["id"])
        self.assertEqual(stored["transcript"], "孫から手紙が届いてうれしかった。少し眠い。")
        self.assertNotIn("data", json.dumps(a["request"]))  # 音声本体はログに残さない

    def test_audio_untranscribable_returns_notice(self):
        a, _, _ = self._analyze(
            {"transcript": "", "todays_word": "今日も一日おつかれさまでした。", "observations": []},
            files={"audio": ("memo.webm", b"0" * 50, "audio/webm")})
        self.assertEqual(a["kind"], "voice_notice")
        self.assertIsNone(a["todays_word"])
        self.assertEqual(a["notice"], analysis.VOICE_NOTICE)

    def test_user_own_ng_words_quoted_are_allowed(self):
        # 本人が書いた「病院」「薬」をそのまま引用しただけなら、ガードで弾かない
        a, _, _ = self._analyze(
            {"todays_word": "「病院の帰りに見た桜」がきれいだった一日。", "observations": ["「薬局の人が親切」だった"]},
            gratitude="病院の帰りに見た桜", mood="薬局の人が親切でほっとした")
        self.assertEqual(a["engine"], "gemini", a["response"].get("guard"))
        # 引用の外で AI が自分で書いた医療助言は、これまでどおり弾く
        a, _, _ = self._analyze(
            {"todays_word": "「病院の帰りに見た桜」の日。お薬は忘れずに。", "observations": []},
            gratitude="病院の帰りに見た桜", mood="元気")
        self.assertEqual(a["fallback_reason"], "guard_ng_word")
        # 本人の言葉ではない引用の中身は、検査の対象のまま
        a, _, _ = self._analyze(
            {"todays_word": "「庭のバラ」の日。「受診」も考えて。", "observations": []},
            gratitude="庭のバラ", mood="元気")
        self.assertEqual(a["fallback_reason"], "guard_ng_word")

    def test_baseline_change_mention_rejected(self):
        a, _, _ = self._analyze(
            {"todays_word": "「庭のバラ」の日。いつもより元気がないようです。", "observations": []},
            gratitude="庭のバラ", mood="元気")
        self.assertEqual(a["fallback_reason"], "guard_baseline_change")
        self.assertIn("庭のバラ", a["todays_word"])
        # 本人が「最近」と書いたのを引用するのは問題ない
        a, _, _ = self._analyze(
            {"todays_word": "「最近忙しい」中で見つけた「庭のバラ」。", "observations": []},
            gratitude="庭のバラ", mood="最近忙しい")
        self.assertEqual(a["engine"], "gemini")

    def test_observations_as_string(self):
        a, _, _ = self._analyze(
            {"todays_word": "「庭のバラ」の日。", "observations": "バラの話をしてくれました"},
            gratitude="庭のバラ", mood="元気")
        self.assertEqual(a["response"]["parsed"]["observations"], "バラの話をしてくれました")
        self.assertEqual(a["engine"], "gemini")

    def test_gemini_error_reason_and_key_redacted(self):
        def boom(api_key, model, parts, timeout=None):
            raise RuntimeError(f"HTTP 404: model not found (key={api_key})")

        rec = self.post_record(gratitude="庭のバラ", mood="元気").json()
        with mock.patch.object(analysis, "_call_gemini", boom):
            a = self.c.post(f"/api/records/{rec['id']}/analyze").json()
        self.assertEqual(a["engine"], "mock (gemini-error fallback)")
        self.assertEqual(a["fallback_reason"], "gemini_error")
        self.assertNotIn("test-key-SECRET", json.dumps(a, ensure_ascii=False))
        dev = self.c.get("/api/dev/latest-analysis").json()
        self.assertTrue(dev["gemini_configured"])
        self.assertEqual(dev["analysis"]["fallback_reason"], "gemini_error")

    def test_care_phrase_added_by_server_after_baseline(self):
        os.environ.pop("GEMINI_API_KEY")
        self.seed_days("mother", [("庭のバラがきれいに咲いて眺めていられたこと", "よく休めて体も軽く元気です")] * 7
                       + [("天気", "元気。でも眠い")], end=self.today - timedelta(days=1))
        os.environ["GEMINI_API_KEY"] = "test-key-SECRET"
        a, _, _ = self._analyze(
            {"todays_word": "「眠い」と言いながらも「元気」な一日。", "observations": ["眠そうですね"]},
            mood="元気だけど眠い")
        self.assertEqual(a["engine"], "gemini")
        self.assertEqual(a["baseline_day"], 9)
        self.assertEqual(a["observations"][-1], analysis.FIXED_CARE_PHRASE)
        self.assertTrue(any("『眠い』" in o for o in a["observations"]))


# --------------------------------------------------------------------------
def scripted_gemini(script: dict):
    """モデル名ごとに、呼ばれるたびの結果（例外 or 返す JSON）を順に返す _call_gemini の差し替え。"""
    calls: list = []
    counters: dict = {}

    def _call(api_key, model, parts, timeout=None):
        calls.append({"model": model, "audio": any("inline_data" in p for p in parts), "timeout": timeout})
        i = counters.get(model, 0)
        counters[model] = i + 1
        seq = script.get(model, [])
        item = seq[min(i, len(seq) - 1)] if seq else analysis.GeminiHTTPError(404, "unknown model")
        if isinstance(item, BaseException):
            raise item
        return {"candidates": [{"content": {"parts": [{"text": json.dumps(item, ensure_ascii=False)}]}}]}

    return _call, calls


OK_WORD = {"todays_word": "「庭のバラ」に心が動いた今日。", "observations": []}
E503 = analysis.GeminiHTTPError(503, "The model is overloaded (high demand)")


class TestGeminiFallback(Base):
    PRIMARY = analysis.DEFAULT_GEMINI_MODEL
    FALLBACK = analysis.DEFAULT_GEMINI_FALLBACK_MODEL

    def setUp(self):
        super().setUp()
        os.environ["GEMINI_API_KEY"] = "test-key-SECRET"

    def _analyze(self, script, files=None, **fields):
        fields.setdefault("gratitude", "庭のバラ")
        fields.setdefault("mood", "元気")
        call, calls = scripted_gemini(script)
        rec = self.post_record(files=files, **fields).json()
        with mock.patch.object(analysis, "_call_gemini", call):
            a = self.c.post(f"/api/records/{rec['id']}/analyze").json()
        return a, calls

    def test_defaults(self):
        self.assertEqual(self.PRIMARY, "gemini-3.5-flash-lite")
        self.assertEqual(self.FALLBACK, "gemini-3.1-flash-lite")
        self.assertEqual(analysis.gemini_models(), [self.PRIMARY, self.FALLBACK])
        os.environ["GEMINI_FALLBACK_MODEL"] = "gemini-x"
        self.assertEqual(analysis.gemini_models(), [self.PRIMARY, "gemini-x"])
        os.environ["GEMINI_FALLBACK_MODEL"] = self.PRIMARY  # 同じなら1回だけ
        self.assertEqual(analysis.gemini_models(), [self.PRIMARY])

    def test_primary_success_does_not_use_fallback(self):
        a, calls = self._analyze({self.PRIMARY: [OK_WORD], self.FALLBACK: [OK_WORD]})
        self.assertEqual([c["model"] for c in calls], [self.PRIMARY])
        self.assertEqual(a["engine"], "gemini")
        self.assertEqual(a["request"]["model"], self.PRIMARY)
        self.assertEqual(a["request"]["model_chain"], [self.PRIMARY, self.FALLBACK])
        self.assertEqual(self.sleeps, [])
        self.assertEqual(a["response"]["gemini_attempts"],
                         [{"model": self.PRIMARY, "input": "text", "attempt": 1, "ok": True}])

    def test_primary_503_retried_then_success(self):
        a, calls = self._analyze({self.PRIMARY: [E503, OK_WORD]})
        self.assertEqual([c["model"] for c in calls], [self.PRIMARY, self.PRIMARY])
        self.assertEqual(self.sleeps, [1.0])
        self.assertEqual(a["engine"], "gemini")
        self.assertEqual(a["request"]["model"], self.PRIMARY)

    def test_primary_503_then_fallback_success(self):
        a, calls = self._analyze({self.PRIMARY: [E503], self.FALLBACK: [OK_WORD]})
        # 主モデルは初回＋再試行2回、その後に代替モデル
        self.assertEqual([c["model"] for c in calls], [self.PRIMARY] * 3 + [self.FALLBACK])
        self.assertEqual(self.sleeps, [1.0, 2.0])
        self.assertEqual(a["engine"], "gemini")
        self.assertEqual(a["fallback_reason"], "")
        self.assertEqual(a["todays_word"], OK_WORD["todays_word"])
        # 「仕組みを見る」が表示するモデル名（request.model）は、実際に応答した代替モデル
        self.assertEqual(a["request"]["model"], self.FALLBACK)
        self.assertEqual(a["response"]["model"], self.FALLBACK)
        attempts = a["response"]["gemini_attempts"]
        self.assertEqual([(x["model"], x["ok"]) for x in attempts],
                         [(self.PRIMARY, False)] * 3 + [(self.FALLBACK, True)])
        self.assertTrue(all(x["transient"] for x in attempts[:3]))
        dev = self.c.get("/api/dev/latest-analysis").json()
        self.assertEqual(dev["analysis"]["request"]["model"], self.FALLBACK)

    def test_permanent_error_not_retried(self):
        a, calls = self._analyze({self.PRIMARY: [analysis.GeminiHTTPError(400, "bad request")],
                                  self.FALLBACK: [OK_WORD]})
        self.assertEqual([c["model"] for c in calls], [self.PRIMARY, self.FALLBACK])
        self.assertEqual(self.sleeps, [])
        self.assertFalse(a["response"]["gemini_attempts"][0]["transient"])
        self.assertEqual(a["request"]["model"], self.FALLBACK)

    def test_transient_classification(self):
        t = analysis._is_transient
        for status in (408, 429, 500, 502, 503, 504):
            self.assertTrue(t(analysis.GeminiHTTPError(status, "")), status)
        for status in (400, 401, 403, 404):
            self.assertFalse(t(analysis.GeminiHTTPError(status, "")), status)
        self.assertTrue(t(TimeoutError("timed out")))
        self.assertTrue(t(ConnectionResetError()))
        import urllib.error
        self.assertTrue(t(urllib.error.URLError("timed out")))
        self.assertFalse(t(ValueError("bad json")))
        self.assertFalse(t(KeyError("candidates")))

    def test_timeout_and_429_are_retried(self):
        a, calls = self._analyze({self.PRIMARY: [TimeoutError("timed out"),
                                                 analysis.GeminiHTTPError(429, "rate"), OK_WORD]})
        self.assertEqual(len(calls), 3)
        self.assertEqual(self.sleeps, [1.0, 2.0])
        self.assertEqual(a["engine"], "gemini")
        self.assertTrue(all(c["timeout"] <= analysis.GEMINI_CALL_TIMEOUT_SEC for c in calls))

    def test_all_fail_falls_back_to_mock(self):
        a, calls = self._analyze({self.PRIMARY: [E503], self.FALLBACK: [E503]})
        self.assertEqual(len(calls), 6)
        self.assertEqual(self.sleeps, [1.0, 2.0, 1.0, 2.0])
        self.assertLessEqual(sum(self.sleeps), 6.0)
        self.assertEqual(a["engine"], "mock (gemini-error fallback)")
        self.assertEqual(a["fallback_reason"], "gemini_error")
        self.assertIn("庭のバラ", a["todays_word"])  # モックでも本人の言葉を引用
        self.assertIn("HTTP 503", a["response"]["gemini_error"])
        self.assertEqual(len(a["response"]["gemini_attempts"]), 6)
        self.assertNotIn("test-key-SECRET", json.dumps(a, ensure_ascii=False))

    def test_unparseable_response_moves_to_fallback(self):
        a, calls = self._analyze({self.PRIMARY: [KeyError("candidates")], self.FALLBACK: [OK_WORD]})
        self.assertEqual([c["model"] for c in calls], [self.PRIMARY, self.FALLBACK])
        self.assertEqual(a["engine"], "gemini")

    def test_guard_applies_to_fallback_response(self):
        bad = {"todays_word": "「庭のバラ」の日。念のため病院に行ってみては。", "observations": []}
        a, _ = self._analyze({self.PRIMARY: [E503], self.FALLBACK: [bad]})
        self.assertEqual(a["engine"], "mock (guard fallback)")
        self.assertEqual(a["fallback_reason"], "guard_ng_word")
        self.assertNotIn("病院", a["todays_word"])
        self.assertEqual(a["response"]["gemini"]["model"], self.FALLBACK)
        no_quote = {"todays_word": "今日もよくがんばりましたね。", "observations": []}
        a, _ = self._analyze({self.PRIMARY: [E503], self.FALLBACK: [no_quote]})
        self.assertEqual(a["fallback_reason"], "guard_no_quote")

    def test_audio_transient_goes_to_fallback_with_audio(self):
        audio = b"OggS" + b"0" * 200
        ok = {"transcript": "孫から手紙が届いた", "todays_word": "「孫から手紙が届いた」一日。", "observations": []}
        a, calls = self._analyze({self.PRIMARY: [E503], self.FALLBACK: [ok]},
                                 files={"audio": ("memo.ogg", audio, "audio/ogg")})
        # 混雑で失敗した音声は、文字だけに落とさず音声のまま代替モデルへ
        self.assertEqual([(c["model"], c["audio"]) for c in calls],
                         [(self.PRIMARY, True)] * 3 + [(self.FALLBACK, True)])
        self.assertEqual(self.store.get_record(a["record_id"])["transcript"], "孫から手紙が届いた")
        self.assertEqual(a["request"]["model"], self.FALLBACK)

    def test_audio_permanent_error_retries_text_on_same_model(self):
        audio = b"0" * 50
        a, calls = self._analyze({self.PRIMARY: [analysis.GeminiHTTPError(400, "unsupported mime"), OK_WORD]},
                                 files={"audio": ("memo.webm", audio, "audio/webm")})
        self.assertEqual([(c["model"], c["audio"]) for c in calls], [(self.PRIMARY, True), (self.PRIMARY, False)])
        self.assertIn("HTTP 400", a["response"]["audio_error"])
        self.assertEqual(a["engine"], "gemini")

    def test_time_budget_caps_waiting(self):
        # 時計を進める呼び出し：毎回タイムアウトまで待たされたことにする
        clock = [0.0]

        def slow(api_key, model, parts, timeout=None):
            clock[0] += timeout
            raise TimeoutError("timed out")

        def sleep(sec):
            self.sleeps.append(sec)
            clock[0] += sec

        with mock.patch.object(analysis, "_monotonic", lambda: clock[0]), \
                mock.patch.object(analysis, "_sleep", sleep), \
                mock.patch.object(analysis, "_call_gemini", slow):
            rec = self.post_record(gratitude="庭のバラ", mood="元気").json()
            a = self.c.post(f"/api/records/{rec['id']}/analyze").json()
        self.assertEqual(a["fallback_reason"], "gemini_error")
        self.assertLessEqual(clock[0], analysis.GEMINI_TOTAL_BUDGET_SEC)
        # 主モデルが時間を使い切っても、代替モデルにも呼び出しの時間が残っている
        models = [x["model"] for x in a["response"]["gemini_attempts"]]
        self.assertIn(self.FALLBACK, models)


# --------------------------------------------------------------------------
class TestMirrorWord(unittest.TestCase):
    """モックの今日の一言：日記の要約ではなく、本人の言葉を映し返す（#7）。API を通さない単体検査。"""

    SAMPLES = [
        ("今日も天気がいい！", "そこそこ元気！"),
        ("孫から電話があった", "元気だけど眠い"),
        ("", "元気だけど眠い"),
        ("", "眠い。元気"),
        ("温かいお茶", "なんだか疲れた"),
        ("", "なんだか疲れた、気分が重い"),
        ("庭のバラが咲いた", "ふつう"),
        ("", "ふつう"),
        ("", "楽しかった"),
        ("庭のバラが咲いた", ""),
        ("病院の帰りに見た桜", "薬を飲んで少し楽"),  # 本人の言葉に NG 語があっても、引用の外には出ない
    ]

    def _word(self, g: str, m: str, d: str, spoken: str = "") -> str:
        r = analysis._analyze_mock(
            {"user_id": "mother", "date": d, "gratitude": g, "mood": m, "transcript": spoken}, 3, None)
        self.assertEqual(r["kind"], "word")
        return r["todays_word"]

    def test_all_variants_quote_and_pass_guards(self):
        # 連続した3日で、締めの文のすべての版を通す
        for g, m in self.SAMPLES:
            for d in ("2026-11-21", "2026-11-22", "2026-11-23"):
                with self.subTest(g=g, m=m, d=d):
                    w = self._word(g, m, d)
                    answers = [t for t in (g, m) if t]
                    self.assertTrue(analysis.has_quote(answers, w), w)
                    own = analysis.strip_user_quotes(w, answers)
                    self.assertEqual(analysis.find_ng_words(own), [], w)
                    # ベースライン中にも出すので、変化・比較の語を地の文に含めない
                    self.assertEqual([x for x in analysis.BASELINE_CHANGE_WORDS if x in own], [], w)
                    self.assertNotIn("昨日", own)
                    self.assertNotIn(analysis.FIXED_CARE_PHRASE, w)
                    # 要約の定型（旧テンプレート）に戻っていない
                    self.assertNotIn("今日のあなたの記録です", w)
                    self.assertNotIn("と教えてくれました", w)

    def test_spoken_only_quotes_transcript(self):
        w = self._word("", "", "2026-11-23", spoken="孫から手紙が届いた")
        self.assertIn("「孫から手紙が届いた」", w)

    def test_coexisting_feelings_are_mirrored(self):
        for m in ("元気だけど眠い", "元気。でも眠い", "眠い。元気"):
            with self.subTest(m=m):
                w = self._word("", m, "2026-11-23")
                self.assertIn("「元気」", w)
                self.assertIn("「眠い」", w)
                self.assertIn("同居", w)
        self.assertNotIn("同居", self._word("", "元気がない", "2026-11-23"))

    def test_low_mood_is_not_relabelled_as_positive(self):
        w = self._word("温かいお茶", "なんだか疲れた", "2026-11-23")
        self.assertIn("「なんだか疲れた」", w)
        self.assertIn("「温かいお茶」", w)
        self.assertNotIn("明るい", w)

    def test_hedge_word_is_noticed(self):
        w = self._word("今日も天気がいい！", "そこそこ元気！", "2026-11-23")
        self.assertIn("「そこそこ」という言い方", w)

    def test_consecutive_days_vary(self):
        words = {self._word("庭のバラが咲いた", "ふつう", f"2026-11-{d}") for d in (21, 22, 23)}
        self.assertEqual(len(words), 3)

    def test_no_gratitude_placeholder_is_not_quoted(self):
        w = self._word("特になし", "なんだか疲れた", "2026-11-23")
        self.assertNotIn("「特になし」", w)
        self.assertIn("「なんだか疲れた」", w)

    def test_gemini_prompt_keeps_format_and_prohibitions(self):
        req = {"inputs": {"gratitude": "庭のバラ", "mood": "元気だけど眠い"},
               "baseline_day": 3, "baseline_period": True}
        p = analysis._gemini_prompt(req, with_audio=False)
        for key in ('"transcript"', '"todays_word"', '"observations"'):
            self.assertIn(key, p)
        self.assertIn("判断・診断", p)
        self.assertIn("病名や医療的な助言", p)
        self.assertIn("変化への言及はしないでください", p)
        self.assertIn("「」で具体的にそのまま引用", p)
        # 鏡としての返し方の指示（今回足した部分）には NG 語を含めない
        guidance = analysis.SYSTEM_RULES.split("『今日の一言』は日記の要約ではありません", 1)[1]
        self.assertEqual(analysis.find_ng_words(guidance), [])


class TestChangeRulesAndSeed(Base):
    def test_rules_silent_during_baseline(self):
        res = self.seed_days("mother", [("", "眠い")] * 7)
        for a in res:
            self.assertFalse(any("ここ" in o or "最近" in o for o in a["observations"]))
            self.assertNotIn(analysis.FIXED_CARE_PHRASE, a["observations"])

    def test_seed_demo_script_and_rules_fire_after_day8(self):
        db = os.path.join(self.dir, "seed.db")
        env = {**os.environ, "APP_UPLOAD_DIR": os.path.join(self.dir, "up")}
        env.pop("GEMINI_API_KEY", None)
        p = subprocess.run(
            [sys.executable, os.path.join(BACKEND_DIR, "scripts", "seed_demo.py"),
             "--db", db, "--reset", "--end-date", TODAY.isoformat(), "--with-letter"],
            capture_output=True, text=True, env=env, cwd=BACKEND_DIR,
        )
        self.assertEqual(p.returncode, 0, p.stderr)
        main.store = self.store = SQLiteStore(db)

        mother = sorted(self.store.list_records("mother"), key=lambda r: r["date"])
        daughter = self.store.list_records("daughter")
        self.assertGreaterEqual(len(mother), 10)
        self.assertGreaterEqual(len(daughter), 10)
        self.assertEqual(mother[-1]["date"], TODAY.isoformat())

        def rule_obs(rec):
            ana = self.store.analysis_for_record(rec["id"])
            return [o for o in ana["observations"] if o.startswith(("ここ", "最近", "『元気』", "『眠い』"))], ana

        for i, rec in enumerate(mother, start=1):
            obs, ana = rule_obs(rec)
            self.assertEqual(ana["baseline_day"], i)
            if i <= 7:
                self.assertEqual(obs, [], f"day {i}")
                self.assertNotIn(analysis.FIXED_CARE_PHRASE, ana["observations"])
        obs9, ana9 = rule_obs(mother[8])
        obs10, ana10 = rule_obs(mother[9])
        self.assertTrue(any("回答がいつもより短め" in o for o in obs9))
        self.assertTrue(any("『眠い』" in o for o in obs9))
        self.assertTrue(any("感謝の欄が空" in o for o in obs10))
        self.assertEqual(len(obs10), 3)
        self.assertIn(analysis.FIXED_CARE_PHRASE, ana10["observations"])
        # 判定語・病名は出さない
        all_text = json.dumps([self.store.analysis_for_record(r["id"])["observations"] for r in mother],
                              ensure_ascii=False)
        self.assertEqual(analysis.find_ng_words(all_text.replace(analysis.FIXED_CARE_PHRASE, "")), [])

        # デモアカウントでログインすると、API 経由で seed データが見える
        mom = self.login_client("haha@demo.local", "demo-mama-2026")
        dau = self.login_client("musume@demo.local", "demo-musume-2026")
        me = mom.get("/api/me").json()
        self.assertEqual(me["user"]["display_name"], "母さん")
        self.assertEqual(me["user"]["id"], "mother")
        self.assertEqual([f["display_name"] for f in me["family"]], ["娘さん"])  # 連携済み
        self.assertEqual(len(mom.get("/api/records").json()), 10)
        self.assertEqual(len(dau.get("/api/records").json()), 10)
        # 今日（10日目）の観察として見える
        tw = mom.get("/api/today-word").json()
        self.assertEqual(tw["baseline_day"], 10)
        self.assertTrue(any("『眠い』" in o for o in tw["observations"]))
        # 娘には変化の観察は出ない（シナリオ上は安定）
        d_tw = dau.get("/api/today-word").json()
        self.assertFalse(any(o.startswith(("ここ", "最近")) for o in d_tw["observations"]))
        # 送信済みの便り（--with-letter）
        received = dau.get("/api/letters").json()["received"]
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0]["from_name"], "母さん")
        # 間違ったパスワードでは入れない
        r = self.anon.post("/api/auth/login", json={"email": "haha@demo.local", "password": "wrong-pass"})
        self.assertEqual(r.status_code, 401)
        # 出力にパスワードを出さない
        self.assertNotIn("demo-mama-2026", p.stdout + p.stderr)

    def login_client(self, email, password):
        r = self.anon.post("/api/auth/login", json={"email": email, "password": password})
        self.assertEqual(r.status_code, 200, r.text)
        return self.client_for(r.json()["token"])


# --------------------------------------------------------------------------
class TestAuth(Base):
    PROTECTED = [
        ("get", "/api/me"), ("get", "/api/stamps"), ("get", "/api/records"), ("post", "/api/records"),
        ("get", "/api/records/1"), ("post", "/api/records/1/analyze"), ("get", "/api/today-word"),
        ("get", "/api/dev/latest-analysis"), ("get", "/api/weekly"), ("post", "/api/shares/draft"),
        ("patch", "/api/shares/1"), ("post", "/api/shares/1/send"), ("get", "/api/letters"),
        ("post", "/api/letters/1/stamps"), ("post", "/api/family/link"),
        ("delete", "/api/family/links/x"), ("post", "/api/auth/logout"),
    ]

    def test_full_authenticated_flow(self):
        """登録→ログイン→記録→解析→共有→スタンプ を、登録したての2アカウントで通す。"""
        r = self.anon.post("/api/auth/register", json={
            "display_name": "はなこ", "email": "  Hanako@Example.com ", "password": "hana-pass-1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("password", json.dumps(r.json()))
        self.assertNotIn("hash", json.dumps(r.json()))
        self.assertEqual(r.json()["user"]["email"], "hanako@example.com")
        # ログイン（大文字小文字・前後の空白は区別しない）
        r = self.anon.post("/api/auth/login", json={"email": "HANAKO@example.com", "password": "hana-pass-1"})
        self.assertEqual(r.status_code, 200, r.text)
        hanako = self.client_for(r.json()["token"])
        taro, _ = self.register("たろう", "taro@example.com")
        # 連携
        taro_code = taro.get("/api/me").json()["user"]["family_code"]
        r = hanako.post("/api/family/link", json={"code": taro_code.lower().replace("-", " ")})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["linked"]["display_name"], "たろう")
        # 記録→解析
        rec = hanako.post("/api/records", data={"gratitude": "朝の紅茶", "mood": "おだやか"}).json()
        a = hanako.post(f"/api/records/{rec['id']}/analyze").json()
        self.assertIn("朝の紅茶", a["todays_word"])
        self.assertEqual(hanako.get("/api/today-word").json()["todays_word"], a["todays_word"])
        # 共有（下書き→編集→送信）
        s = hanako.post("/api/shares/draft", json={"record_id": rec["id"]}).json()
        self.assertEqual(s["to_name"], "たろう")
        self.assertEqual(taro.get("/api/letters").json()["received"], [])  # 送るまで届かない
        hanako.patch(f"/api/shares/{s['id']}", json={"final_text": "紅茶がおいしかったよ"})
        self.assertEqual(hanako.post(f"/api/shares/{s['id']}/send", json={}).json()["status"], "sent")
        # スタンプ
        received = taro.get("/api/letters").json()["received"]
        self.assertEqual(received[0]["final_text"], "紅茶がおいしかったよ")
        self.assertEqual(taro.post(f"/api/letters/{s['id']}/stamps", json={"stamp": "うれしい"}).status_code, 200)
        sent = hanako.get("/api/letters").json()["sent"]
        self.assertEqual(sent[0]["stamps"][0]["from_name"], "たろう")
        # 既存の母・娘のデータとは混ざらない
        self.assertEqual(len(hanako.get("/api/records").json()), 1)
        self.assertEqual(self.c.get("/api/records").json(), [])

    def test_unauthenticated_401(self):
        for method, path in self.PROTECTED:
            r = getattr(self.anon, method)(path)
            self.assertEqual(r.status_code, 401, (method, path, r.text))
            # 不正・形式違いのトークンも 401
            for header in ("Bearer nope", "Basic abc", "Bearer "):
                r = getattr(self.anon, method)(path, headers={"Authorization": header})
                self.assertEqual(r.status_code, 401, (method, path, header))
        self.assertEqual(self.anon.get("/api/health").status_code, 200)

    def test_logout_revokes_token(self):
        cl, _ = self.register("一時", "tmp@example.com")
        self.assertEqual(cl.get("/api/me").status_code, 200)
        self.assertEqual(cl.post("/api/auth/logout").status_code, 200)
        self.assertEqual(cl.get("/api/me").status_code, 401)

    def test_expired_session_rejected(self):
        cl, uid = self.register("期限", "exp@example.com")
        token = cl.headers["Authorization"].split(" ", 1)[1]
        th = auth.token_hash(token)
        self.store.delete_session(th)
        self.store.create_session(th, uid, (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat())
        self.assertEqual(cl.get("/api/me").status_code, 401)
        self.assertIsNone(self.store.get_session(th))

    def test_register_validation(self):
        bad = [
            {"display_name": "", "email": "a@example.com", "password": "password-1"},
            {"display_name": "あ", "email": "not-an-email", "password": "password-1"},
            {"display_name": "あ", "email": "a@example.com", "password": "short7!"},  # 7文字
        ]
        for body in bad:
            self.assertEqual(self.anon.post("/api/auth/register", json=body).status_code, 400, body)
        ok = {"display_name": "あ", "email": "a@example.com", "password": "12345678"}  # 8文字はOK
        self.assertEqual(self.anon.post("/api/auth/register", json=ok).status_code, 200)
        dup = {**ok, "email": "A@EXAMPLE.COM"}
        self.assertEqual(self.anon.post("/api/auth/register", json=dup).status_code, 409)

    def test_login_failures_are_indistinguishable(self):
        r1 = self.anon.post("/api/auth/login", json={"email": "mother@example.com", "password": "wrong-pass"})
        r2 = self.anon.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-pass"})
        self.assertEqual((r1.status_code, r2.status_code), (401, 401))
        self.assertEqual(r1.json(), r2.json())

    def test_password_and_token_storage(self):
        import sqlite3
        r = self.anon.post("/api/auth/register",
                           json={"display_name": "保存", "email": "store@example.com", "password": "plain-secret-9"})
        token = r.json()["token"]
        uid = r.json()["user"]["id"]
        stored = self.store.get_password_hash(uid)
        self.assertNotIn("plain-secret-9", stored)
        self.assertTrue(stored.startswith("pbkdf2_sha256$1000$"))
        self.assertTrue(auth.verify_password("plain-secret-9", stored))
        self.assertFalse(auth.verify_password("plain-secret-0", stored))
        # 同じパスワードでも salt が違うのでハッシュは別
        self.assertNotEqual(auth.hash_password("plain-secret-9"), auth.hash_password("plain-secret-9"))
        # DB にはトークン本体ではなくハッシュだけ
        conn = sqlite3.connect(self.store.db_path)
        dump = "\n".join(conn.iterdump())
        conn.close()
        self.assertNotIn(token, dump)
        self.assertNotIn("plain-secret-9", dump)
        self.assertIn(auth.token_hash(token), dump)
        # 既定の反復回数（テスト中は下げている）
        self.iter_patch.stop()
        try:
            self.assertGreaterEqual(auth.PBKDF2_ITERATIONS, 600_000)
        finally:
            self.iter_patch.start()
        # どの応答にもハッシュを返さない
        cl = self.client_for(token)
        self.assertNotIn("pbkdf2", json.dumps(cl.get("/api/me").json()))


# --------------------------------------------------------------------------
class TestOwnership(Base):
    def test_other_users_data_is_forbidden(self):
        rec = self.post_record(gratitude="母の秘密", mood="元気").json()
        self.assertEqual(self.d.get(f"/api/records/{rec['id']}").status_code, 403)
        self.assertEqual(self.d.post(f"/api/records/{rec['id']}/analyze").status_code, 403)
        self.c.post(f"/api/records/{rec['id']}/analyze")
        # 他人の記録を共有の下書きに使えない
        self.assertEqual(self.d.post("/api/shares/draft", json={"record_id": rec["id"]}).status_code, 403)
        # 一覧・今日の一言・仕組みを見る・週のカウントは自分の分だけ
        self.assertEqual(self.d.get("/api/records").json(), [])
        self.assertFalse(self.d.get("/api/today-word").json()["has_record"])
        self.assertIsNone(self.d.get("/api/dev/latest-analysis").json()["analysis"])
        self.assertEqual(self.d.get("/api/weekly").json()["days_recorded"], 0)
        # クエリで user_id を指定しても、他人のデータは見えない（認証ユーザーが基準）
        self.assertEqual(self.d.get("/api/records", params={"user_id": self.ids["mother"]}).json(), [])
        # 本人は見られる
        self.assertEqual(self.c.get(f"/api/records/{rec['id']}").status_code, 200)
        self.assertEqual(self.c.get("/api/records/999999").status_code, 404)


# --------------------------------------------------------------------------
class TestFamily(Base):
    def test_me_and_link(self):
        me = self.c.get("/api/me").json()
        self.assertRegex(me["user"]["family_code"], r"^[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}$")
        self.assertEqual(me["family"], [{"id": self.ids["daughter"], "display_name": "娘"}])
        self.assertEqual(set(me["user"]), {"id", "email", "display_name", "family_code"})
        # 娘側からも見える（連携は双方向）
        self.assertEqual([f["display_name"] for f in self.d.get("/api/me").json()["family"]], ["母"])
        # もう一度入れても重複しない
        code = self.d.get("/api/me").json()["user"]["family_code"]
        r = self.c.post("/api/family/link", json={"code": code})
        self.assertTrue(r.json()["already_linked"])
        self.assertEqual(len(r.json()["family"]), 1)

    def test_link_errors(self):
        own = self.c.get("/api/me").json()["user"]["family_code"]
        self.assertEqual(self.c.post("/api/family/link", json={"code": own}).status_code, 400)
        self.assertEqual(self.c.post("/api/family/link", json={"code": "AAAA-AAAA-AAAA"}).status_code, 404)
        self.assertEqual(self.c.post("/api/family/link", json={"code": ""}).status_code, 404)
        self.assertEqual(self.c.delete("/api/family/links/nobody").status_code, 404)

    def test_family_codes_are_unique_and_random(self):
        codes = {auth.new_family_code() for _ in range(200)}
        self.assertEqual(len(codes), 200)
        self.assertEqual(auth.normalize_family_code("abcd efgh-jkmn"), "ABCD-EFGH-JKMN")
        self.assertEqual(auth.normalize_family_code("abc"), "")


# --------------------------------------------------------------------------
class TestLegacyMigration(Base):
    def test_fixed_users_migrated_to_demo_accounts(self):
        # 旧版のDB：アカウントはなく、user_id = mother / daughter の記録と送信済みの便りだけがある
        legacy = SQLiteStore(os.path.join(self.dir, "legacy.db"))
        rec = legacy.create_record({"user_id": "mother", "date": "2026-11-20", "gratitude": "旧版の記録", "mood": "元気"})
        legacy.create_record({"user_id": "daughter", "date": "2026-11-20", "gratitude": "娘の旧記録", "mood": "ふつう"})
        share = legacy.create_share({"from_user": "mother", "to_user": "daughter", "record_id": rec["id"],
                                     "draft_text": "旧版の便り"})
        legacy.update_share(share["id"], {"status": "sent", "sent_at": "2026-11-20T12:00:00+00:00"})
        self.assertTrue(auth.migrate_legacy_users(legacy))
        self.assertFalse(auth.migrate_legacy_users(legacy))  # 2回目は何もしない
        main.store = self.store = legacy

        mom = self.anon.post("/api/auth/login", json={"email": "haha@demo.local", "password": "demo-mama-2026"})
        dau = self.anon.post("/api/auth/login", json={"email": "musume@demo.local", "password": "demo-musume-2026"})
        self.assertEqual((mom.status_code, dau.status_code), (200, 200))
        mom_c, dau_c = self.client_for(mom.json()["token"]), self.client_for(dau.json()["token"])
        self.assertEqual(mom_c.get("/api/records").json()[0]["gratitude"], "旧版の記録")
        self.assertEqual(dau_c.get("/api/letters").json()["received"][0]["final_text"], "旧版の便り")
        self.assertEqual(dau_c.post(f"/api/letters/{share['id']}/stamps", json={"stamp": "うれしい"}).status_code, 200)

    def test_empty_db_gets_no_demo_accounts(self):
        fresh = SQLiteStore(os.path.join(self.dir, "fresh.db"))
        self.assertFalse(auth.migrate_legacy_users(fresh))
        self.assertIsNone(fresh.find_user_by_email("haha@demo.local"))


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
