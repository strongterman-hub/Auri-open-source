import asyncio
import hashlib
import io
import json
from types import SimpleNamespace

import pytest
from PIL import Image

from app.chat.expression import StickerCatalog
from scripts.import_community_stickers import validate_image


def catalog(tmp_path):
    (tmp_path / "meme-sleep.gif").write_bytes(b"fixture")
    (tmp_path / "meme-happy.jpg").write_bytes(b"fixture")
    (tmp_path / "female-smile.png").write_bytes(b"fixture")
    (tmp_path / "male-smile.png").write_bytes(b"fixture")
    common = [{"id": "sleep", "presentation": "any", "kind": "community", "reviewed": True,
               "file": "meme-sleep.gif", "animated": True, "tags": ["困", "晚安"], "emotion": "calm"},
              {"id": "happy", "presentation": "any", "kind": "community", "reviewed": True,
               "file": "meme-happy.jpg", "tags": ["开心"], "emotion": "happy"}]
    (tmp_path / "community.json").write_text(json.dumps({"stickers": common + [
        {**common[0], "id": "unreviewed", "reviewed": False},
        {**common[0], "id": "wrong-file", "file": "male-smile.png"},
        {**common[0], "id": "escape", "file": "../meme-sleep.gif"}]}))
    (tmp_path / "manifest.json").write_text(json.dumps({"stickers": [
        {"id": "female", "presentation": "female", "file": "female-smile.png"},
        {"id": "male", "presentation": "male", "file": "male-smile.png"}]}))
    return StickerCatalog(tmp_path)


def test_reviewed_memes_are_shared_but_character_gender_is_still_isolated(tmp_path):
    source = catalog(tmp_path)
    assert [s["id"] for s in source.entries("female")] == ["sleep", "happy", "female"]
    assert [s["id"] for s in source.entries("male")] == ["sleep", "happy", "male"]
    assert source.resolve("sleep", "male")["presentation"] == "any"
    assert source.resolve("sleep", "female")["animated"] is True
    assert source.resolve("male", "female") is None
    assert source.candidates("female", "好困，晚安", ["happy"])[0]["id"] == "sleep"
    assert all(s["id"] != "happy" for s in source.candidates("male", "开心", ["happy"]))


def test_import_rejects_changed_content_or_wrong_animation_flag():
    content = io.BytesIO()
    Image.new("RGB", (20, 10), "red").save(content, format="PNG")
    data = content.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    validate_image(data, digest, False)
    with pytest.raises(ValueError):
        validate_image(data, "0" * 64, False)
    with pytest.raises(ValueError):
        validate_image(data, digest, True)
