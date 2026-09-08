#!/usr/bin/env bash
# scripts/smoke_010.sh — garde-fou CI robuste (010 P2-Robustesse, T043).
# Inspiré de autreprojet/python-tutor-main/scripts/smoke_*.sh :
# rebuild venv check, ports, goldens TSV/ICS — 100 % offline, sans démon.
set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$repo_root"

PORT="${EDUNEXUS_PORT:-9215}"
fail=0

say() { printf '%s\n' "$*"; }
ok() { say "ok - $*"; }
ko() { say "KO - $*"; fail=1; }

# 1) Interpréteur + venv : le venv local prime, sinon python3 système.
if [ -x ./venv/bin/python ]; then
  PY=./venv/bin/python
else
  PY=python3
fi
if "$PY" -c "import sys; assert sys.version_info >= (3, 11)" 2>/dev/null; then
  ok "python >= 3.11 ($PY)"
else
  ko "python >= 3.11 requis"
fi

# 2) Dépendances d'exécution présentes (aucune nouvelle requise par 010).
if "$PY" -c "import httpx, fastapi, pytest" 2>/dev/null; then
  ok "deps httpx/fastapi/pytest importables"
else
  ko "deps manquantes (httpx/fastapi/pytest)"
fi

# 3) Port applicatif libre (défaut 9215) — refuse si déjà occupé.
if command -v ss >/dev/null 2>&1; then
  if ss -ltn 2>/dev/null | grep -q ":${PORT} "; then
    ko "port ${PORT} déjà occupé"
  else
    ok "port ${PORT} libre"
  fi
else
  ok "port ${PORT} (ss absent, contrôle sauté)"
fi

# 4) Goldens TSV/ICS : régénérés et comparés à la référence embarquée.
GOLDEN_TSV=$("$PY" -c "
from src.ollama_tutor.tutor.exporters import flashcards_to_tsv
print(flashcards_to_tsv([{'question': '1/2 + 1/4 = ?', 'answer': '3/4'}]), end='')
")
if [ "$GOLDEN_TSV" = "$(printf '1/2 + 1/4 = ?\t3/4')" ]; then
  ok "golden TSV"
else
  ko "golden TSV (dérive d'export)"
fi
if "$PY" -c "
from src.ollama_tutor.tutor.exporters import reviews_to_ics
ics = reviews_to_ics([{'uid': 'g1', 'date': '20260910', 'summary': 'R'}])
assert ics.startswith('BEGIN:VCALENDAR') and ics.count('BEGIN:VEVENT') == 1
assert 'DTSTART;VALUE=DATE:20260910' in ics
"; then
  ok "golden ICS"
else
  ko "golden ICS (dérive d'export)"
fi

# 5) Contract test web : le garde-fou fetch() vs routes.
if "$PY" -m pytest tests/contract/test_web_contract.py -q -p no:warnings >/dev/null 2>&1; then
  ok "contract test web vert"
else
  ko "contract test web rouge"
fi

# 6) Robustesse P2 : suite ciblée.
if "$PY" -m pytest tests/unit/test_p2_robustness.py -q -p no:warnings >/dev/null 2>&1; then
  ok "tests P2-robustesse verts"
else
  ko "tests P2-robustesse rouges"
fi

if [ "$fail" -ne 0 ]; then
  say "smoke_010: ÉCHEC"
  exit 1
fi
say "smoke_010: OK"
