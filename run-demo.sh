#!/usr/bin/env bash
# きょうのかがみ デモ起動スクリプト
# 使い方: ./run-demo.sh  → http://localhost:5173 を開く
# デモログイン: haha@demo.local / demo-mama-2026（母）、musume@demo.local / demo-musume-2026（娘）
set -e
cd "$(dirname "$0")"

cd backend
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q -r requirements.txt
if [ ! -f data/app.db ]; then
  .venv/bin/python scripts/seed_demo.py --reset --with-letter
fi
.venv/bin/uvicorn app.main:app --port 8000 &
BACK=$!
cd ..

cd frontend
[ -d node_modules ] || npm install
npm run dev -- --port 5173 &
FRONT=$!
cd ..

trap 'kill $BACK $FRONT 2>/dev/null || true' EXIT
echo ""
echo "==> http://localhost:5173 を開いてください（止めるときは Ctrl+C）"
wait
