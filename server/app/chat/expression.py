"""Validated, provider-independent final-reply plans (never a wire protocol)."""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

EMOTIONS = {"calm", "happy", "excited", "concerned", "sad", "annoyed", "playful", "shy"}


@dataclass
class ReplyExpression:
    messages: list[str] = field(default_factory=list)
    emotion: dict[str, Any] = field(default_factory=dict)
    sticker_id: str | None = None

    @property
    def text(self) -> str:
        return "\n\n".join(self.messages)


def parse_expression(raw: str) -> ReplyExpression:
    text = (raw or "").strip()
    candidate = text
    if candidate.startswith("```json") and candidate.endswith("```"):
        candidate = candidate[7:-3].strip()
    if '"auri_reply"' not in candidate[:150]:
        # Plain answers from older providers remain valid. Preserve long prose,
        # reports and code rather than mechanically cutting every sentence.
        return ReplyExpression(messages=[text] if text else [])
    try:
        data = json.loads(candidate)
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid internal reply plan") from exc
    parts = data.get("messages")
    if data.get("auri_reply") != 1 or not isinstance(parts, list) or not 1 <= len(parts) <= 4:
        raise ValueError("invalid internal reply messages")
    if any(not isinstance(p, str) or not p.strip() for p in parts):
        raise ValueError("empty internal reply part")
    emotion = data.get("emotion") or {}
    if not isinstance(emotion, dict) or emotion.get("name") not in EMOTIONS:
        emotion = {}
    else:
        try:
            intensity = float(emotion.get("intensity", 0.2))
        except (ValueError, TypeError):
            intensity = 0.2
        if not math.isfinite(intensity):
            intensity = 0.2
        emotion = {
            "name": emotion["name"],
            "intensity": min(0.8, max(0.0, intensity)),
            "reason": str(emotion.get("reason") or "")[:180],
        }
    sticker_id = data.get("sticker_id")
    return ReplyExpression(
        messages=[p.strip() for p in parts], emotion=emotion,
        sticker_id=sticker_id if isinstance(sticker_id, str) else None,
    )


class StickerCatalog:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path(__file__).resolve().parents[1] / "static" / "expression"

    def entries(self, presentation: str) -> list[dict]:
        if presentation not in ("female", "male"):
            return []
        result = []
        for manifest in ("community.json", "manifest.json"):
            try:
                data = json.loads((self.root / manifest).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for item in data.get("stickers", []):
                filename = item.get("file", "")
                common = (manifest == "community.json" and item.get("presentation") == "any"
                          and item.get("kind") == "community" and item.get("reviewed") is True)
                if manifest == "community.json" and not common:
                    continue
                prefix = "meme-" if common else presentation + "-"
                if ((common or item.get("presentation") == presentation)
                        and isinstance(filename, str) and filename.startswith(prefix)
                        and Path(filename).name == filename and (self.root / filename).is_file()):
                    result.append(item)
        return result

    def candidates(self, presentation: str, query: str, recent: list[str], *, emotion: str = "calm") -> list[dict]:
        items = [item for item in self.entries(presentation) if item.get("id") not in recent]
        def rank(item):
            matched = sum(1 for tag in item.get("tags", []) if isinstance(tag, str) and tag.lower() in query.lower())
            return matched * 10 + (2 if item.get("emotion") == emotion else 0)
        common = sorted((s for s in items if s.get("kind") == "community"), key=rank, reverse=True)
        character = sorted((s for s in items if s.get("kind") != "community"), key=rank, reverse=True)
        # Keep prompt size bounded while offering familiar reaction images first.
        return common[:20] + character[:8]

    def resolve(self, sticker_id: str | None, presentation: str) -> dict | None:
        for item in self.entries(presentation):
            if item.get("id") == sticker_id:
                return {
                    "type": "sticker", "id": item["id"], "presentation": item.get("presentation", presentation),
                    "url": f"/static/expression/{item['file']}",
                    "description": str(item.get("description", "表情"))[:100],
                    "crop": item.get("crop"),
                    "animated": bool(item.get("animated", False)),
                }
        return None


def expression_instruction(state: dict, stickers: list[dict], *, allow_sequence: bool) -> str:
    choices = [{"id": s["id"], "meaning": s.get("description"), "emotion": s.get("emotion"),
                "kind": s.get("kind", "character"), "animated": bool(s.get("animated", False))}
               for s in stickers]
    return (
        "[AURI INTERNAL REPLY PLAN]\n"
        "For your FINAL answer only (tool calls remain normal), return one JSON object: "
        '{"auri_reply":1,"messages":["自然口语回复"],'
        '"emotion":{"name":"calm","intensity":0.2,"reason":"本轮具体原因"},'
        '"sticker_id":null}. No code fence. messages contain exactly the words the user sees. '
        + ("Naturally use 1-3 messages (maximum 4) when thoughts have separate beats. "
           if allow_sequence else "Use exactly one complete message. ")
        + "Never pad a short answer or split a negation, quote, code block, list or report. "
        "Preserve all important facts. You can be happy, playful, sad, concerned or annoyed "
        "when the actual conversation warrants it. State is YOUR emotional response, not "
        "a classification copied from the user's mood. Be expressive in the actual words, "
        "not by announcing emotion labels or roleplay stage directions. Being busy, asleep "
        "or slow to reply is not an insult. Misunderstandings and apologies can soften anger. "
        "Do not invent offscreen life. Use emotion names calm/happy/excited/concerned/sad/"
        "annoyed/playful/shy, intensity 0..0.8. Keep continuity without forcing an emotion. "
        "Choose at most one optional sticker ID from the supplied candidates only when it "
        "adds something; usually null. Serious distress and factual/task answers need care. "
        "Community candidates are familiar cat/dog/opossum internet reactions; prefer one "
        "when its actual caption and meaning fit, and vary it with character stickers. "
        "If the user explicitly requests a matching meme, select it when available; "
        "a requested moving sticker must have animated=true. If they ask for quiet company "
        "or no jokes during distress, sticker_id MUST be null. Do not assume unmentioned "
        "study effort, personal timelines or experiences. Express respectful boundaries "
        "when repeatedly insulted; do not invite or endorse further abuse. "
        "Do not invent a trending rank. Self-mocking captions describe your playful reaction, "
        "never mock the user's pain or promise an action you will not do. "
        "Current emotional context: " + json.dumps(state, ensure_ascii=False)
        + "\nSticker candidates: " + json.dumps(choices, ensure_ascii=False)
    )
