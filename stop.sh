#!/usr/bin/env bash
# Tắt Backend (8088) + Frontend (3001).
# Dùng: ./stop.sh
echo "⏹  Đang tắt..."

for PORT in 8088 3001; do
  PIDS=$(lsof -ti :$PORT 2>/dev/null)
  if [ -n "$PIDS" ]; then
    kill $PIDS 2>/dev/null
    echo "   • Đã tắt cổng $PORT (pid: $PIDS)"
  else
    echo "   • Cổng $PORT không có gì chạy"
  fi
done

# Dọn tiến trình con còn sót (uvicorn reloader, next, chromium của crawler)
pkill -f "uvicorn app.main:app" 2>/dev/null
pkill -f "next dev -p 3001" 2>/dev/null
echo "✅ Đã tắt xong."
