# リポジトリガイドライン

## プロジェクト概要

「きょうのかがみ」（ver 0.01）。Google AI Challenge の応募作。**提出目標は 2026-11-29**。

家族の体調の変化に、**本人が**気づくためのアプリ。中心は本人の自己認知で、AI は本人の言葉を映す「鏡」として気づきを本人に返す。家族への共有は、本人が選んだときだけ使う「出口」（opt-in）であって、家族が見守るためのアプリではない。

- バックエンド: FastAPI（Python 3.10 以上）＋ SQLite。AI は Gemini API（未設定ならローカルモック）
- フロントエンド: React + TypeScript / Vite。全10画面（従来の9画面＋「設定・家族」）と、未ログイン時のログイン/登録画面
- アカウント制（メール＋パスワード、2026-11-22 にオーナー決定で固定2名の切替から移行）。家族とは家族コードで連携する。GCP 連携（Firestore / Cloud Run / Cloud Storage）はまだない
- git 管理外。大きく書き換える前に `/tmp` へバックアップを取る

設計方針・手順・制約は `README.md` を正とする。ここに書いていないことは README の該当節を読む。

## 仕様の不変条件

企画の根幹なので、どの変更でも崩さない。崩す必要が出たら実装せずにオーナーへ相談する。

- **記録は2問だけ**：①今日、感謝できることは？ ②今日の気分・体調を一言で言うと？ 写真は任意。質問を増やさない
- **AI は判定・診断をしない**：病名・医療的な助言を出さない。受診勧奨は定型文 `FIXED_CARE_PHRASE`（`analysis.py`）だけで、それもサーバーが付ける。Gemini には書かせない
- **最初の7日間はベースライン**：記録した日数が7日以下のあいだは、変化を指摘しない（観察文はサーバーの定型文に差し替え、ルールも発動しない）
- **ストリークを表示しない**：連続日数は出さず、「今週は○日できました」という週単位の肯定的なカウントにする。0日のときも責める文にしない
- **共有は本人が送るまで発生しない**：AI の下書きを本人が編集して「送る」まで、誰にも届かない。送信後は書き換えられない。記録画面の「まだ誰にも届いていません」を消さない
- **家族間で記録日数を比べない**：家族の記録回数を並べる・比べる表示を作らない
- **今日の一言は本人の言葉を引用する**：その日の回答（または声の文字起こし）の言葉を「」でそのまま引用する。汎用の励まし文にしない。引用する言葉がないときは、一言の代わりに案内文（`kind: "voice_notice"`）を返す

## ディレクトリ構成

```
backend/
  app/main.py         FastAPI のエンドポイント。認証（current_user）・日付の決定（JST）・アップロード検証・所有者と連携の権限
  app/auth.py         パスワードハッシュ（PBKDF2）・トークン・家族コード・デモアカウントと旧データの移行
  app/storage.py      保存層。Store インターフェースと SQLiteStore 実装
  app/analysis.py     日次解析。Gemini / モックの切替と、Gemini 出力の後段ガード
  app/rules.py        変化への気づきルール（観察文を返す）
  scripts/seed_demo.py  デモデータ投入
  tests/test_api.py   TestClient による検証（unittest）
frontend/src/
  App.tsx             全10画面＋ログイン/登録
  api.ts              API クライアントと型。トークンを localStorage に置き Authorization ヘッダーで送る
```

### アカウントと家族連携

- 登録は表示名＋メール＋パスワード（8文字以上）。メールは小文字化して一意。パスワードは `hashlib.pbkdf2_hmac`（SHA-256、ユーザーごとの salt、600,000 回）で `pbkdf2_sha256$回数$salt$hash` 形式で保存する。追加依存なし
- セッションは不透明トークン（`secrets.token_urlsafe(32)`、30日）。サーバーには SHA-256 だけを保存し、本体を返すのは登録・ログインの応答の1回だけ。ログアウトでサーバー側のセッションを消す
- `/api/health` と `/api/auth/register`・`/api/auth/login` 以外の `/api/*` は `Authorization: Bearer <token>` 必須（なければ 401）。誰のデータかは常にトークンから決める。リクエストの `user_id` は受け付けない（送られても無視）
- 他人の記録の参照・解析・共有は 403、他人の下書きの編集・送信は 403、送信済みの変更は 409
- 家族コードは `XXXX-XXXX-XXXX`（紛らわしい文字を除く31文字×12桁）。相手のコードを入れると双方向に連携する（コードを伝えること＝同意とみなす）。解除はどちらからでもでき、解除後は作りかけの下書きも送れず、スタンプも返せない（届いた便りは残る）
- **連携しても記録は届かない**：連携は「便りを送れる相手」を決めるだけ。連携相手の記録・一言・観察・週のカウントを見せる API や画面を作らない（不変条件「共有は本人が送るまで発生しない」「記録日数を比べない」を守るため）
- 下書きの宛先：連携相手が1人なら自動、複数なら `to_user` で選ぶ。未連携の相手は 403、誰とも連携していなければ 400
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

- `storage.py` — 永続化はすべて `Store` を経由する。Firestore 等への移行は、同じメソッドを持つクラスを作って `main.py` の `store` を差し替えるだけで済むようにしておく。`main.py` から SQL を直接書かない。アカウント・セッション・家族連携も `Store` のメソッド（`create_user` / `create_session` / `add_family_link` など）を通す。記録は1ユーザー1日1件で、同じ日の2回目は `upsert_record` で上書きする。既存DBに足りない列は起動時に `ALTER TABLE` で追加する（`_ANALYSIS_EXTRA_COLUMNS`）
- `analysis.py` — `GEMINI_API_KEY` があれば Gemini、なければ決定的なモック。Gemini の失敗はモックに落とし、理由を `fallback_reason`（`no_api_key` / `gemini_error` / `guard_*`）に残す。「仕組みを見る」画面がこれを表示する。Gemini の出力はそのまま使わず、`_apply_guards` で検査する：NG語、引用チェック、ベースライン中の変化への言及（引用部分を除いて検査）、ベースライン中の観察文差し替え、定型文の付与。ガードを緩める変更は、審査で「AIは診断しない」を説明できなくなるので相談する
- `rules.py` — Python だけで書いた3ルール（回答が短い日が続く／感謝欄が空の日／「眠い」等のことばが続く）。出力は判定ではなく観察文。病名や助言は書かず、配慮が必要な文脈では `care_context=True` を返すだけにする（定型文は `analysis.py` が付ける）
- `scripts/seed_demo.py` — デモアカウント（母さん・娘さん、連携済み）を作り、10日分を、最終日（既定は JST の今日）からさかのぼって投入する。母は1〜7日目が安定、8〜10日目は「元気だけど眠い」で回答が短くなる。9日目に2ルール、10日目に3ルールが発動する。解析はモックで行う

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

### 検証

```sh
cd backend && .venv/bin/python -m unittest discover -s tests -v   # pytest は入っていない
cd frontend && npm run build                                      # tsc の型チェック込み
```

テストは日付（`main.today_jst`）を固定し、Gemini 呼び出し（`analysis._call_gemini`）を差し替えて動かす。実キーは使わない。各テストは母・娘のアカウントを API で登録・連携し、認証済みの `TestClient`（`self.c` = 母、`self.d` = 娘、`self.anon` = 未ログイン）で呼ぶ。速度のため `auth.PBKDF2_ITERATIONS` を 1000 に下げている（既定値は `TestAuth` で確認）。

## 環境変数

| 変数 | 用途 |
|---|---|
| `GEMINI_API_KEY` | 設定すると Gemini を使う。未設定ならモック。キーは `x-goog-api-key` ヘッダーで送り、エラー文では伏せ字にする |
| `GEMINI_MODEL` | モデル名。既定は `gemini-3.8-flash` |
| `APP_DB_PATH` / `APP_UPLOAD_DIR` | SQLite とアップロードの置き場所 |

### 既知の未確認事項

- **実キーでの生成成功は未確認**。旧既定の `gemini-2.0-flash` は 2026-11-22 の実地確認で生成 API が 404（"no longer available"）を返した（`gemini-2.5-flash` も新規利用不可）ため、既定を `gemini-3.8-flash` に変更した。`gemini-3.8-flash` は存在するが、同日は 503（high demand）が継続し、生成の成功確認は未了。提出前に確かめる。使えなければ毎回モックに落ち、「仕組みを見る」に Gemini エラーと出る
- **Chrome の録音形式 `audio/webm` を Gemini が文字起こしできるかは未確認**（公式の対応形式一覧に載っていない）。断られたときは、文字の入力があれば文字だけで再試行する
- `/uploads` は認証なしの静的配信のまま（ファイル名は uuid）。`<img>` / `<audio>` は Authorization ヘッダーを送れないため、アカウント制にしても残っている。本番では所有者を確認する配信か、署名付き URL が必要
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
