#!/usr/bin/env bash
# Tắt rồi bật lại cả hai. Dùng: ./restart.sh
ROOT="$(cd "$(dirname "$0")" && pwd)"
"$ROOT/stop.sh"
sleep 2
"$ROOT/start.sh"
