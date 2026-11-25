"""MySQLStore の検証。

- オフライン検査（常に実行）: インターフェースの一致、接続設定（ソケット / TCP）、SQL のプレースホルダ数
- 実機検査（MySQL サーバーがあるときだけ）: Store の一連の操作を実際の MySQL に対して通す
    KAGAMI_TEST_MYSQL=1 MYSQL_HOST=127.0.0.1 MYSQL_USER=... MYSQL_PASSWORD=... MYSQL_DATABASE=kagami_test \\
      .venv/bin/python -m unittest tests.test_mysql_store -v
  ※ 指定したデータベースの全テーブルを空にする。本番・手元データのDBは指定しない
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import unittest
from unittest import mock

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)

from app import storage  # noqa: E402

try:
    import pymysql  # noqa: F401
    from app import storage_mysql
    HAVE_PYMYSQL = True
except ImportError:  # SQLite だけの環境
    HAVE_PYMYSQL = False


@unittest.skipUnless(HAVE_PYMYSQL, "PyMySQL が入っていない")
class TestMySQLOffline(unittest.TestCase):
    def test_same_methods_as_store(self):
        def public(cls):
            return {n: inspect.signature(f) for n, f in vars(cls).items()
                    if callable(f) and not n.startswith("_")}
        iface = public(storage.Store)
        impl = public(storage_mysql.MySQLStore)
        for name, sig in iface.items():
            self.assertIn(name, impl, name)
            self.assertEqual(list(sig.parameters), list(impl[name].parameters), name)

    def test_settings_socket_and_tcp(self):
        base = {"MYSQL_USER": "u", "MYSQL_PASSWORD": "p", "MYSQL_DATABASE": "d"}
        with mock.patch.dict(os.environ, {**base, "MYSQL_HOST": "/cloudsql/proj:asia-northeast1:inst"}):
            s = storage_mysql.mysql_settings_from_env()
        self.assertEqual(s["unix_socket"], "/cloudsql/proj:asia-northeast1:inst")
        self.assertNotIn("host", s)
        with mock.patch.dict(os.environ, {**base, "MYSQL_HOST": "10.0.0.5", "MYSQL_PORT": "3307"}):
            s = storage_mysql.mysql_settings_from_env()
        self.assertEqual((s["host"], s["port"]), ("10.0.0.5", 3307))
        with mock.patch.dict(os.environ, {"MYSQL_USER": "", "MYSQL_DATABASE": ""}):
            with self.assertRaises(RuntimeError):
                storage_mysql.mysql_settings_from_env()

    def test_connect_error_does_not_leak_password(self):
        settings = {"host": "127.0.0.1", "port": 1, "user": "u", "password": "secret-pw-xyz", "database": "d"}
        with self.assertRaises(RuntimeError) as cm:
            storage_mysql.MySQLStore(settings, connect_wait_sec=0)
        self.assertNotIn("secret-pw-xyz", str(cm.exception))
        self.assertIsNone(cm.exception.__cause__)

    def test_placeholders_match_args(self):
        """全メソッドを偽の接続で呼び、各 SQL の %s の数と引数の数が合うこと・? が残っていないことを確かめる。"""
        executed: list[tuple[str, tuple]] = []

        class FakeCursor:
            lastrowid = 1
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def execute(self, sql, args=()):
                executed.append((sql, tuple(args or ())))
                return 1
            def fetchone(self):
                return {"id": 1, "date": "2026-11-22", "found": 1, "n": 1, "days": 1, "total": 1,
                        "password_hash": "x", "observations": "[]", "request_json": "{}",
                        "response_json": "{}", "kind": "word", "notice": "", "fallback_reason": ""}
            def fetchall(self): return []

        class FakeConn:
            def cursor(self): return FakeCursor()
            def commit(self): pass
            def rollback(self): pass
            def close(self): pass

        with mock.patch("pymysql.connect", lambda **kw: FakeConn()):
            st = storage_mysql.MySQLStore({"host": "h", "port": 1, "user": "u", "password": "p", "database": "d"})
            st.create_user({"id": "u1", "email": "a@b", "display_name": "A", "password_hash": "h",
                            "family_code": "AAAA-BBBB-CCCC"})
            for name in ("get_user", "find_user_by_email", "find_user_by_family_code",
                         "get_password_hash", "has_data_for", "list_records", "latest_analysis",
                         "list_letters_for", "list_letters_sent_by", "delete_session", "get_session"):
                getattr(st, name)("u1")
            st.create_session("t" * 64, "u1", "2026-12-22T00:00:00+00:00")
            st.add_family_link("u1", "u2"); st.remove_family_link("u1", "u2")
            st.are_linked("u1", "u2"); st.list_family("u1")
            rec = {"user_id": "u1", "date": "2026-11-22", "gratitude": "g", "mood": "m"}
            st.create_record(rec); st.upsert_record(rec); st.find_record("u1", "2026-11-22")
            st.get_record(1); st.update_record(1, {"transcript": "t"})
            st.distinct_record_days("u1", "2026-11-22"); st.previous_record("u1", 1)
            st.weekly_summary("u1", ["2026-11-21", "2026-11-22"])
            st.save_analysis({"record_id": 1, "user_id": "u1", "date": "2026-11-22", "engine": "mock",
                              "baseline_day": 1})
            st.analysis_for_record(1)
            st.create_letters("u1", 1, ["u2", "u3"], {"snapshot_json": "{}", "comment": "c", "photo_path": None})
            st.create_letters("u1", 1, ["u2"], {"snapshot_json": "{}", "sent_at": "2026-11-22T20:00:00+09:00"})
            st.sent_recipients("u1", 1); st.get_share(1)
            st.add_stamp(1, "u2", "ありがとう"); st.list_stamps(1)
            st.toggle_stamp(1, "u2", "いいね")
            st.add_reply(1, "u2", "返信"); st.list_replies(1)
            st.get_reply(1); st.delete_reply(1); st.delete_share(1)
            st.consume_gemini_quota("2026-11-22", "u1", 3, 100)
            st.reset_all()

        self.assertGreater(len(executed), 40)
        for sql, args in executed:
            self.assertNotRegex(sql, r"\?", sql)
            self.assertNotIn("INSERT OR", sql)
            self.assertNotIn("AUTOINCREMENT", sql)
            self.assertEqual(len(re.findall(r"%s", sql)), len(args), sql)
            self.assertNotRegex(sql.replace("%s", ""), r"%", sql)  # リテラルの % は pymysql の書式と衝突する


@unittest.skipUnless(HAVE_PYMYSQL and os.environ.get("KAGAMI_TEST_MYSQL") == "1",
                     "実機の MySQL を使う検証は KAGAMI_TEST_MYSQL=1 と MYSQL_* を設定したときだけ")
class TestMySQLLive(unittest.TestCase):
    def setUp(self):
        self.st = storage_mysql.MySQLStore(connect_wait_sec=5)
        self.st.reset_all()

    def test_full_flow(self):
        st = self.st
        u1 = st.create_user({"id": "u1", "email": "a@example.com", "display_name": "母さん",
                             "password_hash": "pbkdf2_sha256$1$aa$bb", "family_code": "AAAA-BBBB-CCCC"})
        self.assertNotIn("password_hash", u1)
        st.create_user({"id": "u2", "email": "b@example.com", "display_name": "娘さん",
                        "password_hash": "h2", "family_code": "DDDD-EEEE-FFFF"})
        for dup, field in (({"id": "u3", "email": "a@example.com", "family_code": "GGGG-HHHH-JJJJ"}, "email"),
                           ({"id": "u3", "email": "c@example.com", "family_code": "AAAA-BBBB-CCCC"}, "family_code"),
                           ({"id": "u1", "email": "d@example.com", "family_code": "KKKK-LLLL-MMMM"}, "id")):
            with self.assertRaises(storage.DuplicateError) as cm:
                st.create_user({**dup, "display_name": "x", "password_hash": "x"})
            self.assertEqual(cm.exception.field, field)
        # 照合はバイナリ（大文字小文字を区別）
        self.assertIsNone(st.find_user_by_email("A@EXAMPLE.COM"))
        self.assertEqual(st.find_user_by_family_code("DDDD-EEEE-FFFF")["id"], "u2")
        self.assertEqual(st.get_password_hash("u1"), "pbkdf2_sha256$1$aa$bb")

        st.create_session("a" * 64, "u1", "2026-12-22T00:00:00+00:00")
        self.assertEqual(st.get_session("a" * 64)["user_id"], "u1")
        st.delete_session("a" * 64)
        self.assertIsNone(st.get_session("a" * 64))

        self.assertTrue(st.add_family_link("u2", "u1"))
        self.assertFalse(st.add_family_link("u1", "u2"))
        self.assertTrue(st.are_linked("u1", "u2"))
        self.assertEqual(st.list_family("u1"), [{"id": "u2", "display_name": "娘さん"}])

        self.assertFalse(st.has_data_for("u1"))
        r1, old = st.upsert_record({"user_id": "u1", "date": "2026-11-21", "gratitude": "バラ🌹", "mood": "元気",
                                    "created_at": "2026-11-21T08:00:00+09:00"})
        self.assertIsNone(old)
        self.assertEqual(r1["gratitude"], "バラ🌹")  # utf8mb4（絵文字）
        r2, old = st.upsert_record({"user_id": "u1", "date": "2026-11-22", "gratitude": "", "mood": "眠い"})
        r2b, old = st.upsert_record({"user_id": "u1", "date": "2026-11-22", "gratitude": "", "mood": "元気だけど眠い",
                                     "audio_path": "/x.webm"})
        self.assertEqual((old["id"], old["mood"]), (r2["id"], "眠い"))
        self.assertEqual((r2b["id"], r2b["mood"], r2b["audio_path"]), (r2["id"], "元気だけど眠い", "/x.webm"))
        self.assertIsNone(r2b["photo_path"])
        self.assertTrue(st.has_data_for("u1"))
        self.assertEqual([r["date"] for r in st.list_records("u1")], ["2026-11-22", "2026-11-21"])
        self.assertEqual(st.distinct_record_days("u1", "2026-11-22"), 2)
        self.assertEqual(st.previous_record("u1", r2["id"])["id"], r1["id"])
        self.assertIsNone(st.previous_record("u1", r1["id"]))
        self.assertEqual(st.weekly_summary("u1", ["2026-11-21", "2026-11-22", "2026-11-23"]),
                         {"days_recorded": 2, "total_records": 2})
        self.assertEqual(st.update_record(r2["id"], {"transcript": "ねむい", "mood": "無視"})["transcript"], "ねむい")

        a = st.save_analysis({"record_id": r2["id"], "user_id": "u1", "date": "2026-11-22",
                              "todays_word": "「眠い」と書いた今日", "observations": ["観察"], "engine": "mock",
                              "baseline_day": 2, "request": {"q": "質問"}, "response": {"a": 1},
                              "fallback_reason": "no_api_key"})
        self.assertEqual((a["observations"], a["request"], a["kind"]), (["観察"], {"q": "質問"}, "word"))
        v = st.save_analysis({"record_id": r2["id"], "user_id": "u1", "date": "2026-11-22", "todays_word": None,
                              "engine": "mock", "baseline_day": 2, "kind": "voice_notice", "notice": "案内"})
        self.assertIsNone(v["todays_word"])
        self.assertEqual(st.latest_analysis("u1")["id"], v["id"])
        self.assertEqual(st.analysis_for_record(r2["id"])["notice"], "案内")

        # 便り（#35）：宛先ごとに1通。同じ記録・同じ相手への2回目は、1通も作らずに AlreadySentError
        snap = '{"date": "2026-11-22", "todays_word": "「眠い」と書いた今日", "observations": ["観察"]}'
        self.assertEqual(st.sent_recipients("u1", r2["id"]), [])
        s, s3 = st.create_letters("u1", r2["id"], ["u2", "u3"], {"snapshot_json": snap, "comment": "コメント🌹",
                                                                "photo_path": "/x.png",
                                                                "sent_at": "2026-11-22T20:00:00+09:00"})
        self.assertEqual((s["status"], s["to_user"], s3["to_user"], s["comment"], s["snapshot_json"], s["sent_at"]),
                         ("sent", "u2", "u3", "コメント🌹", snap, "2026-11-22T20:00:00+09:00"))
        self.assertEqual(st.get_share(s["id"])["photo_path"], "/x.png")
        self.assertEqual(sorted(st.sent_recipients("u1", r2["id"])), ["u2", "u3"])
        with self.assertRaises(storage.AlreadySentError) as cm:
            st.create_letters("u1", r2["id"], ["u4", "u2"], {"snapshot_json": snap, "comment": ""})
        self.assertEqual(cm.exception.to_users, ["u2"])
        self.assertEqual(sorted(st.sent_recipients("u1", r2["id"])), ["u2", "u3"])   # u4 にも作らない
        self.assertTrue(st.delete_share(s3["id"]))
        self.assertEqual(st.sent_recipients("u1", r2["id"]), ["u2"])   # 取り消した便りは数えない
        stamp = st.add_stamp(s["id"], "u2", "電話するね")
        self.assertTrue(st.toggle_stamp(s["id"], "u2", "いいね"))
        self.assertFalse(st.toggle_stamp(s["id"], "u2", "いいね"))
        rp = st.add_reply(s["id"], "u2", "見に行くね🌹")
        st.add_reply(s["id"], "u2", "二通目")
        letters = st.list_letters_for("u2")
        self.assertEqual(len(letters), 1)
        self.assertEqual((letters[0]["to_user"], letters[0]["comment"]), ("u2", "コメント🌹"))
        self.assertEqual([x["id"] for x in letters[0]["stamps"]], [stamp["id"]])
        self.assertEqual([x["body"] for x in letters[0]["replies"]], ["見に行くね🌹", "二通目"])
        self.assertEqual(letters[0]["replies"][0]["id"], rp["id"])
        self.assertEqual(st.list_letters_sent_by("u1")[0]["id"], s["id"])

        # 返信の削除（1件だけ）と、便りの取り消し（反応・返信ごと消える）
        self.assertEqual(st.get_reply(rp["id"])["body"], "見に行くね🌹")
        self.assertTrue(st.delete_reply(rp["id"]))
        self.assertFalse(st.delete_reply(rp["id"]))
        self.assertIsNone(st.get_reply(rp["id"]))
        self.assertEqual([x["body"] for x in st.list_replies(s["id"])], ["二通目"])
        other = st.create_letters("u1", r1["id"], ["u2"], {"snapshot_json": snap, "comment": ""})[0]
        st.add_reply(other["id"], "u2", "残る返信")
        self.assertTrue(st.delete_share(s["id"]))
        self.assertFalse(st.delete_share(s["id"]))
        self.assertIsNone(st.get_share(s["id"]))
        self.assertEqual((st.list_stamps(s["id"]), st.list_replies(s["id"])), ([], []))
        # 取り消した便りだけが消え、別の日の便り（other）は残る
        self.assertEqual([x["id"] for x in st.list_letters_for("u2")], [other["id"]])
        self.assertEqual([x["body"] for x in st.list_replies(other["id"])], ["残る返信"])

        self.assertTrue(st.remove_family_link("u1", "u2"))
        self.assertFalse(st.remove_family_link("u1", "u2"))
        self.assertFalse(st.are_linked("u2", "u1"))

        # 実 Gemini の回数（#34）：全体を先に判定し、超えたら何も増やさない
        day = "2026-11-22"
        self.assertIsNone(st.consume_gemini_quota(day, "u1", 2, 3))
        self.assertIsNone(st.consume_gemini_quota(day, "u1", 2, 3))
        self.assertEqual(st.consume_gemini_quota(day, "u1", 2, 3), "user")
        self.assertIsNone(st.consume_gemini_quota(day, "u2", 2, 3))
        self.assertEqual(st.consume_gemini_quota(day, "u3", 2, 3), "global")
        self.assertIsNone(st.consume_gemini_quota("2026-11-23", "u1", 2, 3))


if __name__ == "__main__":
    unittest.main()
