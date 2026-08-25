#!/usr/bin/env bash
set -euo pipefail

# ============================================================================
# EduNexus benchmark — throughput comparison vs the `ollama` CLI.
#
# Methodology (adapted from the original ollama-tui benchmark.sh):
#   1. `ollama run <model> <prompt>`  → wall-clock ms (CLI reference)
#   2. direct /api/chat (stream:false) → tokens, eval time, tok/s
#   3. EduNexus local API on http://127.0.0.1:${PORT:-9215}
#      - models-listing round-trip latency (always available)
#      - one exam question generated through the tutor API when a subject
#        already exists (end-to-end LLM leg; skipped otherwise)
#
# NOTE: this gate requires a LIVE Ollama daemon and a pulled model — it
# cannot run in CI or fully offline. It is a manual regression gate for the
# "throughput parity with `ollama run`" rule.
#
# Usage: MODEL=gemma4:e4b ./benchmark.sh [model] [prompt]
# ============================================================================

MODEL="${1:-${MODEL:-gemma4:e4b}}"
PROMPT="${2:-Explain Docker in 3 sentences}"
URL="${OLLAMA_URL:-http://localhost:11434}"
PORT="${PORT:-9215}"
EDUNEXUS="http://127.0.0.1:${PORT}"

echo "========================================="
echo "  EduNexus benchmark"
echo "========================================="
echo "Modèle       : $MODEL"
echo "Prompt       : $PROMPT"
echo "Ollama       : $URL"
echo "EduNexus API : $EDUNEXUS"
echo ""

# ---------------------------------------------------------------------------
# Preflight: Ollama must be reachable BEFORE anything else. Degrade cleanly:
# no partial output, non-zero exit.
# ---------------------------------------------------------------------------
if ! curl -sf -m 5 "$URL" > /dev/null 2>&1; then
    echo "Ollama introuvable — lance 'ollama serve' et vérifie le modèle" >&2
    echo "(URL testée : $URL ; modèle attendu : $MODEL)" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Ensure the EduNexus server answers on \$PORT; start it otherwise.
# ---------------------------------------------------------------------------
SERVER_PID=""
cleanup() {
    if [ -n "$SERVER_PID" ] && kill -0 "$SERVER_PID" 2>/dev/null; then
        kill "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
}
trap cleanup EXIT

if curl -sf -m 3 "$EDUNEXUS/api/tutor/engine" > /dev/null 2>&1; then
    echo "EduNexus répond déjà sur $EDUNEXUS — réutilisé."
else
    # Interpreter resolution: \$PYTHON env wins; otherwise probe the usual
    # candidates and keep the FIRST that can import ollama_tutor (the
    # package may be editable-installed in a sibling project's venv).
    PYTHON_BIN=""
    for candidate in "${PYTHON:-}" venv/bin/python "$(command -v python3 || true)" "$(command -v python || true)"; do
        [ -n "$candidate" ] || continue
        if "$candidate" -c "import ollama_tutor" > /dev/null 2>&1; then
            PYTHON_BIN="$candidate"
            break
        fi
    done
    if [ -z "$PYTHON_BIN" ]; then
        echo "Le paquet 'ollama_tutor' n'est importable par aucun interpréteur trouvé." >&2
        echo "Installe-le avec : pip install -e .[web]   (ou relance avec PYTHON=/chemin/vers/python)" >&2
        exit 1
    fi
    echo "Démarrage d'EduNexus sur le port $PORT…"
    # -u : logs non bufferisés (diagnostic si le serveur meurt en cours de run).
    "$PYTHON_BIN" -u -m ollama_tutor.web.__main__ --port "$PORT" \
        > /tmp/edunexus-benchmark-server.log 2>&1 &
    SERVER_PID=$!
    # Wait for the tutor API to answer (up to ~15s). We probe
    # /api/tutor/engine — a cheap registered route with no LLM call.
    for _ in $(seq 1 60); do
        if curl -sf -m 2 "$EDUNEXUS/api/tutor/engine" > /dev/null 2>&1; then
            break
        fi
        if ! kill -0 "$SERVER_PID" 2>/dev/null; then
            echo "EduNexus n'a pas démarré — voir /tmp/edunexus-benchmark-server.log" >&2
            exit 1
        fi
        sleep 0.25
    done
    if ! curl -sf -m 3 "$EDUNEXUS/api/tutor/engine" > /dev/null 2>&1; then
        echo "EduNexus injoignable sur $EDUNEXUS après démarrage." >&2
        exit 1
    fi
fi
echo ""

# ---------------------------------------------------------------------------
# Leg 1 — ollama CLI (wall clock).
# ---------------------------------------------------------------------------
echo "Benchmark 'ollama run' (CLI)…"
CLI_START=$(date +%s%N)
CLI_OUTPUT=$(ollama run "$MODEL" "$PROMPT" 2>&1)
CLI_END=$(date +%s%N)
CLI_MS=$(( (CLI_END - CLI_START) / 1000000 ))

# ---------------------------------------------------------------------------
# Leg 2 — direct Ollama API (tokens + tok/s).
# ---------------------------------------------------------------------------
echo "Benchmark API Ollama directe…"
API_START=$(date +%s%N)
API_STATS=$(curl -s -m 300 "$URL/api/chat" \
    -d "{
        \"model\": \"$MODEL\",
        \"messages\": [{\"role\": \"user\", \"content\": \"$PROMPT\"}],
        \"stream\": false
    }" | python3 -c "
import sys, json
data = json.load(sys.stdin)
eval_count = data.get('eval_count', 0)
eval_duration = data.get('eval_duration', 0) / 1e9
total = data.get('total_duration', 0) / 1e9
speed = eval_count / eval_duration if eval_duration > 0 else 0
print(f'{eval_count} {eval_duration:.2f} {total:.2f} {speed:.2f}')
")
API_END=$(date +%s%N)
API_MS=$(( (API_END - API_START) / 1000000 ))
read -r TOKENS EVAL_S TOTAL_S SPEED <<< "$API_STATS"

# ---------------------------------------------------------------------------
# Leg 3 — EduNexus local API.
# ---------------------------------------------------------------------------
echo "Benchmark API EduNexus ($EDUNEXUS)…"

ensure_server_alive() {
    if ! curl -sf -m 5 "$EDUNEXUS/api/tutor/engine" > /dev/null 2>&1; then
        echo "EduNexus a cessé de répondre pendant le benchmark." >&2
        echo "Voir /tmp/edunexus-benchmark-server.log (mémoire insuffisante ?)." >&2
        exit 1
    fi
}

ensure_server_alive
MODELS_MS_LIST=()
for _ in 1 2 3; do
    M_START=$(date +%s%N)
    curl -sf -m 10 "$EDUNEXUS/api/tutor/models" > /dev/null || {
        echo "Requête /api/tutor/models échouée." >&2
        exit 1
    }
    M_END=$(date +%s%N)
    MODELS_MS_LIST+=( $(( (M_END - M_START) / 1000000 )) )
done
MODELS_AVG=$(( (MODELS_MS_LIST[0] + MODELS_MS_LIST[1] + MODELS_MS_LIST[2]) / 3 ))

EXAM_LINE="(aucun sujet existant — mesure sautée)"
SUBJECT_ID=$(curl -sf -m 5 "$EDUNEXUS/api/tutor/subjects" \
    | python3 -c "
import sys, json
subjects = json.load(sys.stdin).get('subjects', [])
print(subjects[0]['id'] if subjects else '')
" 2>/dev/null || true)
if [ -n "$SUBJECT_ID" ]; then
    CONCEPT_OK=$(curl -sf -m 5 -X POST \
        "$EDUNEXUS/api/tutor/subjects/$SUBJECT_ID/concepts" \
        -H 'Content-Type: application/json' \
        -d '{"name": "__benchmark__"}' > /dev/null 2>&1 && echo yes || echo no)
    if [ "$CONCEPT_OK" = "yes" ]; then
        EXAM_START=$(date +%s%N)
        curl -sf -m 300 -X POST \
            "$EDUNEXUS/api/tutor/subjects/$SUBJECT_ID/exams" \
            -H 'Content-Type: application/json' \
            -d '{"size": 1, "time_limit_s": 600}' > /dev/null || {
            echo "Génération via l'API tuteur échouée (sujet $SUBJECT_ID)." >&2
            exit 1
        }
        EXAM_END=$(date +%s%N)
        EXAM_MS=$(( (EXAM_END - EXAM_START) / 1000000 ))
        EXAM_LINE="${EXAM_MS}ms (génération d'une question via l'API tuteur)"
    fi
fi

# ---------------------------------------------------------------------------
# Results.
# ---------------------------------------------------------------------------
echo ""
echo "========================================="
echo "  Résultats"
echo "========================================="
echo ""
echo "ollama CLI ('ollama run') :"
echo "  Temps mur : ${CLI_MS}ms"
echo ""
echo "API Ollama directe (/api/chat) :"
echo "  Tokens        : $TOKENS"
echo "  Temps d'éval  : ${EVAL_S}s"
echo "  Temps total   : ${TOTAL_S}s"
echo "  Vitesse       : $SPEED tok/s"
echo "  Temps mur     : ${API_MS}ms"
echo ""
echo "API EduNexus locale ($EDUNEXUS) :"
echo "  Latence /api/tutor/models (moy. 3 essais) : ${MODELS_AVG}ms"
echo "  Génération via le tuteur                  : $EXAM_LINE"
echo ""
echo "Note : pour une comparaison fine, relance avec le même modèle et le"
echo "même prompt ; la parité de débit avec 'ollama run' est la règle."
echo "========================================="
