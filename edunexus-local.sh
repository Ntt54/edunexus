#!/usr/bin/env bash
# Lance EduNexus avec les données du projet (./data), où que soit le shell.
# Usage : ./edunexus-local.sh  (puis ouvrir http://127.0.0.1:9215/tutor)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
export EDUNEXUS_DATA_DIR="$ROOT/data"
exec /home/nganso/.local/bin/edunexus "$@"
