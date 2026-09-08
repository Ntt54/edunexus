# Contract — Sandbox Python (P0-A)

## `tutor/safety.py`

```python
def analyze(code: str) -> SafetyReport   # ne lève jamais
```

- Entrée : code source Python (`str`).
- Sortie : `SafetyReport(blocked: bool, events: list[SafetyEvent])`.
- Garanties : `SyntaxError` → événement `syntax_error`, `blocked=False` ;
  import/appel hostile → `blocked=True` ; `TUTOR_STRICT_IMPORTS=1` bloque
  aussi `WARN_MODULES` + `open()`.

## `tutor/sandbox.py`

```python
async def run_python(code: str, *, stdin: str = "",
                     timeout: float | None = None,
                     python_executable: str | None = None,
                     skip_safety: bool = False) -> RunResult
```

- Ne lève jamais sur échec élève (syntaxe, timeout, exit ≠ 0, sortie volumineuse,
  code bloqué) : tout est encodé dans `RunResult`.
- Lève `RunnerError` uniquement sur faute d'appelant (code non-`str`,
  > `MAX_CODE_BYTES`).
- `skip_safety=True` réservé aux appelants internes ayant déjà validé le code
  (ex. tests cachés) — **jamais exposé au réseau**.
- Variables d'env : `TUTOR_RUN_TIMEOUT` [0.5, 30.0], `TUTOR_RUN_MAX_CODE_BYTES`,
  `TUTOR_RUN_MAX_OUTPUT_BYTES`, `TUTOR_RUN_CPU_SECONDS/MEM_MB/FSIZE_MB/NPROC`.

## Branchement `grade_answer` (FR-020)

`assessment.py` / `TutorService.grade_answer` accepte un paramètre optionnel
`execution: RunResult | None` (défaut `None` → comportement actuel inchangé).
Quand fourni, le prompt de notation inclut stdout/stderr/exit et le feedback
cite l'output réel. `AttemptResult` inchangé (INVARIANT 3).
