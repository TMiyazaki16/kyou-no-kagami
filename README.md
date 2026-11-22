# きょうのかがみ — Google AI Challenge app, ver 0.01

> 毎日のわたしを映す鏡。届けたい日は、家族にも。

家族の体調の変化に気づくAI。ただし中心は「家族が見守る」ことではなく、
**本人の自己認知**です。AIは本人の言葉を映す「鏡」として気づきを本人に返し、
家族への共有は本人が選ぶ「出口」（opt-in）としてだけ存在します。

## 動かし方

### バックエンド（FastAPI）

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --port 8000
```

- ヘルスチェック: http://localhost:8000/api/health（サーバーが JST で判断した「今日」も返します）
- データは `backend/data/app.db`（SQLite）、アップロードは `backend/data/uploads/` に保存されます。
  （`APP_DB_PATH` / `APP_UPLOAD_DIR` で変更可。本番の Cloud Storage は下の「アップロードの保存先」）
- Python 3.10 以上が必要です（`X | None` 型注釈、`zoneinfo` を使用）。

### デモデータの投入

```bash
cd backend
.venv/bin/python scripts/seed_demo.py --reset                 # data/app.db を作り直して10日分を投入
.venv/bin/python scripts/seed_demo.py --reset --with-letter   # 母→娘の送信済み便りも1通入れる
.venv/bin/python scripts/seed_demo.py --reset --leave-today   # 今日の母の記録はデモでその場で入力する
```

- デモアカウント（下の表）を2つ作って家族連携し、最終日（既定は JST の今日）が10日目になるよう、
  過去にさかのぼって母・娘の記録を入れます。
- 母：1〜7日目は安定、8〜10日目は「元気」と言いつつ「眠い」が続き、回答が短くなる（9・10日目は感謝欄が空）。
- 解析は決定的なローカルモックで行います（投入中は `GEMINI_API_KEY` を無視）。
- `--reset` は SQLite ファイルを削除します（既定の `data/app.db` の手元データも消えます。試すときは `--db` で別ファイルを指定）。

### Docker で動かす（デモ用）

上の venv＋Vite での起動はそのまま使えます。Docker を使う場合はリポジトリ直下（`app/`）で：

```bash
docker compose up --build        # http://localhost:5173
docker compose exec backend python scripts/seed_demo.py --reset --with-letter   # デモデータの投入（別ターミナルで）
docker compose down              # 停止（データは残る）
docker compose down -v           # 停止してデモデータのボリュームも削除
```

- `backend`（FastAPI、`python:3.12-slim`）と `frontend`（Vite でビルドした dist を `nginx:alpine` で配信）の2コンテナです。
  nginx が `/api/` と `/uploads/` を `http://backend:8000` へプロキシします（Vite 開発サーバーのプロキシと同じ役割）。
  ホストに公開するのは 5173 番だけです。
- データは名前付きボリューム `kagami-data`（コンテナ内 `/app/data`。`APP_DB_PATH=/app/data/app.db`、
  `APP_UPLOAD_DIR=/app/data/uploads`）に保存されます。ホストの `backend/data/app.db` とは別物です。
- **Docker のボリュームはデモ専用の環境として扱ってください**。上の `seed_demo.py --reset` はボリューム内の DB を作り直し、
  公開しているデモアカウントのパスワードでログインできる状態になります。個人のデータは入れないでください。
- `GEMINI_API_KEY` / `GEMINI_MODEL` は、ホストの環境変数に設定があれば backend に引き継ぎます。
  未設定ならモックで動きます。キーの値を `docker-compose.yml` に直接書かないでください。
- 日付はアプリが `Asia/Tokyo` で決めるので、コンテナのタイムゾーン設定は不要です。

## DB バックエンド（SQLite / Cloud SQL for MySQL）

本番の DB は Cloud SQL（MySQL 8）に載せる方針です。保存層は `Store` インターフェースのまま、実装を環境変数で切り替えます。

| `DB_BACKEND` | 実装 | 接続先 |
|---|---|---|
| 未設定・`mysql` 以外 | `SQLiteStore`（`backend/app/storage.py`） | `APP_DB_PATH`（ローカル開発・テストの既定） |
| `mysql` | `MySQLStore`（`backend/app/storage_mysql.py`） | `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` |

- `MYSQL_HOST` が `/` で始まれば Unix ソケット、それ以外は TCP（`MYSQL_PORT`、既定 3306）で接続します。
  **Cloud Run からは `MYSQL_HOST=/cloudsql/<PROJECT>:<REGION>:<INSTANCE>`**（Cloud SQL 接続を追加したときのソケット）を使います。
- 接続名・ユーザー・DB 名は Cloud Run の環境変数で、**パスワードは Secret Manager のシークレットを環境変数 `MYSQL_PASSWORD` として**渡します。
  イメージ・`docker-compose.yml`・リポジトリに本番の値を書かないでください。接続情報はログにも出しません
  （接続失敗時はエラーコードだけを出します）。
- 起動時にテーブルを `CREATE TABLE IF NOT EXISTS` で作ります（冪等）。DB とユーザーは事前に作っておきます。
  起動直後に接続できないときは `MYSQL_CONNECT_WAIT_SEC`（既定 30 秒）まで待ちます（認証エラーは待たずに失敗）。
- ドライバは PyMySQL（`requirements.txt` の `PyMySQL[rsa]`）。`DB_BACKEND=mysql` のときだけ import します。
  `[rsa]` は TCP 接続での MySQL 8 の既定認証（caching_sha2_password）に必要な `cryptography` を入れるためです。
- SQLite 版との違い（意図したもの）：
  - 文字コードは `utf8mb4`、照合順序は `utf8mb4_bin`（SQLite と同じく大文字小文字を区別して照合）。
  - 日時は SQLite と同じくタイムゾーン付き ISO 8601 文字列で保存します（`DATETIME` ではない）。
  - 記録に `(user_id, date)` の一意制約を付け、上書きは `INSERT ... ON DUPLICATE KEY UPDATE` で行います
    （Cloud Run の複数インスタンスでも1ユーザー1日1件を保つため）。
- `/uploads`（写真・音声）の保存先は DB とは別に `UPLOAD_BACKEND` で切り替えます（次節）。

```bash
# ローカルの MySQL で動かす（Docker。既定の docker compose up は SQLite のまま）
DB_BACKEND=mysql docker compose --profile mysql up --build
docker compose exec backend python scripts/seed_demo.py --reset --confirm-mysql-reset kagami --with-letter

# MySQL に対する Store の検証（指定した DB の全テーブルを空にする。検証専用の DB を使う）
cd backend
KAGAMI_TEST_MYSQL=1 MYSQL_HOST=127.0.0.1 MYSQL_USER=... MYSQL_PASSWORD=... MYSQL_DATABASE=kagami_test \
  .venv/bin/python -m unittest tests.test_mysql_store -v
```

- `seed_demo.py` は `DB_BACKEND=mysql` でも Store 経由で投入します。MySQL の `--reset` は全テーブルを空にするので、
  `--confirm-mysql-reset <MYSQL_DATABASE の値>` を付けないと実行しません（`--db` は SQLite 専用）。
  デモアカウントは公開パスワードなので、**本番の Cloud SQL には投入しないでください**。
- compose の `mysql` サービス（`mysql:8`、ボリューム `kagami-mysql`、ポートは公開しない）はローカル検証専用で、
  パスワードは compose 内のローカル用の既定値です。

## アップロードの保存先（ローカル / Cloud Storage）

Cloud Run は複数インスタンス・再起動ありで、ローカルディスクのファイルは消えたり別インスタンスから読めなかったりします。
本番の写真・音声は Cloud Storage に置きます（`backend/app/uploads.py`）。

| `UPLOAD_BACKEND` | 保存先 | 記録に入る参照 | `/uploads/<名前>` の配信 |
|---|---|---|---|
| 未設定・`gcs` 以外 | `APP_UPLOAD_DIR`（ローカルディスク。従来どおり） | ファイルの絶対パス | FastAPI の StaticFiles |
| `gcs` | バケット `GCS_UPLOAD_BUCKET` の直下 | `gs://<バケット>/<uuid>.<拡張子>` | バックエンドが GCS から読んで返すプロキシ |

- フロントが見る URL はどちらも `/uploads/<uuid>.<拡張子>` のままです（API はバケット名や `gs://` を返しません）。
- **配信はプロキシ方式**です。署名付き URL へのリダイレクトにしなかったのは、URL が期限付き・バケット直リンクになり、
  既存の「`/uploads/<名前>` を認証なしで配信する」性質（と nginx / Vite のプロキシ構成）が変わるためです。
  GCS 版の配信は、uuid 形式（`[0-9a-f]{32}` ＋許可リストの拡張子）の名前だけを受け付け、それ以外は 404。
  Content-Type は保存時のメタデータではなく拡張子（MIME 許可リスト）から決め、`X-Content-Type-Options: nosniff` を付けます。
  `<audio>` のシークのため `Range`（単一範囲）に 206 で応じます。オブジェクトが無ければ 404、GCS の失敗は 503。
- MIME 許可リスト・サイズ上限（写真 10MB・音声 5MB）・uuid のファイル名は、保存先によらず同じです。
- 同じ日の上書きで置きかえた古いファイルは、自分のバケットの uuid 形式のオブジェクトだけを削除します。
  GCS への保存に失敗したときは、途中まで書いたオブジェクトを消して 503 を返し、記録は保存しません。
- `google-cloud-storage` は `UPLOAD_BACKEND=gcs` のときだけ import します（ローカルでは未インストールでも動きます）。
- **本番の前提**（作成・設定はまだしていません）：
  - バケットは Cloud Run と同じリージョンに作り、**公開アクセスの防止を有効**・均一なバケットレベルのアクセスにします
    （利用者はバックエンド経由でだけ読む）。
  - 認証は Cloud Run のサービスアカウント（ADC）。**鍵ファイルは使いません**。
  - サービスアカウントにはそのバケットに対して `roles/storage.objectAdmin` 相当（オブジェクトの作成・読み取り・削除）を付けます。
    起動時にバケットの存在確認（`storage.buckets.get`）はしないので、バケットレベルの権限は不要です。
  - `GCS_UPLOAD_BUCKET` が空なら起動時にエラーで止まります。
- ローカル保存のときの旧データ（絶対パス）は、GCS に切り替えても自動では移行しません（その記録の写真・音声は 404 になります）。

## アカウントと家族連携

2026-11-22 のオーナー決定で、固定2名（母／娘）の切替から**アカウント制**に移行しました。

- **登録**：表示名＋メールアドレス＋パスワード（8文字以上）。メールは小文字に揃えて一意。
  パスワードは平文で保存せず、標準ライブラリの `hashlib.pbkdf2_hmac`（SHA-256、ユーザーごとのランダム salt、
  600,000 回）でハッシュ化します（`backend/app/auth.py`、追加依存なし）。
- **セッション**：ログインすると不透明なトークンを発行します（30日有効）。サーバーには SHA-256 だけを保存し、
  フロントは `localStorage` に保持して `Authorization: Bearer <token>` で送ります。ログアウトでサーバー側のセッションも消えます。
- **認証必須**：`/api/health` と登録・ログイン以外の `/api/*` はすべて認証が必要です（未認証は 401）。
  誰のデータかは常にトークンから決め、リクエストの `user_id` は使いません。他人の記録の参照・解析・共有は 403、
  他人の下書きの編集・送信は 403、送信済みの変更は 409 です。
- **家族連携**：各アカウントに推測しにくい家族コード（`XXXX-XXXX-XXXX`、約59bit）を発行します。
  「設定・家族」画面で自分のコードを確認し、相手のコードを入れると双方向に連携します。解除はどちらからでもできます。
  - 共有（便り）とスタンプは、**連携済みの相手とだけ**やり取りできます。未連携の相手を宛先にすると 403、
    誰とも連携していなければ下書きを作れません（400）。連携を解除すると、作りかけの下書きも送れなくなります。
  - 連携しても、記録・一言・観察が相手に見えることはありません。届くのは本人が編集して「送る」と決めた便りだけです。
  - 連携相手が複数いる場合は、下書きを作るときに送る相手を選びます。
- **パスワード・トークンはログやレスポンスに出しません**（トークンは発行時の応答で1回だけ返します）。

| エンドポイント | 内容 |
|---|---|
| `POST /api/auth/register` | `{display_name, email, password}` → `{token, user}` |
| `POST /api/auth/login` | `{email, password}` → `{token, user}`（失敗は 401。メールの有無は区別しない） |
| `POST /api/auth/logout` | セッションを消す |
| `GET /api/me` | `{user: {id, email, display_name, family_code}, family: [{id, display_name}]}` |
| `POST /api/family/link` | `{code}` で連携。自分のコードは 400、見つからなければ 404 |
| `DELETE /api/family/links/{user_id}` | 連携を解除 |
| `POST /api/shares/draft` | `{record_id?, to_user?}`（宛先は連携相手から。1人なら省略可） |

既存のエンドポイントは `user_id`（クエリ・フォーム・本文）と、スタンプの `from_user` を受け取らなくなりました。
便り・スタンプの応答には表示名（`from_name` / `to_name`）が付きます。`GET /api/users` は廃止しました（`/api/me` へ）。

### デモアカウント

| 表示名 | メールアドレス | パスワード |
|---|---|---|
| 母さん | `haha@demo.local` | `demo-mama-2026` |
| 娘さん | `musume@demo.local` | `demo-musume-2026` |

デモ用に公開している認証情報です。本番のデータベースには作らないでください。

### 旧データの移行についての注意

- デモアカウントの内部 ID は旧固定ユーザーと同じ `mother` / `daughter` です。そのため旧版の記録・解析・共有・スタンプは、
  行を書き換えずにデモアカウントへそのまま引き継がれます。
- サーバー起動時に、`mother` / `daughter` のデータがあるのにアカウントがない場合だけ、デモアカウントを自動で作って連携します
  （旧データのない新しいDBでは作りません）。つまり**旧版のDBは、上の公開パスワードでログインできる状態になります**。
  個人のデータが入っているDBを人に見せる環境では使わないでください。
- DB の変更はテーブルの追加（`users` / `sessions` / `family_links`）だけで、既存テーブルはそのままです。
- フロントは旧版の「母として使う／娘として使う」切替を廃止しました。未ログインならログイン/登録画面、
  ログイン中はヘッダーに表示名とログアウトが出ます。

### テスト

```bash
cd backend
.venv/bin/python -m unittest discover -s tests -v   # FastAPI TestClient（httpx が必要）
```

### フロントエンド（React + TypeScript / Vite）

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173 （/api と /uploads は 8000 番へプロキシ）
npm run build      # 本番ビルド（型チェック込み）
```

バックエンドを先に起動してから、フロントエンドを開いてください。

## AI（Gemini / モック）

- 環境変数 `GEMINI_API_KEY` が設定されていれば Gemini API（既定モデル
  `gemini-3.8-flash`、`GEMINI_MODEL` で変更可）で「今日の一言」を生成します。
  API キーは URL ではなく `x-goog-api-key` ヘッダーで送ります。
- 音声がある場合は `inline_data` で Gemini に渡し、**文字起こしと一言の生成を1回の呼び出し**で行います。
  文字起こしは記録の `transcript` に保存されます。音声形式を受け付けられなかった場合、
  文字の入力があれば文字だけで再試行します。
- 文字の入力も文字起こしもない場合（モック経路・聞き取れなかった場合）は汎用文を出さず、
  「声の記録を受け取りました。文字でも一言残すと…」という案内文を返します
  （API では `kind: "voice_notice"`、`todays_word: null`、`notice` に案内文）。
- **Gemini の出力はそのまま使いません**（`backend/app/analysis.py` の後段ガード）:
  1. NG語（病名・「病院」「受診」「薬」などの医療助言）が一言・観察に含まれていたら破棄してモックに差し替え
  2. 引用チェック：回答の言葉（2文字以上の部分文字列。「今日」などのありふれた語は除く）が
     一言に含まれていなければ不採用にしてモックに差し替え
  3. 最初の7日間は、観察文をサーバー側の定型文に差し替え
  4. 健康への配慮が必要な文脈（変化の観察ルールが発動したとき）は、Gemini に書かせず
     定型文 `FIXED_CARE_PHRASE` だけをサーバー側で付与
  5. 最初の7日間に、一言が変化に触れていたら（「いつもより」「最近」など）不採用にしてモックに差し替え
  - 1 と 5 は、本人の言葉をそのまま「」で引用した部分を除いて検査します
    （本人が「病院の帰りに」と書いたのを引用しただけでは弾きません）。
- 同じ日に上書きして、まだ解析し直していない記録には、前の内容の一言を出しません。
- **変化への気づきルール**（`backend/app/rules.py`、pandas なし）。8日目以降だけ動き、
  結果は「判定」ではなく観察文として「AIが観察したこと」に出ます。
  - 回答の総文字数が、はじめの7日間の平均の半分以下の日が2日以上続く
  - 直近7日のうち、感謝欄が空の日が2日以上
  - 「眠い」「だるい」「疲れ」などのことばが2日以上続けて出てくる（はじめの7日間に2日以上出ていた語は除外）

> **提出前の確認事項（M-4）**：既定モデルは `gemini-3.8-flash` です。以前の既定 `gemini-2.0-flash` は、
> 2026-11-22 の実キーでの確認で生成 API が 404（"no longer available"）を返したため変更しました
> （`gemini-2.5-flash` も新規利用不可）。`gemini-3.8-flash` は同日、503（high demand）が継続しており、
> **生成の成功はまだ確認できていません**。提出前に実キーで生成が通ることを確認してください。
> 使えない場合は `GEMINI_MODEL` で利用可能なモデルを指定します。使えないモデルのままだと毎回モックに
> フォールバックし、「仕組みを見る」に「Gemini API の呼び出しでエラー」と表示されます。
> また Chrome の録音形式 `audio/webm` は Gemini の音声対応形式の一覧に明記されていないため、
> 実キーで音声の文字起こしが通るかを一度確認してください（失敗時は上記のとおり文字だけで再試行）。
- 未設定の場合は **ローカルモック** が動きます。モックでも、本人のその日のメモの
  言葉（感謝の内容・気分の一言）をそのまま引用した、具体的な一言を決定的に生成します。
- どちらの経路で生成したかは、アプリ内の **「仕組みを見る」** 画面で
  リクエスト／レスポンス JSON とともに確認できます（デモ動画用）。
  モックに落ちた理由（キー未設定／Gemini エラー／ガードで不採用）も区別して表示します。

## CI/CD（GitHub Actions）

`.github/workflows/ci-cd.yml` の1ファイルに CI と CD をまとめています（リポジトリ `TMiyazaki16/kyou-no-kagami`、この `app/` がルート）。

| ジョブ | 実行する条件 | 内容 |
|---|---|---|
| `backend-sqlite` | push・pull_request・手動実行 | Python 3.12 で `python -m unittest discover -s tests -v`（SQLite。MySQL 実機検査はスキップ） |
| `backend-mysql` | 同上 | `mysql:8` サービスに対して `KAGAMI_TEST_MYSQL=1`・`MYSQL_DATABASE=kagami_test` で `tests.test_mysql_store` を実行 |
| `frontend` | 同上 | Node 20 で `npm ci && npm run build` |
| `deploy-backend` | `main` への push と `main` での手動実行。CI の3ジョブがすべて成功したとき | イメージ `<GCP_REGION>-docker.pkg.dev/<GCP_PROJECT_ID>/<AR_REPO>/backend:<git sha>` を push し、Cloud Run にデプロイ |
| `deploy-frontend` | 同上 | `npm run build` の `dist` をフロント用バケットへ `gsutil rsync -d`（削除同期） |
| `seed-demo` | `main` での手動実行で `seed_demo` にチェックを入れたときだけ（`deploy-backend` の後） | Cloud Run Jobs で `seed_demo.py --reset --with-letter --confirm-mysql-reset <MYSQL_DATABASE>` を実行 |

- GCP 認証は Workload Identity 連携（`google-github-actions/auth`）。鍵ファイルや秘密の値はワークフローにも GitHub Secrets にも置きません。
- Cloud Run には `DB_BACKEND=mysql`、`MYSQL_HOST=/cloudsql/<CLOUDSQL_CONNECTION_NAME>`、`MYSQL_USER`、`MYSQL_DATABASE`、
  `UPLOAD_BACKEND=gcs`、`GCS_UPLOAD_BUCKET` を環境変数で、`MYSQL_PASSWORD` を Secret Manager（`:latest`）から渡します。
  そのほか `--add-cloudsql-instances`、`--port 8000`（Dockerfile の uvicorn に合わせる）、最小インスタンス 0、未認証アクセス許可（公開デモ）。
  `--set-env-vars` / `--set-secrets` はデプロイごとに置きかえるので、Cloud Console で足した環境変数は次のデプロイで消えます。
- フロントのキャッシュ：`assets/`（ハッシュ付きファイル名）は `max-age=31536000, immutable`、`index.html` は `no-cache`。
  新しい `assets/` → `index.html` → 削除同期の順に置くので、デプロイ中に参照切れが起きにくい順序になっています。
- Gemini は任意です。`GEMINI_API_KEY_SECRET` を設定したときだけキーを Secret Manager から渡します（未設定ならモック）。

### リポジトリの Variables

GitHub の Settings → Secrets and variables → Actions → **Variables** に登録します。値は Terraform（別リポジトリ）の outputs から取ります。

| 名前 | 意味 | 必須 |
|---|---|---|
| `GCP_PROJECT_ID` | GCP プロジェクト ID（`kyou-no-kagami`） | ○ |
| `GCP_REGION` | リージョン（`asia-northeast1`）。Artifact Registry・Cloud Run・Cloud Run Jobs で共通 | ○ |
| `GCP_WIF_PROVIDER` | Workload Identity プロバイダのリソース名（`projects/<番号>/locations/global/workloadIdentityPools/<pool>/providers/<provider>`） | ○ |
| `GCP_DEPLOY_SA` | デプロイ用サービスアカウントのメール（WIF でなりかわる先） | ○ |
| `AR_REPO` | Artifact Registry（Docker 形式）のリポジトリ名 | ○ |
| `CLOUD_RUN_SERVICE` | バックエンドの Cloud Run サービス名（LB のサーバーレス NEG が指すもの） | ○ |
| `CLOUD_RUN_RUNTIME_SA` | Cloud Run（サービスとジョブ）の実行用サービスアカウントのメール。未設定ならサービスの現状／既定の SA のまま | 推奨 |
| `CLOUDSQL_CONNECTION_NAME` | Cloud SQL の接続名（`<PROJECT>:<REGION>:<INSTANCE>`） | ○ |
| `MYSQL_USER` | Cloud SQL のアプリ用ユーザー名 | ○ |
| `MYSQL_DATABASE` | DB 名。seed の `--confirm-mysql-reset` にもこの値を使う | ○ |
| `MYSQL_PASSWORD_SECRET` | `MYSQL_PASSWORD` を入れた Secret Manager のシークレット名（値ではなく名前） | ○ |
| `UPLOADS_BUCKET` | 写真・音声のバケット名（`GCS_UPLOAD_BUCKET` に入る） | ○ |
| `FRONTEND_BUCKET` | フロントの静的配信バケット名 | ○ |
| `GEMINI_API_KEY_SECRET` | Gemini API キーを入れたシークレット名 | 任意 |
| `GEMINI_MODEL` | モデル名（未設定なら既定の `gemini-3.8-flash`） | 任意 |
| `SEED_JOB_NAME` | seed 用 Cloud Run Job の名前（未設定なら `kagami-seed-demo`） | 任意 |

必須の Variables が欠けていると、デプロイジョブは最初のステップで名前を挙げて失敗します。

### 初回デプロイの前に必要なこと

1. Terraform で、Artifact Registry・Cloud SQL（インスタンス・DB・ユーザー）・Secret Manager のシークレット（DB パスワードの値を投入済み）・
   2つのバケット・Cloud Run サービス・LB（`/api/*` と `/uploads/*` をバックエンド、それ以外をフロント用バケット）・WIF を作成しておく。
2. WIF のプロバイダは、このリポジトリ（`attribute.repository == "TMiyazaki16/kyou-no-kagami"`）からのトークンだけを受け付ける条件にする。
3. デプロイ用 SA（`GCP_DEPLOY_SA`）の権限：
   - `roles/iam.workloadIdentityUser`（WIF のプリンシパルに対して）
   - Artifact Registry のリポジトリに `roles/artifactregistry.writer`
   - `roles/run.admin`（デプロイ・`--allow-unauthenticated` の IAM 設定・Cloud Run Jobs の作成と実行）
   - 実行用 SA に対する `roles/iam.serviceAccountUser`
   - フロント用バケットに `roles/storage.objectAdmin`（rsync の削除・メタデータ更新を含む）
4. 実行用 SA（`CLOUD_RUN_RUNTIME_SA`）の権限：`roles/cloudsql.client`、DB パスワード（と Gemini キー）のシークレットに
   `roles/secretmanager.secretAccessor`、アップロード用バケットに `roles/storage.objectAdmin`。
5. 組織ポリシーで `allUsers` への公開（ドメイン制限付き共有）が禁止されていると `--allow-unauthenticated` が失敗するので、事前に確認する。
6. 上の Variables を登録し、`main` に push する（または Actions タブから手動実行）。

### デモデータの投入（手動）

Actions タブ → 「CI/CD」→ **Run workflow** → ブランチ `main` を選び、`seed_demo` にチェックを入れて実行します
（`gh workflow run ci-cd.yml --ref main -f seed_demo=true` でも可）。CI → バックエンドのデプロイのあと、同じイメージ・同じ接続設定
（環境変数・シークレット・Cloud SQL 接続・実行用 SA）で Cloud Run Job を作成（既にあれば更新）して実行し、完了まで待ちます。

- **Cloud SQL の全テーブルを空にしてから**、公開パスワードのデモアカウント（母さん・娘さん）と10日分＋便り1通を投入します。
  「本番の Cloud SQL には投入しない」という上の原則の例外で、**この GCP 環境を公開デモ専用として使う前提**です。個人の記録が入ったあとに実行しないでください。
- 最終日は実行した日（JST）です。デモ当日に日付を合わせたいときは、その日に実行し直します。
- アップロード用バケットのファイルは消しません（デモデータには写真・音声がないので影響はありません）。

## 実装済み（M1 + M2）

- **記録と日付の扱い**
  - 日付はサーバーが JST（`Asia/Tokyo`）で決めます。フロントは日付を送りません。
  - 1ユーザー1日1件。同じ日に2回保存すると上書きします（新しく付けなかった声・写真は残ります）。
  - アップロード：写真は image/jpeg・png・webp（10MB まで）、音声は audio/webm・mp4・ogg（5MB まで）。
    超過は 413、対象外の形式は 415。
- **共有**：送信できるのは書いた本人だけ、下書き（draft）のときだけ、宛先は連携済みの家族だけ。送信後の編集・再送信は 409。
  空文字や下書きの案内文のままでは送れません（400）。
- **スタンプ返信**：受け取った人だけが（連携中に限り）、決まったスタンプの中から返せます。
  送った本人には「届いた便り」の「あなたの便りへの返事」に表示されます。
- **M1：記録と保存**
  - アカウントごとに記録（旧版のユーザー切替は廃止。上の「アカウントと家族連携」を参照）
  - 記録画面：2つの質問（①今日、感謝できることは？ ②今日の気分・体調を一言で言うと？）
  - 音声録音（ブラウザの MediaRecorder API）＋文字入力フォールバック、写真の添付（任意）
  - 記録画面には「まだ誰にも届いていません」を明示
  - SQLite に保存（記録一覧で再生・表示可能）
- **M2：日次解析と「今日の一言」、記録一覧**
  - 保存と同時に解析が走り、その日の言葉を具体的に引用した「今日の一言」を生成
  - 最初の7日間はベースライン学習期間として、変化への言及を一切しません
  - AIは観察のみ。判断・診断・病名は出しません
- **その他の画面（簡易版として実装）**
  - 振り返り：連続日数（ストリーク）ではなく「今週は○日できました」の前向きカウント
  - 共有の確認：AI下書き → 本人が編集 → 本人が送る、の opt-in フロー
  - 届いた便り：届いた便りへのスタンプ返信（「電話するね」等の行動につながるスタンプ含む）。
    家族間で記録回数を比べる表示はありません
  - 仕組みを見る：直近の解析のリクエスト／レスポンスと使用エンジンの表示
  - 設定・家族：自分の家族コードの表示、相手のコードでの連携と解除
  - 免責表示：医療ではないこと、固定文の受診勧奨のみ、等

## スタブ／未実装

- 音声の文字起こしは Gemini 経路のみ（モック経路では文字起こしせず、案内文を返します）
- 変化検知ルールは Python のみの3ルール（上記）。pandas による本格的なルール群は今後
- `/uploads` は認証なしの配信のまま（ファイル名は推測できない uuid、拡張子は MIME 許可リストから決定）。
  `<img>` / `<audio>` が Authorization ヘッダーを送れないため、アカウント制にしても残っています。
  GCS 版（`UPLOAD_BACKEND=gcs`）もこの性質を変えないプロキシです。所有者を確認する配信は今後
- **Cloud Storage 版の保存・配信は実機未検証**（フェイクの Client で分岐だけを `tests/test_uploads.py` で検証）
- 写真のAI解析（添付・保存・表示のみ）
- GCP 連携（Cloud Run / Cloud Storage のリソース作成）とデプロイ。DB は Cloud SQL（MySQL）用の `MySQLStore`、
  アップロードは Cloud Storage 用の保存先を実装済み（どちらも実機での動作確認は未了）
- プッシュ通知
- アカウントまわり：パスワード再設定、メールアドレスの確認、ログイン試行回数の制限、アカウント削除、
  表示名・パスワードの変更（今回の範囲外）

## 構成

```
app/
├── docker-compose.yml    # Docker でのデモ起動（backend + frontend、ボリューム kagami-data。--profile mysql で mysql:8 も）
├── backend/
│   ├── Dockerfile        # python:3.12-slim + uvicorn
│   ├── requirements.txt
│   └── app/
│       ├── main.py       # FastAPI エンドポイント（認証・所有者と連携の権限チェック）
│       ├── auth.py       # パスワードハッシュ・トークン・家族コード・デモアカウントと旧データ移行
│       ├── storage.py    # 保存層（Store インターフェース・SQLite 実装・DB_BACKEND での切替）
│       ├── storage_mysql.py  # 保存層の MySQL（Cloud SQL）実装
│       ├── uploads.py    # 写真・音声の保存先（ローカル / Cloud Storage）と /uploads の配信
│       ├── analysis.py   # 日次解析（Gemini / ローカルモック）と出力ガード
│       └── rules.py      # 変化への気づきルール（観察文を返す）
│   ├── scripts/seed_demo.py  # デモデータ投入
│   └── tests/test_api.py     # TestClient による検証（test_mysql_store.py は MySQL 版、test_uploads.py は保存先の切替の検証）
└── frontend/
    ├── package.json / vite.config.ts / tsconfig.json / index.html
    ├── Dockerfile / nginx.conf   # node:20-alpine でビルド → nginx:alpine で配信・/api と /uploads をプロキシ
    └── src/
        ├── main.tsx
        ├── App.tsx       # 全10画面＋ログイン/登録
        ├── api.ts        # API クライアント
        └── styles.css
```
