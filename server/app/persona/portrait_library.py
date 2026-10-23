"""Versioned art library with scene matching and stable, non-repeating rotation."""
from __future__ import annotations

import json
import random
from datetime import datetime
from pathlib import Path


def library_version(root: Path) -> str | None:
    return next((version for version in ("v3", "v2")
                 if (root / version / "manifest.json").is_file()), None)


def choose_asset(root: Path, presentation: str, category: str, previous, now: datetime,
                 refresh_seconds: int) -> dict:
    version = library_version(root)
    if version is None or presentation not in ("female", "male"):
        return {}
    try:
        manifest = json.loads((root / version / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    items = []
    for item in manifest.get("backgrounds", []):
        filename = item.get("file", "")
        if (item.get("presentation") == presentation and isinstance(filename, str)
                and Path(filename).name == filename and (root / version / presentation / filename).is_file()):
            items.append(item)
    if not items:
        return {}
    signals = previous.signals if previous is not None else {}
    old = signals.get("portrait_asset", {})
    candidates = [i for i in items if i.get("category") == category] or items
    ids = {i["id"] for i in candidates}
    base = f"/static/portrait/{version}/{presentation}/"
    # Compare the actual selection time, not the polling/refreshed state time.
    selected_at = float(old.get("selected_at", 0))
    if (old.get("id") in ids and str(old.get("url", "")).startswith(base)
            and now.timestamp() - selected_at < refresh_seconds):
        return old
    recent = signals.get("recent_asset_ids", [])[-3:]
    fresh = [i for i in candidates if i["id"] not in recent]
    if not fresh:
        fresh = [i for i in candidates if i["id"] != old.get("id")] or candidates
    selected = random.SystemRandom().choice(fresh)
    filename = selected["file"]
    small = selected.get("small_file", filename)
    if Path(small).name != small or not (root / version / presentation / small).is_file():
        small = filename
    return {"id": selected["id"], "url": base + filename, "small_url": base + small,
            "selected_at": now.timestamp()}
