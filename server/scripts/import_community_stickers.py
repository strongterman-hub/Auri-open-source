"""Explicitly cache reviewed community memes; never runs on a chat request.

Upstream source/attribution and individual image rights remain with their authors.
Only the reviewed manifest is distributed with source code, not third-party images.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image

MAX_BYTES = 4 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 4_000_000


def validate_image(data: bytes, expected: str, animated: bool) -> None:
    if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("image size or hash mismatch")
    with Image.open(io.BytesIO(data)) as image:
        if image.format not in ("JPEG", "PNG", "GIF", "WEBP"):
            raise ValueError("unsupported image format")
        if image.width * image.height > 4_000_000 or getattr(image, "n_frames", 1) > 400:
            raise ValueError("image dimensions or frame count exceeded")
        if animated and not getattr(image, "is_animated", False):
            raise ValueError("expected animated image")
        image.verify()


async def run(root: Path, verify_only: bool) -> None:
    manifest = json.loads((root / "community.json").read_text(encoding="utf-8"))
    semaphore = asyncio.Semaphore(6)
    async with httpx.AsyncClient(timeout=30, trust_env=False, follow_redirects=False) as client:
        async def one(item):
            source = urlsplit(item["source_url"])
            filename = item["file"]
            if (item.get("reviewed") is not True or item.get("presentation") != "any"
                    or item.get("kind") != "community" or source.scheme != "https"
                    or source.netloc != "zhaoolee.com" or source.query or source.fragment
                    or not re.fullmatch(r"/ChineseBQB/media/[0-9a-f]{64}\.(gif|png|jpg|jpeg|webp)", source.path)
                    or Path(filename).name != filename or not filename.startswith("meme-")):
                raise ValueError("unreviewed or invalid source entry")
            path = root / filename
            async with semaphore:
                if path.exists():
                    data = path.read_bytes()
                elif verify_only:
                    raise FileNotFoundError(filename)
                else:
                    chunks, size = [], 0
                    async with client.stream("GET", item["source_url"]) as response:
                        response.raise_for_status()
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > MAX_BYTES:
                                raise ValueError("image download too large")
                            chunks.append(chunk)
                    data = b"".join(chunks)
                validate_image(data, item["sha256"], bool(item.get("animated")))
                if not path.exists():
                    pending = path.with_suffix(path.suffix + ".tmp")
                    pending.write_bytes(data)
                    os.replace(pending, path)
            return item["id"]
        completed = await asyncio.gather(*(one(item) for item in manifest["stickers"]))
    print(json.dumps({"verified": len(completed), "animated": sum(bool(i.get("animated")) for i in manifest["stickers"])}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1] / "app/static/expression")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(args.root, args.verify_only))
