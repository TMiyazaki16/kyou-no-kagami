# リポジトリガイドライン

## プロジェクト概要

「きょうのかがみ」（ver 0.01）。Google AI Challenge の応募作。**提出目標は 2026-11-29**。

家族の体調の変化に、**本人が**気づくためのアプリ。中心は本人の自己認知で、AI は本人の言葉を映す「鏡」として気づきを本人に返す。家族への共有は、本人が選んだときだけ使う「出口」（opt-in）であって、家族が見守るためのアプリではない。

- バックエンド: FastAPI（Python 3.10 以上）＋ SQLite（ローカル・テスト）／ Cloud SQL for MySQL（本番方針、`DB_BACKEND=mysql`）。AI は Gemini API（未設定ならローカルモック）
- フロントエンド: React + TypeScript / Vite。全10画面（従来の9画面＋「設定・家族」）と、未ログイン時のログイン/登録画面
- アカウント制（メール＋パスワード、2026-11-22 にオーナー決定で固定2名の切替から移行）。家族とは家族コードで連携する。GCP 連携（Cloud Run / Cloud Storage のリソース作成）とデプロイはまだない。MySQL 版 Store と Cloud Storage 版のアップロード保存先は実装済みだが、どちらも実機では未検証
- git 管理外。大きく書き換える前に `/tmp` へバックアップを取る

設計方針・手順・制約は `README.md` を正とする。ここに書いていないことは README の該当節を読む。

## 仕様の不変条件

企画の根幹なので、どの変更でも崩さない。崩す必要が出たら実装せずにオーナーへ相談する。

- **記録は2問だけ**：①今日、感謝できることは？ ②今日の気分・体調を一言で言うと？ 写真は任意。質問を増やさない
- **AI は判定・診断をしない**：病名・医療的な助言を出さない。受診勧奨は定型文 `FIXED_CARE_PHRASE`（`analysis.py`）だけで、それもサーバーが付ける。Gemini には書かせない
- **最初の7日間はベースライン**：記録した日数が7日以下のあいだは、変化を指摘しない（観察文はサーバーの定型文に差し替え、ルールも発動しない）
- **ストリークを表示しない**：連続日数は出さず、「今週は○日できました」という週単位の肯定的なカウントにする。0日のときも責める文にしない
- **共有は本人が送るまで発生しない**：本人が日を選んで「送る」まで、誰にも届かない。便りの中身は、その日の AI フィードバック（今日の一言＋観察）の**無編集の引用**（送信時点のスナップショット）と、本人の**コメント**（任意。空でも送れる）だけ。本人が編集できるのはコメントだけで、引用は誰も書き換えられない。生の記録（2問の回答・文字起こし）は便りに入れない。同じ日（記録）×同じ宛先は1回だけ送れる（取り消した便りは数えない）。複数の宛先は宛先ごとに独立した便りにする（2026-11-24 オーナー決定、#35）。送信後は書き換えられない。ただし送信者本人による取り消し（削除）はでき、取り消すと家族の画面からも消え、付いたリアクション・返信も一緒に消える。自分の返信はいつでも削除できる（2026-11-24 オーナー決定、#21）。記録画面の「まだ誰にも届いていません」を消さない
- **家族間で記録日数を比べない**：家族の記録回数を並べる・比べる表示を作らない
- **今日の一言は本人の言葉を引用する**：その日の回答（または声の文字起こし）の言葉を「」でそのまま引用する。汎用の励まし文にしない。引用する言葉がないときは、一言の代わりに案内文（`kind: "voice_notice"`）を返す

## ディレクトリ構成

```
backend/
  app/main.py         FastAPI のエンドポイント。認証（current_user）・日付の決定（JST）・アップロード検証・所有者と連携の権限
  app/auth.py         パスワードハッシュ（PBKDF2）・トークン・家族コード・デモアカウントと旧データの移行
  app/storage.py      保存層。Store インターフェースと SQLiteStore 実装、DB_BACKEND で選ぶ create_store()
  app/storage_mysql.py  MySQLStore（Cloud SQL for MySQL。PyMySQL は DB_BACKEND=mysql のときだけ import）
  app/uploads.py      写真・音声の保存先（ローカル / Cloud Storage、UPLOAD_BACKEND で選ぶ）と /uploads の配信。MIME 許可リストもここ
  app/analysis.py     日次解析。Gemini / モックの切替と、Gemini 出力の後段ガード
  app/rules.py        変化への気づきルール（観察文を返す）
  scripts/seed_demo.py  デモデータ投入
  tests/test_api.py   TestClient による検証（unittest）。便りのリアクション・返信・写真は TestLetterReactions、引用＋コメントの送信・複数宛て・二重送信・プレビューは TestShareQuote、取り消し・返信の削除は TestShareDeleteAndPickRecord
  tests/test_mysql_store.py  MySQLStore の検証（オフライン検査は常時、実機検査は KAGAMI_TEST_MYSQL=1 のときだけ）
  tests/test_uploads.py  保存先の切替の検証（GCS はフェイクの Client。実機は使わない）
frontend/src/
  App.tsx             外枠のみ（ログイン確認・ヘッダー・ナビ・画面の切替）。画面の中身は書かない
  navigation.ts       Screen（画面ID）・NAV（ナビの並びと文言）・Ctx（各画面に渡す共通 props）
  screens/            1画面1ファイル（全10画面＋ログイン/登録 AuthScreen）。ファイル名＝コンポーネント名
  components/         2画面以上・外枠で使う部品（WeeklyMessage・WeekCard、BottomTabs、DiaryBell＝ヘッダーの通知ベル・NotificationPanel＝ベルから開く反応の一覧、水面の部品：WaterBand＝帯・MirrorWord＝今日の一言の水鏡・WaveLines、ConfirmDialog＝取り消し・破棄の確認）
  styles.css          デザイン「水面」（#19）。色・寸法は :root の CSS 変数、部品は共通クラス（.card・button.primary・.notice・.band・.mirror・.stream・.rings・.tab・.fab＝家族の日記の「投稿する」）。画面ごとの場当たりの色・寸法を足さない。入力中にタブ・保存ボタンの位置を切り替えない（クリックが届かなくなる）
  lib/                React にも API にも依存しない小関数（format.ts：localDateStr・errText・relativeTimeStr・excerpt、audio.ts：録音形式、diarySeen.ts：通知バッジの既読時刻を localStorage に置く・通知パネルの一覧を組み立てる）
  api.ts              API クライアントと型。トークンを localStorage に置き Authorization ヘッダーで送る
```

フロントの依存は `App → screens → components / lib → api` の一方向。画面から `fetch` を直接呼ばず `api.ts` を通す。画面を足すときは `navigation.ts` の `Screen`・`NAV` と `App.tsx` の切替に1行ずつ足す（画面の追加自体はオーナー確認が要る）。

### アカウントと家族連携

- 登録は表示名＋メール＋パスワード（8文字以上）。メールは小文字化して一意。パスワードは `hashlib.pbkdf2_hmac`（SHA-256、ユーザーごとの salt、600,000 回）で `pbkdf2_sha256$回数$salt$hash` 形式で保存する。追加依存なし
- セッションは不透明トークン（`secrets.token_urlsafe(32)`、30日）。サーバーには SHA-256 だけを保存し、本体を返すのは登録・ログインの応答の1回だけ。ログアウトでサーバー側のセッションを消す
- `/api/health` と `/api/auth/register`・`/api/auth/login` 以外の `/api/*` は `Authorization: Bearer <token>` 必須（なければ 401）。誰のデータかは常にトークンから決める。リクエストの `user_id` は受け付けない（送られても無視）
- 他人の記録の参照・解析・共有は 403。便りは `GET /api/shares/preview?record_id=`（引用・宛先ごとの送信済みかどうか）で開き、`POST /api/shares`（`{record_id, to_users, comment, include_photo}`）で送る。`Store.create_letters` が二重送信の判定と宛先ごとの作成を1トランザクションで行い、送信済みの宛先が1人でもいれば誰にも送らず 409。便りを書き換える API は作らない。送信の取り消しは `DELETE /api/letters/{id}`（送った本人だけ。`Store.delete_share` が反応・返信ごと消す）、返信の削除は `DELETE /api/letters/{id}/replies/{reply_id}`（書いた本人だけ）
- 家族コードは `XXXX-XXXX-XXXX`（紛らわしい文字を除く31文字×12桁）。相手のコードを入れると双方向に連携する（コードを伝えること＝同意とみなす）。解除はどちらからでもでき、解除後はその相手に便りを送れず、スタンプも返せない（届いた便りは残る）
- **連携しても記録は届かない**：連携は「便りを送れる相手」を決めるだけ。連携相手の記録・一言・観察・週のカウントを見せる API や画面を作らない（不変条件「共有は本人が送るまで発生しない」「記録日数を比べない」を守るため）
- 届いた便りへのリアクション（`STAMPS`＝「いいね」＋旧スタンプ。保存は `stamps` 表、`toggle_stamp` で人×種類ごとに1つ）と返信（`letter_replies` 表、200字まで）は、受け取った連携中の相手だけ。既読表示は作らない
- 便りの写真は既定で届けない。送信時に `include_photo: true` のときだけ、記録の写真の参照を `shares.photo_path` に写し取る（応答は `photo_url` だけ。記録の上書き時、便りが参照する写真は消さない）
- 宛先は `to_users`（連携中の家族から複数可。画面は連携相手が1人なら最初からチェック）。未連携の相手は 403、宛先なし・誰とも連携していなければ 400。引用は `main._feedback_snapshot`（一言が `kind: "word"` の日だけ。案内文の日・上書き後に解析していない日は送れない）
- 旧版（#35 より前）の便りは書き換えない：送信済みは `snapshot_json` が NULL で本文 `final_text` のまま表示し、二重送信の判定にも数える。未送信の下書き（`status='draft'`）は DB に残るが、どの API・画面にも出ない
- 表示名は本人が決める（デモは「母さん」のように敬称込み）。画面では表示名に「さん」を自動で付けない
- **パスワード・トークンをログ・レスポンス・ファイルに出さない**（`Store` の利用者向けメソッドは `password_hash` を返さない。照合は `get_password_hash` だけ）

### デモアカウントと旧データの移行

| 表示名 | メール | パスワード | ID |
|---|---|---|---|
| 母さん | `haha@demo.local` | `demo-mama-2026` | `mother` |
| 娘さん | `musume@demo.local` | `demo-musume-2026` | `daughter` |

- デモ用に公開している認証情報。本番のDBには作らない
- ID を旧固定ユーザーと同じ `mother` / `daughter` にしているので、旧データ（記録・解析・共有・スタンプ）は行を書き換えずにそのまま引き継がれる
- 起動時（`main.py` の import 時）に `auth.migrate_legacy_users` が、`mother` / `daughter` のデータがあってアカウントがないときだけデモアカウントを作り、連携する。旧データのない新しいDBでは何もしない
- `seed_demo.py` は `auth.ensure_demo_accounts` で2アカウントを作って連携してから投入する

- `storage.py` — 永続化はすべて `Store` を経由する。実装の選択は `storage.create_store()`（`DB_BACKEND=mysql` → `MySQLStore`、それ以外 → `SQLiteStore`）。Store にメソッドを足すときは `SQLiteStore` と `MySQLStore` の両方に実装する（`test_mysql_store` がメソッドの一致を検査する）。`main.py` から SQL を直接書かない。アカウント・セッション・家族連携も `Store` のメソッド（`create_user` / `create_session` / `add_family_link` など）を通す。記録は1ユーザー1日1件で、同じ日の2回目は `upsert_record` で上書きする。既存DBに足りない列は起動時に `ALTER TABLE` で追加する（analyses は `_ANALYSIS_EXTRA_COLUMNS`、shares は `_SHARE_EXTRA_COLUMNS`。MySQL は `storage_mysql._MYSQL_ANALYSIS_EXTRA_COLUMNS` / `_MYSQL_SHARE_EXTRA_COLUMNS` にも足す）
- `storage_mysql.py` — MySQL の方言：プレースホルダは `%s`（SQL にリテラルの `%` を書かない）、`BIGINT AUTO_INCREMENT`、索引・一意の列は VARCHAR、TEXT にリテラル DEFAULT を付けない（値はアプリが渡す）、`utf8mb4` / `utf8mb4_bin`（SQLite と同じバイナリ照合）。日時は DATETIME にせずタイムゾーン付き ISO 文字列（`main._parse_ts` が前提にしている）。記録は `(user_id, date)` に UNIQUE を付けて `INSERT ... ON DUPLICATE KEY UPDATE` で upsert。重複判定は INSERT IGNORE ではなくエラー 1062 を捕まえる。スキーマは `CREATE TABLE IF NOT EXISTS` で冪等。接続情報（特にパスワード）をログ・例外メッセージ・repr に出さない
- `uploads.py` — アップロードの読み書き・削除・配信はすべて `main.upload_storage`（`uploads.create_upload_storage()`）を通す。`main.py` で `open()` や GCS を直接触らない。`UPLOAD_BACKEND=gcs` → `GCSUploadStorage`（バケット `GCS_UPLOAD_BUCKET` 直下、記録の参照は `gs://<bucket>/<uuid>.<ext>`）、それ以外 → `LocalUploadStorage`（`APP_UPLOAD_DIR`、参照は絶対パス）。公開URLはどちらも `/uploads/<basename>`（`uploads.public_url`）で、API にバケット名・`gs://`・実パスを出さない。配信はローカルが StaticFiles、GCS はバックエンドが読んで返すプロキシ（署名付き URL にしない＝認証なし・uuid 名という既存の性質を保つ）。GCS 配信は uuid 形式の名前だけ受け付け、Content-Type は拡張子から決める。`google-cloud-storage` は GCS 選択時だけ import（ローカルでは未インストールでも動く）。認証は ADC（Cloud Run のサービスアカウント、権限はバケットに `roles/storage.objectAdmin` 相当）で、鍵ファイルは使わない・リポジトリに置かない
- `analysis.py` — `GEMINI_API_KEY` があれば Gemini、なければ決定的なモック。呼び出しは主モデル → 代替モデル（`GEMINI_FALLBACK_MODEL`）→ モックの順で、一次的エラー（503・429・5xx・タイムアウト）だけ各モデル最大2回まで指数バックオフ（1s→2s）で再試行する（待ちは `analysis._sleep` を通すので、テストは差し替えて実時間で眠らない）。全滅したらモックに落とし、理由を `fallback_reason`（`no_api_key` / `gemini_error` / `guard_*`）に残す。「仕組みを見る」画面がこれを表示する。Gemini の出力はそのまま使わず、`_apply_guards` で検査する：NG語、引用チェック、ベースライン中の変化への言及（引用部分を除いて検査）、ベースライン中の観察文差し替え、定型文の付与、疲れ・体調の断定調（`guard_assertive`）、外見へのコメント（`guard_appearance`、写真の有無によらず）。写真があれば音声と同じく `inline_data` で渡し（各試行とも）、写真を受け付けない恒常的エラーでは写真だけ外して続行する。モックは写真の中身に触れず「写真が届いた」とだけ書く。観察候補（`observation_candidates`、最大3件）は Gemini が提案し、`evaluate_candidates` が候補ごとに採否を決める（引用の実在・NG語・外見・断定調・ベースライン・ルールとの重複。採用分はルール観察の後ろ、`care_context` には関わらない、モックは候補を出さない）。ガードを緩める変更は、審査で「AIは診断しない」を説明できなくなるので相談する
- `rules.py` — Python だけで書いた3ルール（回答が短い日が続く／感謝欄が空の日／「眠い」等のことばが続く）。出力は判定ではなく観察文。病名や助言は書かず、配慮が必要な文脈では `care_context=True` を返すだけにする（定型文は `analysis.py` が付ける）
- `scripts/seed_demo.py` — デモアカウント（母さん・娘さん、連携済み）を作り、10日分を、最終日（既定は JST の今日）からさかのぼって投入する。母は1〜7日目が安定、8〜10日目は「元気だけど眠い」で回答が短くなる。9日目に2ルール、10日目に3ルールが発動する。解析はモックで行う。`--with-letter` の便りは7日目のフィードバックの引用＋母のコメントで、`Store.create_letters` で作る

## 開発コマンド

システムの `python3` には FastAPI が入っていない。`backend/.venv/bin/python` を使う。

```sh
# バックエンド
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # 初回のみ
.venv/bin/uvicorn app.main:app --reload --port 8000                  # http://localhost:8000/api/health

# デモデータ（--reset は data/app.db を削除して作り直す）
.venv/bin/python scripts/seed_demo.py --reset                 # デモアカウント2つ（連携済み）＋10日分
.venv/bin/python scripts/seed_demo.py --reset --with-letter   # 母→娘の送信済み便りも1通
.venv/bin/python scripts/seed_demo.py --reset --leave-today   # 今日の母の記録はデモでその場で入力する
.venv/bin/python scripts/seed_demo.py --db /tmp/demo.db --reset --end-date 2026-11-29   # 別DB・日付指定

# フロントエンド（バックエンドを先に起動しておく）
cd frontend
npm install
npm run dev     # http://localhost:5173（/api と /uploads は 8000 番へプロキシ）
```

- データは `backend/data/app.db`、アップロードは `backend/data/uploads/`（`APP_DB_PATH` / `APP_UPLOAD_DIR` で変更可）
- 日付はサーバーが `Asia/Tokyo` で決める。フロントから日付を送らない。表示用にフロントで日付を組み立てるときは `toISOString()`（UTC）を使わず、ローカル時刻から作る

### Docker（デモ用。上のベタ起動はそのまま使える）

```sh
docker compose up --build    # app/ 直下で。http://localhost:5173
docker compose exec backend python scripts/seed_demo.py --reset --with-letter   # デモデータ

DB_BACKEND=mysql docker compose --profile mysql up --build   # mysql:8 を使う（既定の up は SQLite のまま）
docker compose exec backend python scripts/seed_demo.py --reset --confirm-mysql-reset kagami --with-letter
```

- `backend`（`backend/Dockerfile`、python:3.12-slim、uvicorn :8000）と `frontend`（`frontend/Dockerfile`、node:20-alpine でビルド → nginx:alpine。`frontend/nginx.conf` が `/api/`・`/uploads/` を `http://backend:8000` へプロキシ）。公開ポートは 5173 だけ
- データは名前付きボリューム `kagami-data`（`/app/data`、`APP_DB_PATH=/app/data/app.db`、`APP_UPLOAD_DIR=/app/data/uploads`）。ホストの `backend/data` とは別。**このボリュームはデモ専用**なので、コンテナ内での `seed_demo.py --reset` は手元データを消す禁止事項に当たらない（ホストの `data/app.db` には今までどおり実行しない）
- `GEMINI_API_KEY` / `GEMINI_MODEL` / `GEMINI_FALLBACK_MODEL` はホストの環境変数から引き継ぐ（未設定ならモック）。キーを `docker-compose.yml` やイメージに書き込まない
- `mysql` サービス（`mysql:8`、`profiles: [mysql]`、ボリューム `kagami-mysql`、ポート非公開）はローカル検証専用。パスワードは compose 内のローカル用既定値で、本番の値は書かない。`seed_demo.py` の MySQL での `--reset` は全テーブルを空にするため `--confirm-mysql-reset <DB名>` が必須（`--db` は SQLite 専用）
- nginx の `client_max_body_size` はアップロード上限（写真 10MB・音声 5MB）に合わせて 20m にしてある。`MAX_BYTES` を上げるときはここも直す

### 検証

```sh
cd backend && .venv/bin/python -m unittest discover -s tests -v   # pytest は入っていない
cd frontend && npm run build                                      # tsc の型チェック込み
# MySQL 実機（検証専用DBの全テーブルを空にする）
cd backend && KAGAMI_TEST_MYSQL=1 MYSQL_HOST=127.0.0.1 MYSQL_USER=... MYSQL_PASSWORD=... MYSQL_DATABASE=kagami_test .venv/bin/python -m unittest tests.test_mysql_store -v
```

テストは日付（`main.today_jst`）を固定し、Gemini 呼び出し（`analysis._call_gemini`）を差し替えて動かす。再試行の待ち（`analysis._sleep`）は `Base` で差し替えて、待った秒数を `self.sleeps` に記録する。実キーは使わない。各テストは母・娘のアカウントを API で登録・連携し、認証済みの `TestClient`（`self.c` = 母、`self.d` = 娘、`self.anon` = 未ログイン）で呼ぶ。速度のため `auth.PBKDF2_ITERATIONS` を 1000 に下げている（既定値は `TestAuth` で確認）。

## 環境変数

| 変数 | 用途 |
|---|---|
| `GEMINI_API_KEY` | 設定すると Gemini を使う。未設定ならモック。キーは `x-goog-api-key` ヘッダーで送り、エラー文では伏せ字にする |
| `GEMINI_MODEL` | モデル名。既定は `gemini-3.5-flash-lite` |
| `GEMINI_FALLBACK_MODEL` | 主モデルが失敗したときの代替モデル。既定は `gemini-3.1-flash-lite`（主モデルと同じなら代替なし）。CI/CD からは渡していない |
| `APP_DB_PATH` / `APP_UPLOAD_DIR` | SQLite とアップロードの置き場所 |
| `DB_BACKEND` | `mysql` で MySQL（Cloud SQL）、それ以外・未設定は SQLite |
| `MYSQL_HOST` / `MYSQL_PORT` | `/` で始まれば Unix ソケット（Cloud Run は `/cloudsql/<PROJECT>:<REGION>:<INSTANCE>`）、それ以外は TCP（ポート既定 3306） |
| `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | 接続情報。Cloud Run では環境変数で渡し、パスワードは Secret Manager から。値をログ・ファイルに出さない |
| `MYSQL_CONNECT_WAIT_SEC` | 起動時に MySQL の準備を待つ秒数（既定 30。認証エラーは待たない） |
| `UPLOAD_BACKEND` | `gcs` で写真・音声を Cloud Storage に保存、それ以外・未設定はローカル（`APP_UPLOAD_DIR`） |
| `GCS_UPLOAD_BUCKET` | `UPLOAD_BACKEND=gcs` のときのバケット名（必須。空なら起動時にエラー）。公開アクセス防止を有効にしたバケットを使う |

### 既知の未確認事項

- **アプリ経由での実キー生成は未確認**。旧既定の `gemini-2.0-flash` は 2026-11-22 の実地確認で生成 API が 404（"no longer available"）を返した（`gemini-2.5-flash` も新規利用不可）ため、既定を `gemini-3.8-flash` に変更した。しかし `gemini-3.8-flash` は Google 側の容量不足で 503（high demand）が継続した（11-22〜23 で計12回すべて 503）ため、2026-11-23 に実プローブで生成・文字起こしとも 200 を確認した `gemini-3.5-flash-lite` を既定にした（API 単体の疎通のみ確認。詳細はワークスペースの `gemini-503-research-2026-11-23.md`）。Lite 系でも 503 が間欠的に出ることがある（再試行で回復）。提出前にアプリ経由で確かめる。使えなければ毎回モックに落ち、「仕組みを見る」に Gemini エラーと出る
- **MySQL 版 Store は実機未検証**（2026-11-22 時点。この環境に MySQL / Docker が無かった）。オフライン検査（メソッド一致・プレースホルダ数・接続設定）だけ通っている。提出前に `docker compose --profile mysql` か Cloud SQL で `tests.test_mysql_store` の実機検査を通す
- **写真つきの解析（`inline_data` の画像）は実キーで未確認**（2026-11-23、スタブでのみ検証）。写真 10MB＋音声 5MB を base64 で同時に送ると Gemini のインライン上限（約 20MB）に近い。超えて 400 になったときは写真を外して続行する
- **Chrome の録音形式 `audio/webm` を Gemini が文字起こしできるかは未確認**（公式の対応形式一覧に載っていない）。断られたときは、文字の入力があれば文字だけで再試行する
- **Cloud Storage 版のアップロード保存先は実機未検証**（2026-11-22 時点）。フェイクの Client で保存・配信・削除・解析時の読み込みの分岐だけを `tests/test_uploads.py` で検証している。提出前に実バケットと Cloud Run のサービスアカウントで、保存→`/uploads` での表示・再生（Safari の `<audio>` の Range を含む）→上書き時の削除を確かめる
- `/uploads` は認証なしの配信のまま（ファイル名は uuid。GCS 版のプロキシも同じ）。`<img>` / `<audio>` は Authorization ヘッダーを送れないため、アカウント制にしても残っている。本番では所有者を確認する配信か、署名付き URL が必要
- アカウントまわりの範囲外（未実装）：パスワード再設定、メールアドレスの確認、ログイン試行回数の制限、アカウント削除、表示名・パスワードの変更、期限切れセッションの一括掃除（期限切れは使われたときに消す）

## AI開発ルール

### 人間確認が必要なとき

次に該当したら、実装に入らず前提・選択肢・影響を提示して確認を待つ。

- 上の「仕様の不変条件」に触れる変更、Gemini 出力ガードを緩める変更
- 画面の追加・削除、記録の質問の変更
- 新しい依存ライブラリの追加（Python・npm とも）
- 公開 API のパス・リクエスト/レスポンス形式の変更
- DB スキーマの変更（既存DBとの互換のため、列の追加は `_ANALYSIS_EXTRA_COLUMNS` と同じ方式、テーブルの追加は `SCHEMA` の `CREATE TABLE IF NOT EXISTS` にする）
- 3ファイル以上にまたがる変更 → 先に計画（変更対象 / README の更新要否 / 検証方針 / リスク）を提示する

### そのまま進めてよいとき

既存パターンに沿った修正、テストの追加、文言の調整、コメント・ドキュメントの追加。置いた仮定は作業報告に明記する。ファイル名・関数名・環境変数は実在を確認してから参照する。

### 明示依頼なしでは実行しない

- GCP 連携（Firestore / Cloud Storage / Cloud Run の作成・設定）とデプロイ
- GitHub へのプッシュ、リポジトリの作成・公開
- 実の `GEMINI_API_KEY` を使った呼び出し、キーの値の出力・ログ出力・ファイルへの書き出し
- `seed_demo.py --reset` を既定の `data/app.db` に対して実行すること（オーナーの手元データを消す）。試すときは `--db` で別のファイルを指定する

### 変更時チェックリスト

1. バックエンドの unittest がすべて通る
2. フロントの `npm run build` が通る
3. 解析・ルールを変えたら、`seed_demo.py` を別DBで実行し、1〜7日目は発動せず、9・10日目に意図どおり発動することを確かめる
4. エンドポイント・環境変数・ガード・制約を変えたら `README.md` を更新する

### 報告

日本語で。実行したコマンドと結果、置いた仮定、確認が必要な事項を明記する。実キーでの確認やテストができなかった場合はその理由も書く。未実施の手順を実施済みと書かない。

## 開発フロー

2026-11-23 から Issue ドリブンで進める。Deroshi（PM）が条件を決め、Claude が Issue 起票 → ブランチで実装 → PR 作成、Deroshi がレビューしてマージし、デプロイは CI/CD が行う。セッションは 1 Issue = 1 セッションを基本にし、引き継ぎは会話ではなく Issue・PR・このファイル・レポートで行う。

### 運用ルール

- Issue は Deroshi の条件提示を受けて Claude が起票する。テンプレ必須項目：**背景 / スコープ / 受け入れ条件 / 対象外**
- 大きな Issue（上の「人間確認が必要なとき」に当たるもの、3 ファイル以上にまたがるもの）は、実装前に Deroshi が内容を確認する。小さいタスクのバッチは起票→実装を通してよい
- ブランチ命名：`<リポジトリ名（小文字）>-i<番号>-<タイトルを英訳したスラッグ>`（例：`kyou-no-kagami-i12-enlarge-record-buttons`）。main から切る。1 Issue = 1 ブランチ = 1 PR
- **main への直接 push 禁止。必ず PR を作る**
- PR 本文に `Closes #<番号>` とテスト・ビルド結果を書く。**マージは Deroshi**（レビュー通過後）。Claude はマージしない
- push と PR 作成は、Deroshi に依頼されたときに `open-pr` の手順で行う（上の「明示依頼なしでは実行しない」は変わらない）
- GCP の手動操作はしない。デプロイは CI 経由（main へのマージで `.github/workflows/ci-cd.yml` が動く）。Terraform apply は従来どおり plan → オーナーの OK → apply のゲートを維持する（インフラリポジトリ側）
- **`.github/workflows` は変更しない**（PAT に Workflows 権限は付けていない。ワークフローの変更はオーナーの手だけ）

### スキル（`.claude/skills/`）

| スキル | 用途 |
|---|---|
| `file-issue` | 条件 → テンプレどおりに Issue を起票 |
| `start-issue` | Issue 番号 → 命名規則どおりのブランチを main から作成 |
| `open-pr` | 検品 → commit → push → PR 作成（`Closes #<番号>`・テスト結果つき）。マージはしない |
| `verify-app` | バックエンドテスト＋フロントビルド＋Playwright（Python）の画面確認＋不変条件の照合 |
| `code-review` | PR 番号 → 差分・Issue・CI を照合した構造化レビュー。マージ判断は Deroshi |

スキル本体は汎用に書いてあり、リポジトリ固有の値は下の表から読む。値が変わったら表だけを直す。

### スキルが参照する値

| キー | 値 |
|---|---|
| リポジトリ | `TMiyazaki16/kyou-no-kagami` |
| ブランチ接頭辞 | `kyou-no-kagami` |
| 既定ブランチ | `main` |
| gh | `~/workspace/tools/gh/gh`（Deroshi の VM。PATH に `gh` があればそれでよい） |
| 検証スキル | `verify-app` |
| テスト | `cd backend && env -u GEMINI_API_KEY .venv/bin/python -m unittest discover -s tests -v` |
| テストの期待値 | `Ran 157 tests` / `OK (skipped=1)`（2026-11-25 時点、#35 後。skip は MySQL 実機検査）。テストを足したら更新する |
| ビルド | `cd frontend && npm run build` |
| 画面確認用 Python | `playwright` が入った Python（アプリの依存ではない。無ければ `verify-app` の 3-1 で別 venv を作る） |
| 不変条件 | このファイルの「仕様の不変条件」 |
| 変更禁止パス | `.github/workflows/` |
