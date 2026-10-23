import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.persona.portrait_library import choose_asset, library_version


def write_library(root, version, genders=("female", "male")):
    items = []
    for gender in genders:
        target = root / version / gender
        target.mkdir(parents=True)
        for index, angle in enumerate(("front", "profile")):
            filename = f"day-{index}.jpg"
            (target / filename).write_bytes(b"test")
            items.append({"id": f"{gender}-{index}", "presentation": gender,
                          "category": "day", "file": filename, "angle": angle})
    (root / version / "manifest.json").write_text(json.dumps({"backgrounds": items}))


def test_latest_library_replaces_cached_pose_and_rotates_after_refresh(tmp_path):
    write_library(tmp_path, "v2")
    write_library(tmp_path, "v3")
    now = datetime.now(timezone.utc)
    previous = SimpleNamespace(signals={"portrait_asset": {
        "id": "female-0", "url": "/static/portrait/v2/female/day-0.jpg",
        "selected_at": now.timestamp()}})
    selected = choose_asset(tmp_path, "female", "day", previous, now, 60)
    assert selected["url"].startswith("/static/portrait/v3/female/")
    previous = SimpleNamespace(signals={"portrait_asset": selected, "recent_asset_ids": [selected["id"]]})
    assert choose_asset(tmp_path, "female", "day", previous, now + timedelta(seconds=20), 60) == selected
    following = choose_asset(tmp_path, "female", "day", previous, now + timedelta(seconds=61), 60)
    assert following["id"] != selected["id"]


def test_partial_new_library_never_falls_back_to_other_gender_or_old_pose(tmp_path):
    write_library(tmp_path, "v2")
    write_library(tmp_path, "v3", genders=("male",))
    now = datetime.now(timezone.utc)
    assert library_version(tmp_path) == "v3"
    assert choose_asset(tmp_path, "female", "day", None, now, 60) == {}
    assert choose_asset(tmp_path, "../male", "day", None, now, 60) == {}
    assert choose_asset(tmp_path, "male", "missing category", None, now, 60)["url"].startswith(
        "/static/portrait/v3/male/")
