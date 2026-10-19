"""Simplified/Traditional Chinese normalization for external place text.

OSM/Nominatim sometimes returns administrative names in Traditional Chinese
(for example ``海淀區``).  The model sees that text in the location context and
can mirror the script in its reply, so external place strings are normalized to
Simplified Chinese before they are cached, injected into prompts, or returned by
tools.

``opencc-python-reimplemented`` is the preferred converter.  A small built-in
table keeps basic normalization working in environments where the optional
dependency is unavailable (for example a minimal test runner).
"""
from __future__ import annotations

import threading
from typing import Any

try:  # pragma: no cover - exercised in production where the wheel is installed
    from opencc import OpenCC
except Exception:  # noqa: BLE001 - optional dependency, fall back below
    OpenCC = None  # type: ignore[assignment]

# Common Traditional -> Simplified character pairs seen in OSM admin/place
# names.  The OpenCC converter covers the full character set when installed.
_FALLBACK_TRANSLATION = str.maketrans(
    {
    "區": "区",
    "縣": "县",
    "鎮": "镇",
    "鄉": "乡",
    "莊": "庄",
    "園": "园",
    "樓": "楼",
    "號": "号",
    "臺": "台",
    "檯": "台",
    "灣": "湾",
    "島": "岛",
    "嶼": "屿",
    "東": "东",
    "門": "门",
    "廣": "广",
    "場": "场",
    "學": "学",
    "醫": "医",
    "藥": "药",
    "範": "范",
    "館": "馆",
    "圖": "图",
    "書": "书",
    "記": "记",
    "銀": "银",
    "錢": "钱",
    "線": "线",
    "網": "网",
    "車": "车",
    "馬": "马",
    "龍": "龙",
    "鳳": "凤",
    "華": "华",
    "國": "国",
    "際": "际",
    "產": "产",
    "業": "业",
    "動": "动",
    "體": "体",
    "樂": "乐",
    "遊": "游",
    "戲": "戏",
    "劇": "剧",
    "電": "电",
    "視": "视",
    "話": "话",
    "訊": "讯",
    "軟": "软",
    "數": "数",
    "據": "据",
    "庫": "库",
    "點": "点",
    "觀": "观",
    "復": "复",
    "興": "兴",
    "陽": "阳",
    "陰": "阴",
    "雲": "云",
    "霧": "雾",
    "風": "风",
    "濕": "湿",
    "溫": "温",
    "熱": "热",
    "涼": "凉",
    "氣": "气",
    "質": "质",
    "這": "这",
    "與": "与",
    "從": "从",
    "來": "来",
    "會": "会",
    "後": "后",
    "裡": "里",
    "個": "个",
    "們": "们",
    "對": "对",
    "還": "还",
    "過": "过",
    "開": "开",
    "關": "关",
    "問": "问",
    "間": "间",
    "頭": "头",
    "實": "实",
    "應": "应",
    "當": "当",
    "讓": "让",
    "覺": "觉",
    "認": "认",
    "舊": "旧",
    "聽": "听",
    "寫": "写",
    "讀": "读",
    "誰": "谁",
    "買": "买",
    "賣": "卖",
    "貴": "贵",
    "週": "周",
    "師": "师",
    "題": "题",
    "頁": "页",
    "載": "载",
    "長": "长",
    "養": "养",
    "內": "内",
    "兩": "两",
    "為": "为",
    "麼": "么",
    "發": "发",
    "髮": "发",
    "說": "说",
    "進": "进",
    "遠": "远",
    "邊": "边",
    "隨": "随",
    "續": "续",
    "協": "协",
    "廠": "厂",
    "歷": "历",
    "證": "证",
    "辦": "办",
    "務": "务",
    "員": "员",
    "團": "团",
    "隊": "队",
    "構": "构",
    "標": "标",
    "準": "准",
    "確": "确",
    "識": "识",
    "課": "课",
    "練": "练",
    "習": "习",
    "試": "试",
    "績": "绩",
    "級": "级",
    "組": "组",
    "織": "织",
    "統": "统",
    "計": "计",
    "劃": "划",
    "價": "价",
    "值": "值",
    "億": "亿",
    "萬": "万",
    "條": "条",
    "樣": "样",
    "種": "种",
    "類": "类",
    "處": "处",
    "稱": "称",
    "節": "节",
    "約": "约",
    "繫": "系",
    "聯": "联",
    "絡": "络",
    "橋": "桥",
    "路": "路",
    "街": "街",
    "巷": "巷",
    "弄": "弄",
    "里": "里",
    "市": "市",
    "省": "省",
    "村": "村",
    "屯": "屯",
    "營": "营",
    "壩": "坝",
    "坡": "坡",
    "嶺": "岭",
    "峰": "峰",
    "崗": "岗",
    "湖": "湖",
    "河": "河",
    "江": "江",
    "海": "海",
    "溝": "沟",
    "溪": "溪",
    "泉": "泉",
    "池": "池",
    "潭": "潭",
    "灘": "滩",
    "港": "港",
    "碼": "码",
    "林": "林",
    "舍": "舍",
    "房": "房",
    "屋": "屋",
    "宅": "宅",
    "院": "院",
    "寺": "寺",
    "廟": "庙",
    "宮": "宫",
    "塔": "塔",
    "亭": "亭",
    "閣": "阁",
    "壇": "坛",
    "墓": "墓",
    "墳": "坟",
    "陵": "陵",
    "碑": "碑",
    "石": "石",
    "岩": "岩",
    "洞": "洞",
    "窟": "窟",
    "礦": "矿",
    "窯": "窑",
    "倉": "仓",
    "棧": "栈",
    "閘": "闸",
    "堤": "堤",
    "渠": "渠",
    "運": "运",
    "輸": "输",
    "輪": "轮",
    "船": "船",
    "艦": "舰",
    "機": "机",
    "站": "站",
    "道": "道",
    "隧": "隧",
    "環": "环",
    "繞": "绕",
    "轉": "转",
    "彎": "弯",
    "直": "直",
    "交": "交",
    "叉": "叉",
    "口": "口",
    "圓": "圆",
    "礁": "礁",
    "沙": "沙",
    "洲": "洲",
    }
)

_converter = None
_converter_lock = threading.Lock()
if OpenCC is not None:
    try:
        _converter = OpenCC("t2s")
    except Exception:  # noqa: BLE001 - malformed installs fall back safely
        _converter = None


def to_simplified(text: str | None) -> str | None:
    """Return *text* with Traditional Chinese characters converted to Simplified."""
    if not text:
        return text
    if _converter is not None:
        try:
            with _converter_lock:
                return _converter.convert(text)
        except Exception:  # noqa: BLE001 - never fail location enrichment
            pass
    return text.translate(_FALLBACK_TRANSLATION)


def normalize_value(value: Any) -> Any:
    """Recursively convert all strings inside JSON-like *value*."""
    if isinstance(value, str):
        return to_simplified(value)
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(normalize_value(item) for item in value)
    if isinstance(value, dict):
        return {key: normalize_value(item) for key, item in value.items()}
    return value
