"""Conservative, clause-scoped interpretation of explicit owner statements.

This is a bounded grammar, not a general language understanding model. Ambiguous
mentions stay unclassified; the original conversation remains available to the LLM.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


PLACE_LABELS = {
    "employer": "公司", "home": "家或住处", "campus": "学校",
    "library": "图书馆", "dorm": "宿舍", "hospital": "医院", "hotel": "酒店",
}
_PLACES = {
    "公司": "employer", "单位": "employer", "办公室": "employer",
    "家里": "home", "家": "home", "学校": "campus", "校园": "campus",
    "图书馆": "library", "宿舍": "dorm", "寝室": "dorm",
    "医院": "hospital", "酒店": "hotel",
}
_PLACE_WORDS = "|".join(sorted(_PLACES, key=len, reverse=True))
_PAST = r"昨天|前天|之前|以前|那天|上次|曾经|刚才|刚刚"
_FUTURE = r"明天|后天|下周|以后|待会|等会|一会儿|准备|打算|计划|快要|马上要|马上(?:就)?(?:去|回|到|上)|要去|将要"
_HYPOTHETICAL = r"如果|假如|要是|假设|倘若|是否|是不是|会不会|能不能"
_ACTIVITIES = (
    ("class", r"(?:正在|在)上课|上课呢"),
    ("meeting", r"(?:正在|在)开会|开会呢|正在组会"),
    ("studying", r"(?:正在|在)自习|(?:正在|在)学习|在图书馆(?:看书|学习|查资料)|自习呢"),
    ("working", r"(?:正在|在)工作|(?:正在|在)上班|(?:正在|在|居家|在家)办公|上班呢|办公呢"),
    ("meal", r"(?:正在|在)吃(?:午饭|晚饭|早饭|饭)|吃饭呢"),
    ("commuting", r"在路上|在车上|(?:正在|在)骑车|(?:正在|在)坐车|(?:已经)?出发了|已经出门|在去往|在去|通勤中"),
    ("exercising", r"(?:正在|在)运动|(?:正在|在)跑步|(?:正在|在)健身|(?:正在|在)打球"),
    ("leisure", r"(?:正在|在)休息|(?:正在|在)看剧|(?:正在|在)看电影|(?:正在|在)打游戏"),
    ("arrived", r"已经到(?:家|了)|回到.+了|到家了|回来了"),
    ("post_exercise", r"(?:已经)?(?:运动完|跑完|骑完|练完)(?:了)?"),
)


@dataclass
class OwnerState:
    place: str | None = None
    activity: str | None = None
    excluded_places: set[str] = field(default_factory=set)
    excluded_activities: set[str] = field(default_factory=set)
    source_refs: list[str] = field(default_factory=list)
    correction: bool = False

    def merge(self, newer: "OwnerState") -> None:
        if newer.place:
            self.place = newer.place
            self.excluded_places.discard(newer.place)
        if newer.activity:
            self.activity = newer.activity
            self.excluded_activities.discard(newer.activity)
        self.excluded_places.update(newer.excluded_places)
        self.excluded_activities.update(newer.excluded_activities)
        if self.place in self.excluded_places:
            self.place = None
        if self.activity in self.excluded_activities:
            self.activity = None
        self.source_refs = list(dict.fromkeys([*self.source_refs, *newer.source_refs]))
        self.correction = self.correction or newer.correction

    def audit_dict(self) -> dict:
        return {
            "place_kind": self.place, "activity": self.activity,
            "excluded_place_kinds": sorted(self.excluded_places),
            "excluded_activities": sorted(self.excluded_activities),
            "source_refs": self.source_refs, "correction": self.correction,
        }

    def format(self) -> str:
        lines = ["[CURRENT OWNER STATEMENTS AND CONSTRAINTS]"]
        if self.place:
            lines.append("用户明确表示当前在：" + PLACE_LABELS[self.place] + "。")
        for kind in sorted(self.excluded_places):
            lines.append("用户明确表示当前不在：" + PLACE_LABELS[kind] + "。不要重新假设用户在那里。")
        if self.activity:
            lines.append("Explicit current activity: " + self.activity)
        if self.excluded_activities:
            lines.append("Explicitly excluded activities: " + ", ".join(sorted(self.excluded_activities)))
        lines.append("Current message explicitly challenges a prior mistake: " + str(self.correction).lower() + ". Do not invent an earlier mistake or apology for a routine new self-report.")
        lines.append("These constraints override GPS, old conversation, habits and plans. "
                     "A building's map category is not the owner's employer or home. "
                     "Negative place evidence does not identify another place or exclude remote work.")
        return "\n".join(lines)


def parse_owner_state(text: str) -> OwnerState:
    result = OwnerState()
    text = re.sub(r'“[^”]*”|「[^」]*」|"[^"]*"|‘[^’]*’', "", str(text or ""))
    owner_subject = False
    for clause in re.split(r"[，,。；;！!\n]|但是|不过|而是", text):
        clause = re.sub(r"\s+", "", clause)
        if not clause:
            continue
        if re.search(r"(?:他说|她说|你说|别人说|朋友说|同事说)", clause):
            owner_subject = False
            continue
        if re.match(r"^(?:那|但|可)?(?:你|他|她|他们|她们|朋友|同事|我(?:的)?(?:朋友|同事|老师|家人|室友|同学|老板))", clause):
            owner_subject = False
            continue
        if "我" in clause and not re.search(r"(?:问我|说我|觉得我)", clause):
            owner_subject = True
        if not owner_subject and not re.match(r"^(?:现在|目前|今天|正在|已经|还)?(?:在|到家|回到|出发了|运动完|跑完|骑完)", clause):
            continue
        clause = re.sub(r"(?:你不知道|你不清楚|你忘了(?:吗)?)[?？!！]*$", "", clause)
        # Questions and double negatives need semantic interpretation; don't turn
        # them into authoritative state via a substring match.
        if re.search(r"[?？]|吗|么|哪|什么|怎么|不是不|并非不|没有不|不得不|不能不", clause):
            continue
        if re.search(_HYPOTHETICAL, clause) or re.search(_FUTURE, clause):
            continue
        # Explicit 'now' within a clause can disambiguate an earlier reference.
        current_clause = re.split(r"现在|此刻|目前|这会儿", clause)[-1]
        if re.search(_PAST, current_clause):
            continue
        clause = current_clause
        negative = re.search(r"(?:不(?:是)?在|没(?:有)?在|并非在)(" + _PLACE_WORDS + r")", clause)
        if negative:
            result.excluded_places.add(_PLACES[negative.group(1)])
        denied_activity = re.search(r"(?:不|没(?:有)?)(?:正在|在)?(上班|工作|办公|开会|上课|吃饭|跑步|运动)", clause)
        if denied_activity:
            result.excluded_activities.add({"上班":"working", "工作":"working", "办公":"working", "开会":"meeting", "上课":"class", "吃饭":"meal", "跑步":"exercising", "运动":"exercising"}[denied_activity.group(1)])
        for match in re.finditer(r"(?:待在|呆在|身在|在)(" + _PLACE_WORDS + r")", clause):
            if re.search(r"不|没|并非", clause[max(0, match.start()-3):match.start()]):
                continue
            result.place = _PLACES[match.group(1)]
        arrival = re.search(r"(?:回到(" + _PLACE_WORDS + r")(?:了|啦)|到了(" + _PLACE_WORDS + r"))", clause)
        if arrival:
            result.place = _PLACES[arrival.group(1) or arrival.group(2)]
        if re.search(r"(?:回到家|到家了)", clause):
            result.place = "home"
        for state, pattern in _ACTIVITIES:
            match = re.search(pattern, clause)
            if not match:
                continue
            if re.search(r"不|没|并非|别", clause[max(0, match.start()-3):match.start()]):
                result.excluded_activities.add(state)
                continue
            result.activity = state
            break
    if result.place in result.excluded_places:
        result.place = None
    result.correction = bool(result.excluded_places and re.search(r"你不知道|你怎么|你还以为|你以为|你又|不是说过|不是告诉|刚说过|说错|搞错", text))
    return result
