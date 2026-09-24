#!/bin/sh
set -eu

PORT="${PORT:-49193}"

exec gunicorn \
  --workers 1 \
  --threads 16 \
  --timeout 300 \
  --access-logfile - \
  --access-logformat '%(t)s %({cf-connecting-ip}i)s "%(r)s" %(s)s %(B)sB %(M)sms "%(a)s"' \
  --bind "0.0.0.0:${PORT}" \
  "ai_chat_site.wsgi:app"

