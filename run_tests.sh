#!/usr/bin/env bash
# Runs the QED Chess test suite with the project venv.
set -uo pipefail
cd "$(dirname "$0")"
exec .venv/bin/python -m pytest -q "$@"
