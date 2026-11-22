"""アップロード（写真・音声）の保存先。

UPLOAD_BACKEND=gcs なら Google Cloud Storage（GCS_UPLOAD_BUCKET）、それ以外はローカルディスク（APP_UPLOAD_DIR）。

- 記録の audio_path / photo_path には、ローカルなら絶対パス、GCS なら ``gs://<bucket>/<uuid>.<ext>`` を保存する。
  どちらも basename が公開名（``/uploads/<uuid>.<ext>``）になるので、フロントの参照先は変わらない。
- 配信：ローカルは従来どおり StaticFiles。GCS はバックエンドが GCS から読んで返すプロキシ
  （``/uploads/<名前>`` のまま、認証なし・uuid のファイル名という既存の性質を変えない）。
- google-cloud-storage は GCS を選んだときだけ import する（ローカルでは未インストールでも動く）。
  認証は ADC（Cloud Run のサービスアカウント）。鍵ファイルは使わない。
"""
from __future__ import annotations

import logging
import os
import re
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

log = logging.getLogger("kagami.uploads")

# H-6: MIME 許可リストから拡張子を決める（利用者のファイル名は使わない）
PHOTO_MIMES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
AUDIO_MIMES = {"audio/webm": ".webm", "audio/mp4": ".m4a", "audio/ogg": ".ogg"}
EXT_TO_MIME = {ext: mime for mime, ext in {**PHOTO_MIMES, **AUDIO_MIMES}.items()}

# 新しく保存するファイル名（uuid4 の hex ＋許可リストの拡張子）。GCS の配信ではこれ以外を受け付けない
_NAME_RE = re.compile(r"^[0-9a-f]{32}(" + "|".join(re.escape(e) for e in EXT_TO_MIME) + r")$")


def new_name(ext: str) -> str:
    return f"{uuid.uuid4().hex}{ext}"


def public_url(ref: str | None) -> str | None:
    """保存した参照（パス or gs://）から公開URLを作る。サーバーの実パスやバケット名は返さない（M-7）。"""
    return f"/uploads/{os.path.basename(ref)}" if ref else None


class UploadStorage:
    backend = ""

    def save(self, name: str, data: bytes, mime: str) -> str:
        """保存して、記録に入れる参照を返す。"""
        raise NotImplementedError

    def read(self, ref: str | None) -> bytes | None:
        """参照の中身。無ければ None。"""
        raise NotImplementedError

    def delete(self, ref: str | None) -> None:
        """自分の保存先の参照だけを消す。失敗しても例外にしない。"""
        raise NotImplementedError

    def mount(self, app: FastAPI) -> None:
        """``/uploads/<名前>`` の配信を app に登録する。"""
        raise NotImplementedError


class LocalUploadStorage(UploadStorage):
    backend = "local"

    def __init__(self, directory: str):
        self.directory = directory
        os.makedirs(directory, exist_ok=True)

    def save(self, name: str, data: bytes, mime: str) -> str:
        path = os.path.join(self.directory, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def read(self, ref: str | None) -> bytes | None:
        if not ref or not os.path.isfile(ref):
            return None
        with open(ref, "rb") as f:
            return f.read()

    def delete(self, ref: str | None) -> None:
        if not ref:
            return
        try:
            real = os.path.realpath(ref)
            if real.startswith(os.path.realpath(self.directory) + os.sep) and os.path.isfile(real):
                os.remove(real)
        except OSError:
            pass

    def mount(self, app: FastAPI) -> None:
        app.mount("/uploads", StaticFiles(directory=self.directory), name="uploads")


def _is_not_found(exc: Exception) -> bool:
    # google.api_core.exceptions.NotFound は code == 404。ライブラリを import せずに判定する
    return getattr(exc, "code", None) == 404


def _parse_range(header: str | None, size: int) -> tuple[int, int] | None | bool:
    """``Range: bytes=...`` を (start, end) に。指定なし・複数範囲は None（全体を返す）、満たせなければ False。"""
    if not header:
        return None
    unit, _, spec = header.partition("=")
    if unit.strip().lower() != "bytes" or "," in spec:
        return None
    first, sep, last = spec.strip().partition("-")
    if not sep:
        return False
    try:
        if first == "":
            n = int(last)
            if n <= 0:
                return False
            start, end = max(size - n, 0), size - 1
        else:
            start = int(first)
            end = int(last) if last else size - 1
    except ValueError:
        return False
    if start < 0 or start >= size or end < start:
        return False
    return start, min(end, size - 1)


class GCSUploadStorage(UploadStorage):
    backend = "gcs"

    def __init__(self, bucket_name: str, client=None):
        if not bucket_name:
            raise RuntimeError("UPLOAD_BACKEND=gcs には GCS_UPLOAD_BUCKET（バケット名）が必要です")
        if client is None:
            try:
                from google.cloud import storage as gcs  # 遅延 import（GCS を選んだときだけ）
            except ImportError as e:
                raise RuntimeError("UPLOAD_BACKEND=gcs には google-cloud-storage が必要です"
                                   "（pip install -r requirements.txt）") from e
            client = gcs.Client()  # ADC（Cloud Run のサービスアカウント）。鍵ファイルは使わない
        self.bucket_name = bucket_name
        # client.bucket() は API を呼ばない（storage.buckets.get 権限は不要）
        self._bucket = client.bucket(bucket_name)
        self._prefix = f"gs://{bucket_name}/"

    def _object_name(self, ref: str | None) -> str | None:
        """自分のバケットの、uuid 形式の名前だけを受け付ける（それ以外は触らない）。"""
        if not ref or not ref.startswith(self._prefix):
            return None
        name = ref[len(self._prefix):]
        return name if _NAME_RE.match(name) else None

    def save(self, name: str, data: bytes, mime: str) -> str:
        if not _NAME_RE.match(name):
            raise ValueError("unexpected upload name")
        self._bucket.blob(name).upload_from_string(data, content_type=mime)
        return self._prefix + name

    def _download(self, name: str) -> bytes | None:
        try:
            return self._bucket.blob(name).download_as_bytes()
        except Exception as e:  # noqa: BLE001
            if _is_not_found(e):
                return None
            raise

    def read(self, ref: str | None) -> bytes | None:
        name = self._object_name(ref)
        if not name:
            return None
        try:
            return self._download(name)
        except Exception as e:  # noqa: BLE001
            log.warning("GCS read failed: %s", type(e).__name__)
            return None

    def delete(self, ref: str | None) -> None:
        name = self._object_name(ref)
        if not name:
            return
        try:
            self._bucket.blob(name).delete()
        except Exception as e:  # noqa: BLE001
            if not _is_not_found(e):
                log.warning("GCS delete failed: %s", type(e).__name__)

    def serve(self, name: str, range_header: str | None = None, head: bool = False) -> Response:
        if not _NAME_RE.match(name):
            raise HTTPException(404, "Not Found")
        try:
            data = self._download(name)
        except Exception as e:  # noqa: BLE001
            log.warning("GCS read failed: %s", type(e).__name__)
            raise HTTPException(503, "いまは読み込めません。少し時間をおいてください") from None
        if data is None:
            raise HTTPException(404, "Not Found")
        size = len(data)
        headers = {
            # Content-Type は保存時の metadata ではなく、拡張子（＝MIME 許可リスト）から決める
            "Content-Type": EXT_TO_MIME[os.path.splitext(name)[1]],
            "Accept-Ranges": "bytes",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, max-age=86400",  # uuid 名で中身は変わらない
        }
        rng = _parse_range(range_header, size)
        if rng is False:
            return Response(status_code=416, headers={**headers, "Content-Range": f"bytes */{size}"})
        status = 200
        if rng:
            start, end = rng
            data = data[start:end + 1]
            headers["Content-Range"] = f"bytes {start}-{end}/{size}"
            status = 206
        headers["Content-Length"] = str(len(data))
        return Response(content=b"" if head else data, status_code=status, headers=headers)

    def mount(self, app: FastAPI) -> None:
        storage = self

        @app.api_route("/uploads/{name}", methods=["GET", "HEAD"], include_in_schema=False)
        def serve_upload(name: str, request: Request):
            return storage.serve(name, request.headers.get("range"), head=request.method == "HEAD")


def create_upload_storage(local_dir: str) -> UploadStorage:
    if os.environ.get("UPLOAD_BACKEND", "").strip().lower() == "gcs":
        return GCSUploadStorage(os.environ.get("GCS_UPLOAD_BUCKET", "").strip())
    return LocalUploadStorage(local_dir)
