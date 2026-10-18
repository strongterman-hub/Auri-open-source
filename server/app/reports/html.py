from __future__ import annotations

import html
import re
from html.parser import HTMLParser


TOKEN = re.compile(r"\{\{(fact|chart):([A-Za-z0-9_.:-]+)\}\}")
TAGS = {"section", "article", "div", "p", "h2", "h3", "h4", "strong", "b", "em", "small", "span", "ul", "ol", "li", "table", "thead", "tbody", "tr", "th", "td", "caption", "blockquote", "br", "hr", "details", "summary"}
CLASSES = {"card", "grid", "muted", "accent", "large", "table-wrap", "note", "pill"}


class SafeHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.output, self.blocked = [], 0
    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "iframe", "object", "svg", "math", "template"}:
            self.blocked += 1
        if self.blocked or tag not in TAGS:
            return
        allowed = []
        for name, value in attrs:
            if name == "class":
                value = " ".join(c for c in (value or "").split() if c in CLASSES)
                allowed.append(f'class="{value}"')
            elif name in {"colspan", "rowspan"} and tag in {"td", "th"} and (value or "").isdigit() and 1 <= int(value) <= 12:
                allowed.append(f'{name}="{value}"')
        self.output.append("<" + tag + (" " + " ".join(allowed) if allowed else "") + ">")
    def handle_endtag(self, tag):
        if tag in {"script", "style", "iframe", "object", "svg", "math", "template"}:
            self.blocked = max(0, self.blocked - 1)
            return
        if not self.blocked and tag in TAGS and tag not in {"br", "hr"}:
            self.output.append(f"</{tag}>")
    def handle_data(self, data):
        if not self.blocked:
            self.output.append(html.escape(data))


def sanitize(fragment):
    parser = SafeHTML()
    parser.feed(fragment)
    parser.close()
    return "".join(parser.output)


def chart(series, kind="bar"):
    if kind not in {"bar", "line"}:
        raise ValueError("unsupported_chart")
    values = series["values"]
    maximum = max([abs(v) for v in values if v is not None] + [1])
    parts = []
    previous = None
    for i, value in enumerate(values):
        x = 27 + i * 43
        if value is None:
            previous = None
            parts.append(f'<text x="{x}" y="150" text-anchor="middle" fill="#9292ad">—</text>')
        else:
            y = 142 - max(0, value) / maximum * 95
            if kind == "bar":
                parts.append(f'<rect x="{x-12}" y="{y:.1f}" width="24" height="{142-y:.1f}" rx="5" fill="#a594ff"/>')
            else:
                if previous is not None:
                    parts.append(f'<line x1="{previous[0]}" y1="{previous[1]:.1f}" x2="{x}" y2="{y:.1f}" stroke="#a594ff" stroke-width="3"/>')
                parts.append(f'<circle cx="{x}" cy="{y:.1f}" r="4" fill="#a594ff"/>')
                previous = (x, y)
            parts.append(f'<text x="{x}" y="{y-8:.1f}" text-anchor="middle" fill="#ddd8fa" font-size="9">{value:g}</text>')
        parts.append(f'<text x="{x}" y="165" text-anchor="middle" fill="#aaa5bf" font-size="10">{html.escape(series["days"][i][5:])}</text>')
    label = html.escape(series["label"] + "（" + series["unit"] + "）")
    return f'<figure><figcaption>{label}</figcaption><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 310 180" role="img" aria-label="{label}">{"".join(parts)}</svg></figure>'


def render_output(value, evidence):
    if not isinstance(value, dict) or not all(isinstance(value.get(k), str) for k in ("summary", "html")):
        raise ValueError("invalid_report_schema")
    raw = value["html"]
    sparse = max((d['valid_days'] for d in evidence['coverage'].values()), default=0) <= 2
    if len(raw) > 45000 or len(raw) < (160 if sparse else 400):
        raise ValueError("report_length")
    facts, series = evidence["facts"], evidence["series"]
    def replace(match):
        kind, key = match.groups()
        if kind == "fact":
            if key not in facts:
                raise ValueError("unknown_fact:" + key)
            return html.escape(str(facts[key]))
        metric, _, form = key.partition(":")
        if metric not in series:
            raise ValueError("unknown_chart")
        return chart(series[metric], form or "bar")
    # Reject invented numeric measurements in prose/tables; all values use facts.
    format_errors = []
    for kind, key in TOKEN.findall(value['summary'] + raw):
        if kind == 'fact' and key not in facts:
            format_errors.append('unknown_fact:' + key)
        elif kind == 'chart' and key.split(':')[0] not in series:
            format_errors.append('unknown_chart')
    for field, text in (("summary", value["summary"]), ("html", raw)):
        if re.search(r'(\{\{fact:[A-Za-z0-9_.:-]+\}\})\s*(?:为|是|的)\s*\1', text):
            format_errors.append("repeated_fact_statement:" + field)
        for key in re.findall(r'(?:少了|减少了|缩短了|降低了|下降了)\s*\{\{fact:([A-Za-z0-9_.:-]+)\}\}', text):
            if str(facts.get(key, '')).startswith('-'):
                format_errors.append('signed_delta_direction:' + field)
        measurement = '|'.join(re.escape(key) for key in series)
        if measurement and re.search(r'\{\{fact:(?:'+measurement+r')\.\d{4}-\d{2}-\d{2}\}\}\s*的\s*\{\{fact:', text):
            format_errors.append('measurement_used_as_date:' + field)
        stripped = re.sub(r"<[^>]+>", "", TOKEN.sub("", text))
        if re.search(r"[0-9]|[一二两三四五六七八九十百千万]+(?:到[一二两三四五六七八九十百千万]+)?(?:分钟|小时|次/分)|[一二两三四五六七八九十]+[百千万][一二两三四五六七八九十百千万]*步", html.unescape(stripped)):
            format_errors.append("unreferenced_number:" + field)
    if format_errors:
        raise ValueError('; '.join(dict.fromkeys(format_errors)))
    summary = TOKEN.sub(replace, value["summary"]).strip()
    if "<" in summary or "{{" in summary or not 10 <= len(summary) <= 80:
        raise ValueError("summary_length_or_markup")
    safe = sanitize(raw)
    if series and not re.search(r"\{\{chart:", safe):
        raise ValueError("missing_chart")
    if not re.search(r"\{\{fact:", safe):
        raise ValueError("no_evidence")
    if evidence.get('body_state_evidence', {}).get('synthesis_ready'):
        cited = {key for kind, key in TOKEN.findall(safe) if kind == 'fact'}
        domains = {metric for metric in ('MAIN_SLEEP', 'STEPS', 'RESTING_HEART_RATE')
                   if any(key.startswith(metric+'.') or (key.startswith('body.') and '.'+metric+'.' in key)
                          for key in cited)}
        if len(domains) < 2:
            raise ValueError('missing_cross_domain_evidence')
    if len(re.sub(r"<[^>]+>|\{\{[^}]+\}\}", "", safe)) < (90 if sparse else 250):
        raise ValueError("insufficient_report_content")
    body = TOKEN.sub(replace, safe)
    if "{{" in body:
        raise ValueError("invalid_placeholder")
    rows = "".join(f'<tr><td>{html.escape(d["label"])}</td><td>{d["valid_days"]}/7天</td></tr>' for d in evidence["coverage"].values())
    period = html.escape(f'{facts["period.start"]} — {facts["period.end"]}')
    footer = f'<details class="card"><summary>记录范围与计算口径</summary><p class="muted">{period} · {html.escape(facts["period.timezone"])}</p><table><tr><th>记录</th><th>有效天数</th></tr>{rows}</table><p class="muted">平均值按有效记录计算，图中空白代表缺测。主睡眠与午睡分开；穿戴评分反映记录中的变化。</p><p class="muted">数据更新时间：{html.escape(facts["data.updated_at"])}</p></details>'
    csp = "default-src 'none'; style-src 'unsafe-inline'; img-src 'none'; font-src 'none'; base-uri 'none'; form-action 'none'"
    document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="{csp}"><title>健康周报</title><style>
*{{box-sizing:border-box}}html{{color-scheme:dark}}body{{margin:0;background:#10101c;color:#eeeaf6;font:16px/1.85 system-ui,sans-serif;padding:24px 18px 40px}}main{{max-width:760px;margin:auto}}h1{{font-size:29px;line-height:1.35;margin:10px 0}}h2{{font-size:23px;line-height:1.5}}h3{{font-size:19px}}p{{margin:12px 0}}.eyebrow,.accent{{color:#bbaaff}}.muted,small{{color:#aaa4bb}}.card{{background:#1d1b2e;border:1px solid #363149;border-radius:20px;padding:20px;margin:20px 0}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}.large{{font-size:27px;font-weight:700}}.note,blockquote{{border-left:3px solid #b6a1fa;padding-left:15px;margin:20px 0}}.pill{{display:inline-block;background:#302846;border-radius:20px;padding:3px 12px}}table{{width:100%;border-collapse:collapse;font-size:14px;overflow-wrap:anywhere}}th,td{{text-align:left;padding:10px 6px;border-bottom:1px solid #393248}}.table-wrap{{overflow-x:auto}}figure{{margin:20px 0}}figcaption{{color:#c0b7d5;font-size:14px}}svg{{width:100%;height:auto}}li{{margin:8px 0}}a{{color:inherit}}@media(min-width:600px){{body{{padding:36px}}}}
</style></head><body><main><p class="eyebrow">AURI · 七天健康回顾</p><h1>这一周，慢慢看懂自己</h1><p class="muted">{period}</p>{body}{footer}</main></body></html>'''
    return html.unescape(summary), document
