"""Unit tests for gamification — learner profile, XP and streak (US15).

All tests run offline against a ``tmp_path`` config dir; no network, no daemon.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from src.ollama_tutor.tutor.store import LibraryStore


# ------------------------------------------------------------------
# T086 / T087 — add_xp
# ------------------------------------------------------------------

def test_add_xp_increments_total(tmp_path: Path) -> None:
    """Adding XP increases total_xp by the specified amount."""
    store = LibraryStore(tmp_path)
    profile = store.get_learner_profile()
    assert profile["total_xp"] == 0

    new_total = store.add_xp(15)
    assert new_total == 15

    new_total = store.add_xp(20)
    assert new_total == 35

    new_total = store.add_xp(10)
    assert new_total == 45
    store.close()


def test_add_xp_multiple_additions(tmp_path: Path) -> None:
    """XP accumulates correctly over many calls."""
    store = LibraryStore(tmp_path)
    for _ in range(10):
        store.add_xp(5)
    profile = store.get_learner_profile()
    assert profile["total_xp"] == 50
    store.close()


# ------------------------------------------------------------------
# T086 / T087 — update_streak
# ------------------------------------------------------------------

def test_update_streak_first_activity(tmp_path: Path) -> None:
    """First-ever activity sets streak to 1."""
    store = LibraryStore(tmp_path)
    streak = store.update_streak()
    assert streak == 1
    store.close()


def test_update_streak_same_day_no_change(tmp_path: Path) -> None:
    """Calling update_streak twice on the same day keeps streak at 1."""
    store = LibraryStore(tmp_path)
    streak1 = store.update_streak()
    streak2 = store.update_streak()
    assert streak1 == 1
    assert streak2 == 1
    store.close()


def test_update_streak_consecutive_days(tmp_path: Path) -> None:
    """Streak increments by 1 for each consecutive day."""
    store = LibraryStore(tmp_path)

    # Day 1: streak = 1
    fake_today = date(2025, 6, 10)
    with patch("src.ollama_tutor.tutor.store.date") as mock_date:
        mock_date.today.return_value = fake_today
        mock_date.fromisoformat = date.fromisoformat
        streak = store.update_streak()
    assert streak == 1

    # Day 2: streak = 2
    fake_today2 = date(2025, 6, 11)
    with patch("src.ollama_tutor.tutor.store.date") as mock_date:
        mock_date.today.return_value = fake_today2
        mock_date.fromisoformat = date.fromisoformat
        streak = store.update_streak()
    assert streak == 2

    # Day 3: streak = 3
    fake_today3 = date(2025, 6, 12)
    with patch("src.ollama_tutor.tutor.store.date") as mock_date:
        mock_date.today.return_value = fake_today3
        mock_date.fromisoformat = date.fromisoformat
        streak = store.update_streak()
    assert streak == 3

    store.close()


def test_update_streak_resets_on_gap(tmp_path: Path) -> None:
    """Streak resets to 1 when a day is missed."""
    store = LibraryStore(tmp_path)

    # Day 1
    fake_today = date(2025, 6, 10)
    with patch("src.ollama_tutor.tutor.store.date") as mock_date:
        mock_date.today.return_value = fake_today
        mock_date.fromisoformat = date.fromisoformat
        streak = store.update_streak()
    assert streak == 1

    # Day 2
    fake_today2 = date(2025, 6, 11)
    with patch("src.ollama_tutor.tutor.store.date") as mock_date:
        mock_date.today.return_value = fake_today2
        mock_date.fromisoformat = date.fromisoformat
        streak = store.update_streak()
    assert streak == 2

    # Skip a day — Day 4
    fake_today3 = date(2025, 6, 13)
    with patch("src.ollama_tutor.tutor.store.date") as mock_date:
        mock_date.today.return_value = fake_today3
        mock_date.fromisoformat = date.fromisoformat
        streak = store.update_streak()
    assert streak == 1  # reset

    store.close()


def test_update_streak_longest_tracked(tmp_path: Path) -> None:
    """longest_streak is updated when the current streak exceeds it."""
    store = LibraryStore(tmp_path)

    # Day 1: streak=1, longest=1
    fake_today = date(2025, 6, 10)
    with patch("src.ollama_tutor.tutor.store.date") as mock_date:
        mock_date.today.return_value = fake_today
        mock_date.fromisoformat = date.fromisoformat
        store.update_streak()

    # Day 2: streak=2, longest=2
    fake_today2 = date(2025, 6, 11)
    with patch("src.ollama_tutor.tutor.store.date") as mock_date:
        mock_date.today.return_value = fake_today2
        mock_date.fromisoformat = date.fromisoformat
        store.update_streak()

    # Gap — Day 4: streak=1, longest stays 2
    fake_today3 = date(2025, 6, 13)
    with patch("src.ollama_tutor.tutor.store.date") as mock_date:
        mock_date.today.return_value = fake_today3
        mock_date.fromisoformat = date.fromisoformat
        store.update_streak()

    profile = store.get_learner_profile()
    assert profile["current_streak"] == 1
    assert profile["longest_streak"] == 2
    store.close()


# ------------------------------------------------------------------
# T087 — get_learner_profile
# ------------------------------------------------------------------

def test_get_learner_profile_initial(tmp_path: Path) -> None:
    """Initial profile has zero XP, zero streak, empty badges."""
    store = LibraryStore(tmp_path)
    profile = store.get_learner_profile()
    assert profile["total_xp"] == 0
    assert profile["current_streak"] == 0
    assert profile["longest_streak"] == 0
    assert profile["badges"] == []
    assert "id" in profile
    store.close()


def test_get_learner_profile_after_activity(tmp_path: Path) -> None:
    """Profile reflects XP and streak after activity."""
    store = LibraryStore(tmp_path)
    store.add_xp(30)
    store.update_streak()
    store.add_xp(20)

    profile = store.get_learner_profile()
    assert profile["total_xp"] == 50
    assert profile["current_streak"] == 1
    assert "id" in profile
    assert "last_active_date" in profile
    store.close()


def test_get_learner_profile_returns_badges(tmp_path: Path) -> None:
    """badges_json is deserialized to a list in the profile dict."""
    store = LibraryStore(tmp_path)
    profile = store.get_learner_profile()
    assert isinstance(profile["badges"], list)
    # badges_json key is NOT in the profile (replaced by "badges")
    assert "badges_json" not in profile
    store.close()
