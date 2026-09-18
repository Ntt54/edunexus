"""Curriculum pack loader/validator (feature 012, T003, research D4).

Versioned JSON curriculum skeletons under ``src/ollama_tutor/data/packs/``
(titles / objectives / durations / prerequisites ONLY — no exam texts under
copyright). Envelope::

    {schemaVersion, packKey, packVersion, minAppVersion, source,
     statut, title, classe, examen, matieres: [...]}

Validation is stdlib-only (``json`` — Constitution V, no new runtime dep).
Shiped JSON files are NEVER mutated: :func:`upgrade_pack` performs the
v1 → v2 upgrade on an in-memory deep copy.

UI-framework-free by contract (no textual/fastapi imports).
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

#: Current envelope version written by new packs.
SCHEMA_VERSION = 2

#: Envelope versions accepted by the loader (v1 is upgraded in memory).
SUPPORTED_SCHEMA_VERSIONS = (1, 2)

#: Lifecycle of a pack (data-model.md): human OBC re-read flips
#: ``squelette_à_valider`` → ``validé`` (manual T031, never automatic).
PACK_STATUTS = ("squelette_à_valider", "validé")

#: BEPC / probatoire coefficients are unknown (no official table found in
#: open crawl): they stay ``null`` with this source marker — never invent.
SOURCE_UNCONFIRMED = "OBC/MINESEC à confirmer"

#: Link relations for atomic notes (kept here so packs + notes share one enum).
NOTE_LINK_RELS = ("précise", "contredit", "mécanisme-de", "exemple-de")

#: Weekly slot kinds for semester plans.
PLAN_SLOT_KINDS = ("révision", "simulation", "cours")

logger = logging.getLogger(__name__)


def packs_dir() -> Path:
    """Directory holding the shipped ``*.json`` curriculum packs."""
    return Path(__file__).resolve().parents[1] / "data" / "packs"


def list_pack_files(base: Path | None = None) -> list[Path]:
    """All shipped pack JSON files (recursive, sorted)."""
    root = base or packs_dir()
    if not root.is_dir():
        return []
    return sorted(root.rglob("*.json"))


def load_pack_file(path: Path | str) -> dict[str, Any]:
    """Load a pack file with stdlib ``json`` (raises on invalid JSON)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Pack {path} must be a JSON object")
    return raw


def pack_checksum(data: dict[str, Any]) -> str:
    """Stable sha256 over the canonical (sorted-keys) JSON encoding."""
    canonical = json.dumps(data, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade_pack(data: dict[str, Any]) -> dict[str, Any]:
    """Upgrade a v1 envelope to v2 on an in-memory copy (input untouched).

    v1 legacy forms: ``schema_version`` instead of ``schemaVersion``,
    ``version`` instead of ``packVersion``, missing ``minAppVersion`` /
    ``statut`` (defaulted). v2 input is deep-copied unchanged.
    Raises :class:`ValueError` on unsupported ``schemaVersion``.
    """
    upgraded = copy.deepcopy(data)
    if "schemaVersion" not in upgraded and "schema_version" in upgraded:
        upgraded["schemaVersion"] = upgraded.pop("schema_version")
    if "packVersion" not in upgraded and "version" in upgraded:
        upgraded["packVersion"] = upgraded.pop("version")
    version = upgraded.get("schemaVersion")
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValueError(f"Unsupported pack schemaVersion {version!r}")
    if version == 1:
        upgraded["schemaVersion"] = SCHEMA_VERSION
        upgraded.setdefault("minAppVersion", "0.1.0")
        upgraded.setdefault("statut", "squelette_à_valider")
    return upgraded


def _is_semver(value: Any) -> bool:
    parts = str(value or "").split(".")
    return len(parts) == 3 and all(p.isdigit() for p in parts)


def validate_pack(data: dict[str, Any]) -> list[str]:
    """Validate a pack envelope (v1 or v2). Returns error list ([] = valid)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["pack must be a JSON object"]
    version = data.get("schemaVersion", data.get("schema_version"))
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(f"schemaVersion must be one of {SUPPORTED_SCHEMA_VERSIONS}")
    for key in ("packKey", "title", "classe", "source"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            errors.append(f"{key} must be a non-empty string")
    pack_version = data.get("packVersion", data.get("version"))
    if not _is_semver(pack_version):
        errors.append("packVersion must be a X.Y.Z string")
    statut = data.get("statut")
    if statut is not None and statut not in PACK_STATUTS:
        errors.append(f"statut must be one of {PACK_STATUTS}")
    matieres = data.get("matieres")
    if not isinstance(matieres, list) or not matieres:
        errors.append("matieres must be a non-empty list")
        return errors
    seen: set[str] = set()
    for i, matiere in enumerate(matieres):
        where = f"matieres[{i}]"
        if not isinstance(matiere, dict):
            errors.append(f"{where} must be an object")
            continue
        titre = matiere.get("titre", matiere.get("title", ""))
        if not isinstance(titre, str) or not titre.strip():
            errors.append(f"{where}.titre must be a non-empty string")
        code = str(matiere.get("code", titre)).strip().lower()
        if code in seen:
            errors.append(f"{where}.code {code!r} duplicated")
        seen.add(code)
        coeff = matiere.get("coefficient")
        if coeff is not None and not (
            isinstance(coeff, (int, float)) and not isinstance(coeff, bool) and coeff > 0
        ):
            errors.append(f"{where}.coefficient must be null or a positive number")
        chapitres = matiere.get("chapitres")
        if not isinstance(chapitres, list) or not chapitres:
            errors.append(f"{where}.chapitres must be a non-empty list")
            continue
        for j, chapitre in enumerate(chapitres):
            cwhere = f"{where}.chapitres[{j}]"
            if not isinstance(chapitre, dict):
                errors.append(f"{cwhere} must be an object")
                continue
            ctitre = chapitre.get("titre", chapitre.get("title", ""))
            if not isinstance(ctitre, str) or not ctitre.strip():
                errors.append(f"{cwhere}.titre must be a non-empty string")
            objectifs = chapitre.get("objectifs", chapitre.get("objectives"))
            if (
                not isinstance(objectifs, list)
                or not objectifs
                or not all(isinstance(o, str) and o.strip() for o in objectifs)
            ):
                errors.append(f"{cwhere}.objectifs must be a non-empty string list")
            duree = chapitre.get("duree_min", chapitre.get("dureeMin"))
            if duree is not None and (
                isinstance(duree, bool) or not isinstance(duree, int) or duree <= 0
            ):
                errors.append(f"{cwhere}.duree_min must be a positive integer")
            prerequis = chapitre.get("prerequis", chapitre.get("prerequisites", []))
            if not isinstance(prerequis, list) or not all(
                isinstance(p, str) for p in prerequis
            ):
                errors.append(f"{cwhere}.prerequis must be a string list")
    return errors


def load_pack(key: str, base: Path | None = None) -> dict[str, Any]:
    """Load + validate + upgrade (in memory) the pack with this ``packKey``.

    Raises :class:`KeyError` when unknown, :class:`ValueError` when invalid.
    The shipped JSON file is never written to.
    """
    for path in list_pack_files(base):
        try:
            raw = load_pack_file(path)
        except (OSError, ValueError) as exc:
            # Log (don't swallow silently): a packKey match hidden behind an
            # unreadable file would otherwise surface as "Unknown pack".
            logger.warning("Skipping unreadable pack file %s: %s", path, exc)
            continue
        if str(raw.get("packKey", "")) == key:
            problems = validate_pack(raw)
            if problems:
                raise ValueError(f"Invalid pack {key}: {problems[0]}")
            return upgrade_pack(raw)
    raise KeyError(f"Unknown pack: {key}")


def list_pack_keys(base: Path | None = None) -> list[str]:
    """``packKey`` of every loadable shipped pack (invalid files skipped)."""
    keys: list[str] = []
    for path in list_pack_files(base):
        try:
            raw = load_pack_file(path)
        except (OSError, ValueError):
            continue
        if validate_pack(raw):
            continue
        keys.append(str(raw["packKey"]))
    return sorted(keys)


def iter_packs(base: Path | None = None) -> list[dict[str, Any]]:
    """All valid packs, upgraded in memory (never mutates shipped JSON)."""
    return [load_pack(key, base) for key in list_pack_keys(base)]
