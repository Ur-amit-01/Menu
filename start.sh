#!/bin/sh

gunicorn app:app --bind 0.0.0.0:${PORT:-8080} &
GUNICORN_PID=$!

python bot.py &
BOT_PID=$!

trap 'kill -TERM $GUNICORN_PID $BOT_PID 2>/dev/null; wait $GUNICORN_PID $BOT_PID' TERM INT

wait $GUNICORN_PID $BOT_PID
