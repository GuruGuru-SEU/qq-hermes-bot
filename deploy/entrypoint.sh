#!/bin/sh
set -eu
python /opt/qq-hermes-bot/deploy/bootstrap.py
exec /opt/hermes/.venv/bin/hermes "$@"
