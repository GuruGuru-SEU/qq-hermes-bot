#!/bin/sh
set -eu
python /opt/qq-hermes-bot/deploy/bootstrap.py
HERMES_WRITE_SAFE_ROOT="$(python -m qqhermes.runtime)"
export HERMES_WRITE_SAFE_ROOT
exec /opt/hermes/.venv/bin/hermes "$@"
