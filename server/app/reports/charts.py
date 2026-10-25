"""Trusted, script-free visuals derived from the saved evidence only."""
from datetime import date
import html
import math
from statistics import mean
from app.reports.editorial import readable


def sleep_schedule(evidence):
    rows = sorted(evidence.get('sleep_windows', []), key=lambda row: row['day'])
    if not rows:
        raise ValueError('missing_sleep_windows')
    valid = [r for r in rows if r['end_minutes'] > r['start_minutes'] and r['end_minutes']-r['start_minutes'] <= 1440]
    if not valid:
        raise ValueError('invalid_sleep_windows')
    lower = math.floor(min(r['start_minutes'] for r in valid)/360)*360
    upper = max(lower+1440, math.ceil(max(r['end_minutes'] for r in valid)/360)*360)
    x = lambda value: 64 + (value-lower)/(upper-lower)*232
    days = next(iter(evidence['series'].values()))['days']
    lookup = {r['day']: r for r in valid}; parts = []
    for minute in range(lower, upper+1, 360):
        parts.append(f'<text x="{x(minute):.1f}" y="18" text-anchor="middle" fill="#aaa4bb" font-size="9">{minute//60%24:02d}:00</text>')
        parts.append(f'<line x1="{x(minute):.1f}" y1="26" x2="{x(minute):.1f}" y2="236" stroke="#363149"/>')
    for i, day in enumerate(days):
        y = 34+i*29; weekend = date.fromisoformat(day).weekday() >= 5
        parts.append(f'<text x="2" y="{y+11}" fill="#d7b978" font-size="10">{day[5:]}'+(' 周末' if weekend else '')+'</text>')
        row = lookup.get(day)
        if not row:
            parts.append(f'<text x="176" y="{y+11}" fill="#aaa4bb">—</text>'); continue
        start, end = row['start_minutes'], row['end_minutes']
        label = f'{day} {start//60%24:02d}:{start%60:02d}—{end//60%24:02d}:{end%60:02d}'
        color = '#d7b978' if weekend else '#a594ff'
        parts.append(f'<rect x="{x(start):.1f}" y="{y}" width="{max(1,x(end)-x(start)):.1f}" height="14" rx="7" fill="{color}"><title>{html.escape(label)}</title></rect>')
    return '<figure><figcaption>主睡眠开始与结束时间 · 金色为周末</figcaption><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 310 252" role="img" aria-label="主睡眠时间区间">'+''.join(parts)+'</svg><small>按醒来日排列；区间不等于实际睡着时长。</small></figure>'


def baseline_chart(evidence, metric):
    profile = evidence.get('baseline', {}).get(metric)
    if not profile:
        raise ValueError('missing_personal_baseline')
    values = [v for v in evidence['series'][metric]['values'] if v is not None]
    current = mean(values); refs = profile['fact_keys']; unit = evidence['series'][metric]['unit']
    def number(key):return float(evidence['facts'][refs[key]].removesuffix(unit))
    low, middle, high = number('lower'), number('median'), number('upper')
    left, right = min(low,current), max(high,current)
    padding = max((right-left)*.25, 1); left -= padding; right += padding
    x = lambda n: 22+(n-left)/(right-left)*266
    label = evidence['series'][metric]['label']
    historical = html.escape(str(readable(f'{low:.1f}{unit}'))+'—'+str(readable(f'{high:.1f}{unit}')))
    return f'<figure><figcaption>{html.escape(label)} · 与本人历史参照</figcaption><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 310 120" role="img" aria-label="本人历史范围与本期记录均值"><rect x="{x(low):.1f}" y="40" width="{max(2,x(high)-x(low)):.1f}" height="18" rx="9" fill="#49405e"/><line x1="{x(middle):.1f}" y1="34" x2="{x(middle):.1f}" y2="64" stroke="#aaa4bb"/><circle cx="{x(current):.1f}" cy="49" r="6" fill="#c0b0ff"/><text x="155" y="91" text-anchor="middle" fill="#eeeaf6" font-size="12">本期记录日均 {html.escape(str(readable(f"{current:.1f}{unit}")))}</text></svg><small>圆点为本期均值；阴影为此前历史中间范围 {historical}，中线为历史中位数，不是医学正常范围。</small></figure>'
