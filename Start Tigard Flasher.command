#!/bin/sh
cd -- "$(dirname -- "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
    echo "Create .venv and install the package first; see README.md."
    exit 1
fi
exec .venv/bin/python launch.py "$@"
