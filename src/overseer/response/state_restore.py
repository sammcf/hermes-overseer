"""Post-red restore audit: classify files and group them by restore category."""

from __future__ import annotations

_CLASSIFICATIONS: dict[str, str] = {
    "state.db": "safe",
    "SOUL.md": "audit",
    ".env": "skip",
    "config.yaml": "canonical",
}

_AUDIT_PATTERNS = ("memories/",)


def classify_file_for_restore(file_path: str) -> str:
    """Classify a file path into a restore category.

    Categories:
    - "safe"      — state.db: can be restored automatically
    - "audit"     — memories/, SOUL.md: requires human review before restore
    - "canonical" — config.yaml: restore from canonical source only
    - "skip"      — .env: never restore automatically
    - "unknown"   — anything else

    Matching is done on the basename and known path prefixes.
    """
    basename = file_path.split("/")[-1]

    if basename in _CLASSIFICATIONS:
        return _CLASSIFICATIONS[basename]

    for pattern in _AUDIT_PATTERNS:
        if pattern in file_path:
            return "audit"

    return "unknown"


def audit_restore_plan(files: list[str]) -> dict[str, list[str]]:
    """Group files by their restore classification.

    Returns a dict with keys: "safe", "audit", "canonical", "skip", "unknown".
    Each value is a (possibly empty) list of file paths in that category.
    """
    plan: dict[str, list[str]] = {
        "safe": [],
        "audit": [],
        "canonical": [],
        "skip": [],
        "unknown": [],
    }
    for f in files:
        category = classify_file_for_restore(f)
        plan[category].append(f)
    return plan
