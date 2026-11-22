"""アップロード保存先（ローカル / Cloud Storage）の切替の検証。

GCS 実機は使わない。google-cloud-storage の Client / Bucket / Blob をフェイクに差し替えて、
保存・配信・削除・解析時の読み込みの分岐を確かめる（実機での動作は未検証）。
"""
from __future__ import annotations

import os
import subprocess
import sys
import types
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_api import BACKEND_DIR, PNG, Base  # noqa: F401  （環境変数の設定もここで済む）

from app import main, uploads  # noqa: E402

BUCKET = "kagami-test-bucket"


class FakeNotFound(Exception):
    code = 404  # google.api_core.exceptions.NotFound と同じ


class FakeBlob:
    def __init__(self, bucket: "FakeBucket", name: str):
        self.bucket, self.name = bucket, name

    def upload_from_string(self, data, content_type=None):
        if self.bucket.fail_uploads:
            raise RuntimeError("simulated GCS failure")
        self.bucket.objects[self.name] = (bytes(data), content_type)

    def download_as_bytes(self):
        if self.name not in self.bucket.objects:
            raise FakeNotFound(self.name)
        return self.bucket.objects[self.name][0]

    def delete(self):
        if self.name not in self.bucket.objects:
            raise FakeNotFound(self.name)
        del self.bucket.objects[self.name]


class FakeBucket:
    def __init__(self, name: str):
        self.name = name
        self.objects: dict[str, tuple[bytes, str | None]] = {}
        self.fail_uploads = False

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self, name)


class FakeClient:
    def __init__(self):
        self.buckets: dict[str, FakeBucket] = {}

    def bucket(self, name: str) -> FakeBucket:
        return self.buckets.setdefault(name, FakeBucket(name))


def make_gcs():
    client = FakeClient()
    return uploads.GCSUploadStorage(BUCKET, client=client), client.bucket(BUCKET)


# --------------------------------------------------------------------------
class TestCreateUploadStorage(unittest.TestCase):
    def test_default_is_local(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UPLOAD_BACKEND", None)
            st = uploads.create_upload_storage(main.UPLOAD_DIR)
        self.assertIsInstance(st, uploads.LocalUploadStorage)
        self.assertIsInstance(main.upload_storage, uploads.LocalUploadStorage)

    def test_gcs_requires_bucket(self):
        with mock.patch.dict(os.environ, {"UPLOAD_BACKEND": "gcs", "GCS_UPLOAD_BUCKET": ""}):
            with self.assertRaises(RuntimeError):
                uploads.create_upload_storage(main.UPLOAD_DIR)

    def test_gcs_uses_lazy_import_and_adc_client(self):
        # google.cloud.storage をフェイクモジュールで差し替える（Client() は引数なし＝ADC）
        created = []

        class Client(FakeClient):
            def __init__(self, *a, **kw):
                super().__init__()
                created.append((a, kw))

        google = types.ModuleType("google")
        cloud = types.ModuleType("google.cloud")
        storage_mod = types.ModuleType("google.cloud.storage")
        storage_mod.Client = Client
        google.cloud, cloud.storage = cloud, storage_mod
        mods = {"google": google, "google.cloud": cloud, "google.cloud.storage": storage_mod}
        with mock.patch.dict(sys.modules, mods), \
                mock.patch.dict(os.environ, {"UPLOAD_BACKEND": "GCS", "GCS_UPLOAD_BUCKET": BUCKET}):
            st = uploads.create_upload_storage(main.UPLOAD_DIR)
        self.assertIsInstance(st, uploads.GCSUploadStorage)
        self.assertEqual(created, [((), {})])  # 鍵ファイルなどは渡さない

    def test_gcs_without_library_fails_clearly(self):
        with mock.patch.dict(sys.modules, {"google.cloud": None, "google.cloud.storage": None}), \
                mock.patch.dict(os.environ, {"UPLOAD_BACKEND": "gcs", "GCS_UPLOAD_BUCKET": BUCKET}):
            with self.assertRaisesRegex(RuntimeError, "google-cloud-storage"):
                uploads.create_upload_storage(main.UPLOAD_DIR)

    def test_local_mode_does_not_import_google_cloud(self):
        env = {k: v for k, v in os.environ.items() if k not in ("UPLOAD_BACKEND", "GCS_UPLOAD_BUCKET")}
        code = "import sys; import app.main; print('google.cloud.storage' in sys.modules)"
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           env=env, cwd=BACKEND_DIR)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "False")


# --------------------------------------------------------------------------
class TestGCSServing(unittest.TestCase):
    """配信プロキシ（/uploads/<名前>）。新しい FastAPI に GCS 版を mount して確かめる。"""

    def setUp(self):
        self.st, self.bucket = make_gcs()
        app = FastAPI()
        self.st.mount(app)
        self.client = TestClient(app)
        self.name = "0123456789abcdef0123456789abcdef.png"
        self.ref = self.st.save(self.name, PNG, "image/png")

    def test_save_and_serve(self):
        self.assertEqual(self.ref, f"gs://{BUCKET}/{self.name}")
        self.assertEqual(self.bucket.objects[self.name], (PNG, "image/png"))
        r = self.client.get(f"/uploads/{self.name}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, PNG)
        self.assertEqual(r.headers["content-type"], "image/png")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertEqual(r.headers["content-length"], str(len(PNG)))
        h = self.client.head(f"/uploads/{self.name}")
        self.assertEqual(h.status_code, 200)
        self.assertEqual(h.content, b"")

    def test_content_type_comes_from_extension_not_metadata(self):
        name = "f" * 32 + ".webm"
        self.bucket.objects[name] = (b"\x1aE\xdf\xa3", "text/html")  # metadata が書き換えられていても
        r = self.client.get(f"/uploads/{name}")
        self.assertEqual(r.headers["content-type"], "audio/webm")

    def test_rejects_non_uuid_names(self):
        self.bucket.objects["evil.html"] = (b"<script>", "text/html")
        for name in ("evil.html", "mother_2026-11-22_audio.webm", "0123456789abcdef0123456789abcdef.html",
                     "..%2Fsecret"):
            self.assertEqual(self.client.get(f"/uploads/{name}").status_code, 404, name)

    def test_missing_object_is_404_and_error_is_503(self):
        self.assertEqual(self.client.get("/uploads/" + "a" * 32 + ".jpg").status_code, 404)
        with mock.patch.object(FakeBlob, "download_as_bytes", side_effect=RuntimeError("boom")):
            r = self.client.get(f"/uploads/{self.name}")
        self.assertEqual(r.status_code, 503)
        self.assertNotIn(BUCKET, r.text)

    def test_range_requests(self):
        r = self.client.get(f"/uploads/{self.name}", headers={"Range": "bytes=0-3"})
        self.assertEqual(r.status_code, 206)
        self.assertEqual(r.content, PNG[:4])
        self.assertEqual(r.headers["content-range"], f"bytes 0-3/{len(PNG)}")
        r = self.client.get(f"/uploads/{self.name}", headers={"Range": "bytes=-2"})
        self.assertEqual(r.content, PNG[-2:])
        r = self.client.get(f"/uploads/{self.name}", headers={"Range": "bytes=10-"})
        self.assertEqual(r.content, PNG[10:])
        r = self.client.get(f"/uploads/{self.name}", headers={"Range": f"bytes={len(PNG)}-"})
        self.assertEqual(r.status_code, 416)

    def test_delete_only_own_bucket_uuid_objects(self):
        self.bucket.objects["keep.txt"] = (b"x", None)
        self.st.delete(f"gs://{BUCKET}/keep.txt")
        self.st.delete(f"gs://other-bucket/{self.name}")
        self.st.delete(os.path.join("/tmp", self.name))  # ローカルの旧パスは触らない
        self.assertIn("keep.txt", self.bucket.objects)
        self.assertIn(self.name, self.bucket.objects)
        self.st.delete(self.ref)
        self.assertNotIn(self.name, self.bucket.objects)
        self.st.delete(self.ref)  # 2回目（NotFound）も例外にしない


# --------------------------------------------------------------------------
class TestRecordsWithGCS(Base):
    """記録 API の保存・上書き・解析が GCS 版を通ること（main.upload_storage を差し替え）。"""

    def setUp(self):
        super().setUp()
        self.gcs, self.bucket = make_gcs()
        self.st_patch = mock.patch.object(main, "upload_storage", self.gcs)
        self.st_patch.start()
        self.local_before = set(os.listdir(main.UPLOAD_DIR))

    def tearDown(self):
        self.st_patch.stop()
        super().tearDown()

    def post(self, client=None, **data):
        files = data.pop("files", None)
        return (client or self.c).post("/api/records", data=data, files=files)

    def test_photo_and_audio_go_to_bucket(self):
        r = self.post(gratitude="写真", files={
            "photo": ("evil.html", PNG, "image/png"),
            "audio": ("memo.webm", b"\x1aE\xdf\xa3" + b"0" * 10, "audio/webm;codecs=opus")})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertRegex(body["photo_url"], r"^/uploads/[0-9a-f]{32}\.png$")
        self.assertRegex(body["audio_url"], r"^/uploads/[0-9a-f]{32}\.webm$")
        self.assertNotIn("gs://", r.text)
        self.assertNotIn(BUCKET, r.text)
        photo = body["photo_url"].rsplit("/", 1)[1]
        audio = body["audio_url"].rsplit("/", 1)[1]
        self.assertEqual(self.bucket.objects[photo], (PNG, "image/png"))
        self.assertEqual(self.bucket.objects[audio][1], "audio/webm")
        rec = self.store.get_record(body["id"])
        self.assertEqual(rec["photo_path"], f"gs://{BUCKET}/{photo}")
        self.assertEqual(set(os.listdir(main.UPLOAD_DIR)), self.local_before)  # ローカルには書かない

    def test_overwrite_deletes_replaced_object(self):
        r1 = self.post(gratitude="1", files={"photo": ("a.png", PNG, "image/png")}).json()
        r2 = self.post(gratitude="2", files={"photo": ("a.png", PNG + b"x", "image/png")}).json()
        old, new = (x["photo_url"].rsplit("/", 1)[1] for x in (r1, r2))
        self.assertNotEqual(old, new)
        self.assertNotIn(old, self.bucket.objects)
        self.assertIn(new, self.bucket.objects)
        # 写真を付けずに上書きしても、前の写真は残る
        r3 = self.post(gratitude="3").json()
        self.assertEqual(r3["photo_url"], r2["photo_url"])
        self.assertIn(new, self.bucket.objects)

    def test_mime_and_size_rules_unchanged(self):
        r = self.post(gratitude="x", files={"photo": ("x.svg", b"<svg/>", "image/svg+xml")})
        self.assertEqual(r.status_code, 415)
        r = self.post(files={"audio": ("a.webm", b"0" * (5 * 1024 * 1024 + 1), "audio/webm")})
        self.assertEqual(r.status_code, 413)
        self.assertEqual(self.bucket.objects, {})

    def test_gcs_failure_returns_503_and_cleans_up(self):
        calls = {"n": 0}
        orig = FakeBlob.upload_from_string

        def second_fails(blob, data, content_type=None):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("simulated")
            return orig(blob, data, content_type)

        with mock.patch.object(FakeBlob, "upload_from_string", second_fails):
            r = self.post(gratitude="x", files={
                "audio": ("a.webm", b"\x1aE\xdf\xa3", "audio/webm"),
                "photo": ("a.png", PNG, "image/png")})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self.bucket.objects, {})  # 1つ目も片付ける
        self.assertEqual(self.c.get("/api/records").json(), [])

    def test_analysis_reads_audio_from_bucket(self):
        captured = {}

        def fake_analyze(rec, baseline_day, previous, history, audio):
            captured["audio"] = audio
            return {"todays_word": None, "observations": [], "engine": "mock", "request": {},
                    "response": {}, "kind": "voice_notice", "notice": "n", "fallback_reason": "no_api_key"}

        audio_bytes = b"\x1aE\xdf\xa3" + b"1" * 20
        rec = self.post(files={"audio": ("a.webm", audio_bytes, "audio/webm")}).json()
        with mock.patch.object(main.analysis_mod, "analyze", fake_analyze):
            self.assertEqual(self.c.post(f"/api/records/{rec['id']}/analyze").status_code, 200)
        self.assertEqual(captured["audio"], (audio_bytes, "audio/webm"))


if __name__ == "__main__":
    unittest.main()
