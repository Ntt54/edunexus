# Data Model — 010-greffe-autreprojet (Phase 1)

## E-001 — `SafetyEvent` / `SafetyReport` (P0-A, `tutor/safety.py`)

| Champ | Type | Règles |
|---|---|---|
| `type` | `blocked_import \| blocked_call \| syntax_error` | énum fermée |
| `detail` | `str` | nom du module / appel / message parser |
| `lineno` | `int \| None` | ligne source si connue |
| `SafetyReport.blocked` | `bool` | `True` ⇒ exécution refusée avant subprocess |
| `SafetyReport.events` | `list[SafetyEvent]` | `syntax_error` ne bloque jamais (le runner retourne le diagnostic parser) |

## E-002 — `RunResult` (P0-A, `tutor/sandbox.py`)

| Champ | Type | Règles |
|---|---|---|
| `stdout` / `stderr` | `str` | tronqués à `MAX_OUTPUT_BYTES` (défaut 32 Ko) + marqueur `... [truncated]` |
| `exit_code` | `int` | `-1` si bloqué/timeout sans code |
| `duration_ms` | `int` | temps mural mesuré |
| `timed_out` | `bool` | `True` ⇒ `stderr` suffixé `[runner] killed after Xs timeout` |
| `truncated` | `bool` | une des deux sorties a été coupée |
| `blocked` | `bool` | `True` ⇒ `exit_code == -1`, `duration_ms == 0` |
| `safety_events` | `list[dict]` | payload sérialisable des événements |

Validation : `code` doit être `str`, ≤ `MAX_CODE_BYTES` (50 Ko) sinon `RunnerError`
(erreur appelant, pas échec élève). `timeout` borné [0.5, 30.0] s.

## E-003 — `Exercise` / `GradeResult` (P0-A, extension notation)

`Exercise` : `id, title, section, concepts[], prompt, starter_code,
visible_tests[], hidden_tests[], references[]` (URLs filtrées par allowlist
`DEFAULT_ALLOWED_HOSTS` de `docs_refs.py`, rejets loggés). Notation :
`TestOutcome {expr, passed, error | None}`,
`GradeResult {run: RunResult, visible: list[TestOutcome],
hidden: list[TestOutcome], all_passed}` — le harnais `_HARNESS_TEMPLATE`
concatène soumission + tests, exécute, parse le marqueur `__TUTOR_HARNESS__`
(JSON sur stdout). Le verdict pédagogique
`AttemptResult` (`correct|partial|incorrect`, INVARIANT 3) est conservé et
**enrichi** de la preuve d'exécution, jamais remplacé par elle.

## E-004 — `LLMClient` / `ProviderRegistry` (P0-B, `tutor/providers/registry.py`)

- `LLMClient` (ABC) : `name`, `is_healthy: bool`, `chat(messages, …)`,
  `ping() -> bool`, compteurs de tokens pour observabilité.
- `ProviderRegistry` : `register(name, client, primary=False)`,
  `register_variant(hint, client)` (`large|small|fast`), `get(name?) -> LLMClient`
  (chaîne : variante → demandée → primaire → ordre de fallback, premier sain),
  `ping_all() -> dict[name, ok]`, `LLMConfigurationError` si registre vide.
- Circuit breaker : `CIRCUIT_OPEN_THRESHOLD`, `CIRCUIT_RESET_TIMEOUT`,
  `COOLDOWN_STEPS` (progressif) — un provider en faute est écarté puis retesté.

## E-005 — `EmbeddingProvider` (P0-B)

`embed(text) -> vec`, `embed_batch(texts) -> vecs`, `dimension: int`
(auto-détectée, persistée ; changement ⇒ ré-indexation).
`NoOpEmbeddingProvider` (vecteurs nuls, `skip`), `FallbackEmbeddingProvider`
(chaîne primaire → secours avec log). Mode global `auto|eager|skip`
(config, défaut `auto`).

## E-006 — `IngestionJob` (P1-A, table `ingestion_jobs`)

| Champ | Type | Règles |
|---|---|---|
| `id` | `str` (uuid) | PK |
| `source_type` | `file \| url \| canvas` | |
| `original_filename` / `url` | `str \| None` | l'un requis selon source |
| `content_hash` | `str` | dedup (sha256 stdlib ; xxhash si déjà dispo, sinon non) |
| `status` | `uploaded \| extracting \| classifying \| dispatching \| embedding \| completed \| failed` | transitions avant uniquement (+ `failed` depuis tout état) |
| `progress_percent` | `int` | 0–100, monotone par job |
| `phase_label` | `str` | libellé FR affichable |
| `embedding_status` | `pending \| running \| done \| skipped \| failed` | reflète le mode d'embedding |
| `nodes_created` | `int` | chunks/nœuds créés (dispatch) |
| `error_message` | `str \| None` | renseigné si `failed` (principe VI) |
| `created_at` / `updated_at` | ISO-8601 | |

Relations : `IngestionJob 1—* chunks` (via `book_id` existant après dispatch) ;
pas de FK dure vers `books` avant dispatch (le job peut échouer avant création).

## E-007 — Carte de contrat web (P1-B, test uniquement)

`EndpointUsage { method, path, source_file, lineno }` extrait des `fetch()` ;
`RouteDecl { method, path }` extrait de `web/server.py` (préfixe `/tutor`
normalisé). Règle : tout `EndpointUsage` doit matcher une `RouteDecl`
(méthode + chemin template, segments `{param}` joker) ; rapport des orphelins
dans l'assert du test.
