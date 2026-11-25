---
name: verify-app
description: アプリの検品。バックエンドのテスト（SQLite）、フロントエンドのビルド、Playwright（Python）での画面確認とスクリーンショット、仕様の不変条件の照合を行い、PR に貼れる形で報告する。PR を出す前、レビューで動作を確かめるとき、「検品して」「動作確認して」と言われたときに使う。
---

# verify-app — テスト＋ビルド＋画面の検品

コマンド・期待値などの値は、リポジトリ直下の `AGENTS.md` の「開発フロー」節にある **「スキルが参照する値」表** から読む（`<テスト>`・`<テストの期待値>`・`<ビルド>`・`<不変条件>`・`<画面確認用 Python>`）。表の値とこの文書が食い違ったら `AGENTS.md` を正とする。

## 守ること

- **手元 DB（`backend/data/app.db`）に触らない。** 画面確認は一時ディレクトリに作った別 DB で行う。`seed_demo.py --reset` を `--db` なしで実行しない
- **実の `GEMINI_API_KEY` を使わない。** サーバーもテストも `env -u GEMINI_API_KEY` で起動し、モックで確かめる
- アプリのコード・依存（`requirements.txt`・`package.json`）を検品のために変えない。Playwright はアプリの依存ではなく、検品用の別環境に入れる
- 未実施の手順を実施済みと書かない。できなかった手順は理由を書く

## 1. バックエンドのテスト（SQLite 既定）

```sh
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # .venv が無いときだけ。requirements.txt どおりに入れる（依存は増やさない）
env -u GEMINI_API_KEY .venv/bin/python -m unittest discover -s tests -v
```

- 期待値は `<テストの期待値>`（2026-11-23 時点：`Ran 68 tests` / `OK (skipped=1)`。skip は MySQL 実機検査で、`KAGAMI_TEST_MYSQL` 未設定のため）
- テストを足した PR では件数が増える。増減した件数と理由を報告する。失敗・エラーが 1 件でもあれば検品は不合格
- MySQL 実機検査（`KAGAMI_TEST_MYSQL=1`）は検証専用 DB の全テーブルを空にするので、PM の指示があるときだけ行う

## 2. フロントエンドのビルド

```sh
cd frontend
npm ci            # node_modules が無いときだけ。lockfile どおりに入れる（依存は増やさない）
npm run build     # tsc の型チェック込み
```

型エラー・ビルドエラーがあれば不合格。警告は報告に書く。

## 3. 画面の確認（Playwright・Python）

画面（`frontend/src` の画面・スタイル・`api.ts`）に関わる変更では必ず行う。バックエンドだけの変更でも、応答形式が変わるなら行う。

### 3-1. 検品用の Python を用意する

`<画面確認用 Python>` を使う。`playwright` が入っていなければ、アプリとは別の venv に入れる（ネットワークが要る）。

```sh
python3 -m venv /tmp/kagami-pw
/tmp/kagami-pw/bin/pip install playwright
/tmp/kagami-pw/bin/python -m playwright install chromium
```

入れられない環境なら 3 は実施せず、その旨を報告する。

### 3-2. 一時 DB でサーバーを起動する

```sh
W=$(mktemp -d /tmp/kagami-verify-XXXX)
cd backend
env -u GEMINI_API_KEY .venv/bin/python scripts/seed_demo.py --db "$W/app.db" --reset --with-letter
env -u GEMINI_API_KEY APP_DB_PATH="$W/app.db" APP_UPLOAD_DIR="$W/uploads" \
  .venv/bin/uvicorn app.main:app --port 8000 > "$W/backend.log" 2>&1 &
cd ../frontend
npx vite --port 5173 --host 127.0.0.1 > "$W/vite.log" 2>&1 &   # /api・/uploads は 8000 へプロキシ
curl -sf http://127.0.0.1:8000/api/health && curl -sf -o /dev/null http://127.0.0.1:5173/
```

8000 / 5173 が使用中なら、既に動いているものを止めずに PM に確認する（vite のプロキシ先は 8000 固定）。

### 3-3. 撮影する

このスキルのフォルダにある `screenshots.py` を使う。画面の一覧は `frontend/src/navigation.ts` の `NAV` から読むので、画面の追加・改名に追従する。

```sh
<画面確認用 Python> .claude/skills/verify-app/screenshots.py \
  --base http://127.0.0.1:5173 --out "$W/shots" --nav frontend/src/navigation.ts
```

- デモアカウント（母さん）でログインし、全画面をスマホ幅 390px（全体）とデスクトップ幅 1280px で撮る。ログイン画面も撮る
- ブラウザのコンソールエラー・ページエラーを集計して表示する。1 件でもあれば報告する
- 撮った画像は Read で開いて目で確かめる。崩れ・はみ出し・文字切れ・空の画面がないか

### 3-4. 画面で確かめる不変条件

| 画面 | 確かめること |
|---|---|
| 記録 | 質問が 2 問だけ（感謝／気分・体調の一言）＋写真は任意。「まだ誰にも届いていません」が出ている |
| ホーム・振り返り | 連続日数（ストリーク）を出していない。週のカウントは「今週は○日できました」の肯定形で、0 日でも責める文でない |
| 今日の一言 | その日の本人の言葉を「」で引用している（汎用の励ましでない） |
| 振り返り・仕組みを見る | 病名・診断・医療的助言がない。受診勧奨は定型文だけ |
| 家族への共有・届いた便り・設定・家族 | 本人が「送る」まで届かない。家族の記録・記録日数を並べたり比べたりしていない |

### 3-5. 後片付け

```sh
kill %1 %2 2>/dev/null   # 起動した uvicorn / vite を止める（同じシェルで起動した場合）
rm -rf "$W"              # スクリーンショットを残すなら先に別の場所へ移す
```

## 4. 差分と不変条件の照合

`<不変条件>`（`AGENTS.md` の「仕様の不変条件」）を 1 項目ずつ、差分に関係するか確かめる。あわせて `AGENTS.md` の「変更時チェックリスト」を満たしているか確かめる。

- 解析・ルール（`analysis.py`・`rules.py`）を変えたら：別 DB で `seed_demo.py` を実行し、1〜7 日目は発動せず、9 日目に 2 ルール・10 日目に 3 ルールが発動することを出力で確かめる
- 受診勧奨の文言が `FIXED_CARE_PHRASE`（`analysis.py`）以外に増えていないか
- エンドポイント・環境変数・ガード・制約を変えたら `README.md` が更新されているか
- `.github/workflows/` に差分がないか

## 報告フォーマット（PR の「テスト・ビルド結果」にそのまま貼れる形）

```markdown
### 検品結果（verify-app）
- バックエンド：`unittest discover -s tests` → Ran <N> tests, OK (skipped=<M>)（期待値 68 / skip 1 との差：<理由>）
- フロント：`npm run build` → 成功（<警告があれば>）
- 画面：Playwright（Python）で <画面数> 画面 × 2 幅を撮影。コンソールエラー <件数>。保存先 `<パス>`
- 不変条件：<関係した項目と確認結果。関係しない項目は「該当なし」>
- 未実施：<あれば理由>
```
