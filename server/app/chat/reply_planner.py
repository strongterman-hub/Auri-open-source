from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.agent.llm import LLMClient
from app.agent.response_style import infer_response_style, normalize_response_style
from app.core.token_logger import token_context


SILENT_EXACT = {
    "嗯",
    "嗯嗯",
    "好",
    "好的",
    "好吧",
    "行",
    "可以",
    "知道了",
    "收到",
    "晚安",
    "哈哈",
    "哈哈哈",
    "ok",
    "okay",
}

FAST_MARKERS = (
    "提醒",
    "待办",
    "同步",
    "刷新",
    "健康",
    "睡眠",
    "心率",
    "步数",
    "天气",
    "位置",
    "在哪里",
    "几点",
    "什么时候",
    "帮我",
    "查一下",
    "搜索",
    "验证码",
    "登录",
    "密码",
    "账号",
    "报错",
    "失败",
    "紧急",
    "救命",
    "危险",
    "胸痛",
    "呼吸困难",
    "自杀",
    "不想活",
)


@dataclass(frozen=True)
class ReplyPlan:
    outcome: str = "reply"
    lane: str = "fast"
    verbosity: str = "short"
    reason: str = "fallback"


class ChatReplyPlanner:
    """Choose semantic reply/silence and a delay band for one message batch."""

    def __init__(
        self,
        llm: LLMClient,
        *,
        model: str | None = None,
        max_tokens: int = 2048,
    ) -> None:
        self.llm = llm
        self.model = model
        self.max_tokens = max_tokens

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind == "text":
                parts.append(str(part.get("text") or ""))
            elif kind == "image_url":
                parts.append("[图片]")
            elif kind == "file":
                info = part.get("file") or {}
                parts.append(f"[文件:{info.get('name', 'file')}]")
        return "\n".join(parts)

    @classmethod
    def batch_text(cls, messages: list[dict]) -> str:
        return "\n".join(
            text
            for message in messages
            if (text := cls._content_text(message.get("content"))).strip()
        ).strip()

    @classmethod
    def deterministic_plan(
        cls,
        messages: list[dict],
        *,
        allow_silent: bool,
    ) -> ReplyPlan | None:
        text = cls.batch_text(messages)
        normalized = re.sub(r"[\s。.!！,，~～]+", "", text).lower()
        has_attachment = any(
            isinstance(message.get("content"), list)
            and any(
                isinstance(part, dict) and part.get("type") in {"image_url", "file"}
                for part in message.get("content")
            )
            for message in messages
        )
        if any(message.get("onboarding_reply") for message in messages):
            return ReplyPlan("reply", "fast", "micro", "onboarding_reply")
        if allow_silent and not has_attachment and normalized in SILENT_EXACT:
            return ReplyPlan("silent", "fast", "micro", "high_confidence_closure")
        verbosity = infer_response_style(text, has_attachment=has_attachment)
        if has_attachment:
            return ReplyPlan("reply", "fast", verbosity, "attachment")
        if "?" in text or "？" in text:
            return ReplyPlan("reply", "fast", verbosity, "question")
        if any(marker in text for marker in FAST_MARKERS):
            return ReplyPlan(
                "reply", "fast", verbosity, "functional_or_safety_marker"
            )
        return None

    async def plan(
        self,
        messages: list[dict],
        *,
        recent_history: list[dict] | None = None,
        allow_silent: bool = True,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> ReplyPlan:
        deterministic = self.deterministic_plan(messages, allow_silent=allow_silent)
        if deterministic is not None:
            return deterministic

        batch = self.batch_text(messages)[:6000]
        recent = []
        for message in (recent_history or [])[-4:]:
            recent.append(
                {
                    "role": message.get("role"),
                    "content": self._content_text(message.get("content"))[:1000],
                }
            )
        prompt = (
            "You plan Auri's reply rhythm as a believable AI friend. Classify the current "
            "user-message batch. Return only JSON with outcome, lane, verbosity, reason. "
            "outcome is reply or silent. lane is fast, normal, or away. silent is allowed only for a "
            "high-confidence conversational closing or weak acknowledgment with no new "
            "information. Never choose silent for a question, request, task, health/account "
            "need, emotional disclosure, safety concern, image/file needing a response, or "
            "onboarding. Choose fast for functional, urgent, direct-question, or active "
            "back-and-forth messages; normal for ordinary conversation; away only for "
            "non-urgent casual sharing after a conversational pause. verbosity is micro, "
            "short, normal, or detailed: micro is one brief acknowledgment; short is the "
            "default for casual chat and simple questions; normal is for a few necessary "
            "reasons or steps, attachments, or important health/safety information; detailed "
            "is only for explicit requests for depth or genuinely complex planning, research, "
            "development, troubleshooting, comparison, or long-form writing. Match a short "
            "user message with a short reply unless completeness or safety requires more. "
            "Do not invent facts.\n\n"
            f"allow_silent={str(allow_silent).lower()}\n"
            f"recent_history={json.dumps(recent, ensure_ascii=False)}\n"
            f"current_batch={batch}"
        )
        kwargs = {"model": self.model} if self.model else {}
        try:
            with token_context(
                kind="chat_reply_plan", session_id=session_id, user_id=user_id
            ):
                response = await self.llm.complete(
                    [{"role": "user", "content": prompt}],
                    max_tokens=self.max_tokens,
                    **kwargs,
                )
            payload = self._parse_json(response.content)
            outcome = str(payload.get("outcome") or "reply").strip().lower()
            lane = str(payload.get("lane") or "fast").strip().lower()
            verbosity = normalize_response_style(
                payload.get("verbosity"),
                fallback=infer_response_style(
                    batch,
                    has_attachment="[图片]" in batch or "[文件:" in batch,
                ),
            )
            reason = str(payload.get("reason") or "model").strip()[:200]
            if outcome not in {"reply", "silent"}:
                outcome = "reply"
            if not allow_silent and outcome == "silent":
                outcome = "reply"
            if lane not in {"fast", "normal", "away"}:
                lane = "fast"
            return ReplyPlan(outcome, lane, verbosity, reason)
        except Exception:
            return ReplyPlan(
                "reply",
                "fast",
                infer_response_style(
                    batch,
                    has_attachment="[图片]" in batch or "[文件:" in batch,
                ),
                "planner_error_fallback",
            )

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        text = (content or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                raise
            parsed = json.loads(text[start : end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("reply plan must be a JSON object")
        return parsed
