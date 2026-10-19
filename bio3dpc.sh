#!/bin/sh
# Resolve the checkout independently of the caller's working directory.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ ! -x "$root/.venv/bin/python" ]; then
    printf '%s\n' 'BioShock 3D: create the project .venv first (see README.md).' >&2
    exit 1
fi
cd "$root"
exec "$root/.venv/bin/python" -m bio3dpc "$@"
