#!/usr/bin/env bash
# Khởi động Backend (8088) + Frontend (3001) chạy nền.
# Dùng: ./start.sh
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"

# ── Backend ──
cd "$ROOT/backend"
if lsof -ti :8088 >/dev/null 2>&1; then
  echo "⚠️  Backend đã chạy ở cổng 8088 (bỏ qua)."
else
  mkdir -p logs
  nohup .venv/bin/uvicorn app.main:app --reload --reload-dir app --port 8088 --loop asyncio \
    > logs/backend.log 2>&1 &
  echo "✅ Backend khởi động → http://localhost:8088  (log: backend/logs/backend.log)"
fi

# ── Frontend ──
cd "$ROOT/frontend"
if lsof -ti :3001 >/dev/null 2>&1; then
  echo "⚠️  Frontend đã chạy ở cổng 3001 (bỏ qua)."
else
  mkdir -p logs
  nohup npm run dev > logs/frontend.log 2>&1 &
  echo "✅ Frontend khởi động → http://localhost:3001  (log: frontend/logs/frontend.log)"
fi

echo ""
echo "👉 Mở trình duyệt: http://localhost:3001   (đăng nhập: admin / 123456)"
