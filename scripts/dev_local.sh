#!/usr/bin/env bash
# 本地开发：读取 .env.dev（不入库），用与线上相同的 gunicorn 参数启动
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env.dev; set +a
exec .venv/bin/gunicorn --workers 1 --threads 16 --timeout 300 --bind 127.0.0.1:${PORT:-49193} "ai_chat_site.wsgi:app"
