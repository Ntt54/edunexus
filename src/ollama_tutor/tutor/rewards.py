"""XP anti-grinding + serie hebdo avec joker (feature 012, T027, research D10).

``XP = base(difficulte) x decay(repetition <7j)`` aux points d'appel
existants (``TutorService.grade_answer`` / ``submit_answers``), cap
journalier, re-do du meme item le meme jour <= 25 %, serie hebdomadaire
avec 1 joker (persiste dans ``streak_freeze_json``), re-submit idempotent
d'un quiz deja ``completed`` -> 0 XP supplementaire (suivi G3).

100 % offline, stdlib uniquement, aucun appel LLM. L'historique de
repetition est lu depuis la table existante ``exercise_attempts``
(``created_at`` ISO) ; le cap journalier est suivi dans un registre
memoire du process (profil apprenant singleton) ; la serie hebdo est
persiste en best-effort dans ``learner_profile.streak_freeze_json``.

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

#: XP de base par difficulte (``medium`` = 15 = montant historique
#: d'un exercice reussi, preserve par T027).
BASE_XP: dict[str, int] = {"easy": 10, "medium": 15, "hard": 25}

#: XP de base pour un quiz/une epreuve termine(e) (montant historique).
QUIZ_BASE_XP = 20

#: Bonus de serie quand l'apprenant est actif plusieurs jours de suite
#: (montant historique du bonus de streak).
STREAK_BONUS_XP = 10

#: Plafond d'XP attribuable par jour civil (anti-grinding).
DAILY_XP_CAP = 200

#: Fenetre de decroissance : toute reussite du meme item dans les
#: 7 derniers jours divise le gain (recherche D10).
REPEAT_WINDOW_DAYS = 7

#: Plafond du multiplicateur pour un re-do du meme item le meme jour
#: (25 % max du XP de base).
SAME_DAY_REDO_MAX = 0.25

_DIFFICULTY_ALIASES = {
    "easy": "easy",
    "facile": "easy",
    "beginner": "easy",
    "medium": "medium",
    "moyen": "medium",
    "moyenne": "medium",
    "intermediate": "medium",
    "normal": "medium",
    "hard": "hard",
    "difficile": "hard",
    "advanced": "hard",
    "dûr": "hard",
}


def normalize_difficulty(value: Any) -> str:
    """Normalise une difficulte vers ``easy`` | ``medium`` | ``hard``.

    Toute valeur inconnue/vide retombe sur ``medium`` (comportement
    historique : +15 XP sans ponderation).
    """
    key = str(value or "").strip().lower()
    return _DIFFICULTY_ALIASES.get(key, "medium")


def base_xp(difficulty: Any) -> int:
    """XP de base pondere par la difficulte reelle (FR-012)."""
    return BASE_XP[normalize_difficulty(difficulty)]


def decay_factor(repetitions_7d: int) -> float:
    """Decroissance exponentielle : ``0.5 ** repetitions`` (<7j).

    0 repetition -> 1.0 (plein gain) ; 1 -> 0.5 ; 2 -> 0.25, etc.
    Les valeurs negatives sont saturees a 0 repetition.
    """
    try:
        n = int(repetitions_7d)
    except (TypeError, ValueError):
        n = 0
    return 0.5 ** max(0, n)


def exercise_multiplier(repetitions_7d: int = 0, same_day_redos: int = 0) -> float:
    """Multiplicateur anti-grinding pour un exercice reussi.

    - repetition d'un item deja reussi il y a <7j : ``decay_factor`` ;
    - re-do du meme item le meme jour : plafonne a 25 %
      (``SAME_DAY_REDO_MAX``), la decroissance continuant de s'appliquer
      en dessous du plafond.
    """
    try:
        redos = int(same_day_redos)
    except (TypeError, ValueError):
        redos = 0
    factor = decay_factor(repetitions_7d)
    if redos >= 1:
        factor = min(factor, SAME_DAY_REDO_MAX)
    return factor


def compute_exercise_xp(
    difficulty: Any,
    repetitions_7d: int = 0,
    same_day_redos: int = 0,
) -> int:
    """XP theorique (avant cap journalier) d'un exercice reussi."""
    amount = base_xp(difficulty) * exercise_multiplier(
        repetitions_7d, same_day_redos
    )
    return max(0, int(round(amount)))


def daily_grant(already_today: int, requested: int, cap: int = DAILY_XP_CAP) -> int:
    """Part de ``requested`` attribuable sans depasser le cap journalier."""
    try:
        have = max(0, int(already_today))
    except (TypeError, ValueError):
        have = 0
    try:
        want = max(0, int(requested))
    except (TypeError, ValueError):
        want = 0
    return max(0, min(want, max(0, int(cap) - have)))


# ---------------------------------------------------------------------------
# Registre journalier (memoire process ; profil apprenant singleton)
# ---------------------------------------------------------------------------

#: XP deja attribue par jour civil ``{"2026-09-18": 45}``.
_DAILY_AWARDED: dict[str, int] = {}


def reset_daily_ledger() -> None:
    """Vide le registre journalier (tests offline)."""
    _DAILY_AWARDED.clear()


def awarded_today(day: str | date | None = None) -> int:
    """XP deja attribue pour ``day`` (defaut : aujourd'hui)."""
    return _DAILY_AWARDED.get(_day_key(day), 0)


def _note_awarded(day: str, amount: int) -> None:
    _DAILY_AWARDED[day] = _DAILY_AWARDED.get(day, 0) + max(0, int(amount))


def _day_key(day: str | date | None = None) -> str:
    if isinstance(day, date):
        return day.isoformat()
    if isinstance(day, str) and day.strip():
        return day.strip()[:10]
    return date.today().isoformat()


def _coerce_day(day: str | date | None = None) -> date:
    if isinstance(day, date):
        return day
    if isinstance(day, str) and day.strip():
        try:
            return date.fromisoformat(day.strip()[:10])
        except ValueError:
            pass
    return date.today()


# ---------------------------------------------------------------------------
# Serie hebdomadaire + joker (semaines ISO ``"2026-W38"``)
# ---------------------------------------------------------------------------


def week_key(day: str | date | None = None) -> str:
    """Cle de semaine ISO (``YYYY-Www``) pour ``day`` (defaut : aujourd'hui)."""
    iso = _coerce_day(day).isocalendar()
    return f"{iso[0]:04d}-W{iso[1]:02d}"


def _valid_week(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 8:
        return False
    if value[4:6] != "-W" or not value[:4].isdigit() or not value[6:].isdigit():
        return False
    try:
        date.fromisocalendar(int(value[:4]), int(value[6:]), 1)
    except ValueError:
        return False
    return True


def _prev_week(key: str) -> str:
    monday = date.fromisocalendar(int(key[:4]), int(key[6:]), 1)
    return week_key(monday - timedelta(days=7))


def _week_run_length(weeks: list[str], current: str) -> int:
    known = set(weeks)
    run = 0
    cursor = current
    while cursor in known:
        run += 1
        cursor = _prev_week(cursor)
    return run


def _credited_run(weeks: list[str], frozen: str | None, current: str) -> int:
    """Longueur de serie en semaines ACTIVES, la semaine couverte par le
    joker (``frozen``) preservant la continuite sans incrementer."""
    known = set(weeks)
    run = 0
    cursor = current
    skipped = False
    while True:
        if cursor in known:
            run += 1
            cursor = _prev_week(cursor)
        elif frozen is not None and cursor == frozen and not skipped:
            skipped = True
            cursor = _prev_week(cursor)
        else:
            break
    return run


def parse_freeze_state(raw: Any) -> dict[str, Any]:
    """Etat ``{weeks, frozen_week}`` depuis ``streak_freeze_json`` (tolerant)."""
    state: dict[str, Any] = {"weeks": [], "frozen_week": None}
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str) and raw.strip():
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return state
        if not isinstance(data, dict):
            return state
    else:
        return state
    weeks = data.get("weeks")
    if isinstance(weeks, list):
        state["weeks"] = sorted({w for w in weeks if _valid_week(w)})[-12:]
    frozen = data.get("frozen_week")
    state["frozen_week"] = frozen if _valid_week(frozen) else None
    return state


def dump_freeze_state(state: dict[str, Any]) -> str:
    """Serialise l'etat hebdo (jamais d'exception)."""
    try:
        payload = {
            "weeks": [w for w in state.get("weeks", []) if _valid_week(w)][-12:],
            "frozen_week": state.get("frozen_week"),
        }
        if not _valid_week(payload["frozen_week"]):
            payload["frozen_week"] = None
        return json.dumps(payload)
    except (TypeError, ValueError):
        return '{"weeks": [], "frozen_week": null}'


def record_weekly_activity(
    state: dict[str, Any], current_week: str | None = None
) -> tuple[dict[str, Any], int, bool]:
    """Enregistre une activite pour ``current_week`` (defaut : cette semaine).

    Retourne ``(nouvel_etat, serie_hebdo, joker_consomme_maintenant)`` :

    - semaine deja connue -> serie inchangee (ni joker ni reset) ;
    - semaine consecutive -> serie +1 ;
    - exactement UNE semaine manquee et joker intact -> le joker couvre
      la semaine manquee (``frozen_week``), la serie continue ;
    - trou >= 2 semaines, ou trou d'1 semaine avec joker deja depense,
      ou semaine anterieure (retour horloge) -> reset a 1 (sans mutation
      dans le cas du retour horloge).
    """
    current: str = (
        current_week if isinstance(current_week, str) and _valid_week(current_week) else week_key()
    )
    weeks = sorted({w for w in state.get("weeks", []) if _valid_week(w)})[-12:]
    frozen = state.get("frozen_week") if _valid_week(state.get("frozen_week")) else None

    if current in weeks:
        kept = {"weeks": weeks, "frozen_week": frozen}
        return kept, _credited_run(weeks, frozen, current), False
    if weeks and current < weeks[-1]:
        # Retour en arriere (horloge/backdate) : ne casse jamais la serie.
        kept = {"weeks": weeks, "frozen_week": frozen}
        return kept, _credited_run(weeks, frozen, weeks[-1]), False

    if not weeks or weeks[-1] == _prev_week(current):
        weeks = (weeks + [current])[-12:]
        kept = {"weeks": weeks, "frozen_week": frozen}
        return kept, _credited_run(weeks, frozen, current), False

    missed = _prev_week(current)
    if weeks[-1] == _prev_week(missed) and frozen is None:
        weeks = (weeks + [current])[-12:]
        kept = {"weeks": weeks, "frozen_week": missed}
        return kept, _credited_run(weeks, missed, current), True

    kept = {"weeks": [current], "frozen_week": frozen}
    return kept, 1, False


# ---------------------------------------------------------------------------
# Acces store (duck-type ; SQL best-effort, jamais bloquant)
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def count_recent_correct_attempts(
    store: Any,
    exercise_id: str,
    *,
    days: int = REPEAT_WINDOW_DAYS,
    now: datetime | None = None,
    exclude_id: str | None = None,
    same_day: bool = False,
) -> int:
    """Nombre de reussites anterieures de ``exercise_id`` sur la fenetre.

    ``same_day=True`` restreint au jour civil courant (UTC). La tentative
    courante (``exclude_id``) est exclue du compte. Retourne 0 si la table
    est absente/injoignable (offline-safe).
    """
    moment = now if isinstance(now, datetime) else _utcnow()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    cutoff = (moment - timedelta(days=max(0, int(days)))).isoformat()
    sql = (
        "SELECT COUNT(*) AS n FROM exercise_attempts "
        "WHERE exercise_id = ? AND verdict = 'correct' AND created_at >= ?"
    )
    params: list[Any] = [exercise_id, cutoff]
    if same_day:
        day_start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
        sql += " AND created_at >= ?"
        params.append(day_start.isoformat())
    if exclude_id:
        sql += " AND id != ?"
        params.append(exclude_id)
    try:
        row = store._conn.execute(sql, params).fetchone()
    except Exception:
        return 0
    try:
        return max(0, int(row["n"]))
    except (TypeError, KeyError, IndexError, ValueError):
        return 0


def is_quiz_completed(store: Any, quiz_id: str) -> bool:
    """Vrai si le quiz/epreuve est deja ``completed`` (garde G3)."""
    try:
        row = store._conn.execute(
            "SELECT status FROM quizzes WHERE id = ?", (quiz_id,)
        ).fetchone()
    except Exception:
        return False
    if row is None:
        return False
    try:
        return str(row["status"]) == "completed"
    except (KeyError, IndexError, TypeError):
        return False


def _read_freeze_state(store: Any) -> dict[str, Any]:
    try:
        profile = store.get_learner_profile()
    except Exception:
        return {"weeks": [], "frozen_week": None}
    if not isinstance(profile, dict):
        return {"weeks": [], "frozen_week": None}
    return parse_freeze_state(profile.get("streak_freeze_json"))


def _write_freeze_state(store: Any, state: dict[str, Any]) -> None:
    try:
        store._conn.execute(
            "UPDATE learner_profile SET streak_freeze_json = ? WHERE id = ?",
            (dump_freeze_state(state), "default"),
        )
        store._conn.commit()
    except Exception:
        # Colonne absente (base anterieure a 012/T004) : la serie hebdo
        # reste calculee en memoire pour l'appel courant.
        pass


def record_weekly_activity_for_store(
    store: Any, today: str | date | None = None
) -> tuple[int, bool]:
    """Serie hebdo persistee (best-effort) -> ``(serie, joker_consomme)``."""
    current = week_key(today)
    state = _read_freeze_state(store)
    new_state, streak, consumed = record_weekly_activity(state, current)
    _write_freeze_state(store, new_state)
    return streak, consumed


def award_exercise_xp(
    store: Any,
    *,
    exercise_id: str,
    difficulty: Any = "medium",
    attempt_id: str | None = None,
    today: str | date | None = None,
    now: datetime | None = None,
) -> int:
    """Attribue l'XP d'un exercice reussi (cap + decay + re-do <=25 %).

    Retourne le montant effectivement attribue (0 si cap atteint ou gain
    nul). Ne leve jamais pour une cause de lecture (comptes a 0).
    """
    moment = now if isinstance(now, datetime) else _utcnow()
    repetitions = count_recent_correct_attempts(
        store, exercise_id, now=moment, exclude_id=attempt_id
    )
    redos = count_recent_correct_attempts(
        store, exercise_id, now=moment, exclude_id=attempt_id, same_day=True
    )
    theoretical = compute_exercise_xp(difficulty, repetitions, redos)
    day = _day_key(today)
    granted = daily_grant(awarded_today(day), theoretical)
    if granted <= 0:
        return 0
    try:
        store.add_xp(granted)
    except Exception:
        return 0
    _note_awarded(day, granted)
    return granted


def award_quiz_xp(
    store: Any, *, today: str | date | None = None
) -> dict[str, Any]:
    """Attribue l'XP d'un quiz/epreuve termine(e) (cap + bonus de serie).

    +20 XP de base, +10 de bonus si la serie quotidienne depasse 1 jour
    (montants historiques US15/T088), le tout sous cap journalier. La
    serie hebdo est enregistree en best-effort. Retourne
    ``{xp, streak, bonus, week_streak, freeze_used}``.
    """
    try:
        streak = int(store.update_streak())
    except Exception:
        streak = 0
    bonus = STREAK_BONUS_XP if streak > 1 else 0
    try:
        week_streak, freeze_used = record_weekly_activity_for_store(store, today)
    except Exception:
        week_streak, freeze_used = 0, False
    day = _day_key(today)
    granted = daily_grant(awarded_today(day), QUIZ_BASE_XP + bonus)
    if granted > 0:
        try:
            store.add_xp(granted)
        except Exception:
            granted = 0
        else:
            _note_awarded(day, granted)
    return {
        "xp": granted,
        "streak": streak,
        "bonus": bonus,
        "week_streak": week_streak,
        "freeze_used": freeze_used,
    }
