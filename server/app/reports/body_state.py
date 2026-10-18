"""Date-aligned inputs for AI synthesis, without a diagnostic score or cutoff."""
from itertools import combinations
from statistics import mean


DOMAINS = {'MAIN_SLEEP': 'sleep', 'STEPS': 'activity', 'RESTING_HEART_RATE': 'resting_physiology'}


def add_body_state_evidence(evidence):
    facts, series = evidence['facts'], evidence['series']
    previous = evidence.get('previous_series', {})

    def rows(items, metric):
        item = items.get(metric, {})
        return {day: value for day, value in zip(item.get('days', []), item.get('values', []))
                if value is not None}

    current = {key: rows(series, key) for key in DOMAINS}
    prior = {key: rows(previous, key) for key in DOMAINS}
    days = sorted({day for key in DOMAINS for day in series.get(key, {}).get('days', [])})
    comparisons = []

    def put(key, value, unit=''):
        facts[key] = f'{value:g}{unit}' if isinstance(value, (int, float)) else value
        return key

    def compare(kind, left, right, minimum):
        # Prefer all three independent domains; never compare mismatched dates.
        for size in (3, 2):
            for keys in combinations(DOMAINS, size):
                early = sorted(set.intersection(*(set(left[k]) for k in keys)))
                late = sorted(set.intersection(*(set(right[k]) for k in keys)))
                if min(len(early), len(late)) < minimum:
                    continue
                prefix = 'body.' + kind
                metrics = []
                for key in keys:
                    before = mean(left[key][d] for d in early)
                    after = mean(right[key][d] for d in late)
                    unit = series[key]['unit']
                    metrics.append({'metric': key, 'domain': DOMAINS[key], 'fact_keys': {
                        'before': put(prefix+'.'+key+'.before', round(before, 1), unit),
                        'after': put(prefix+'.'+key+'.after', round(after, 1), unit),
                        'change': put(prefix+'.'+key+'.change', round(after-before, 1), unit)},
                        'direction': 'up' if after > before else 'down' if after < before else 'unchanged'})
                directions = {m['metric']: m['direction'] for m in metrics}
                boundaries = []
                if directions.get('MAIN_SLEEP') == 'down' and directions.get('RESTING_HEART_RATE') == 'down':
                    boundaries.append('睡眠变短与静息心率下降是混合信号：可分别说明睡眠安排需照顾、静息读数趋缓；不据此断定整体负担加重或已经恢复。')
                if directions.get('STEPS') == 'up':
                    boundaries.append('走动增加不等于训练负荷增加；静息读数未抬高只能说未见同步抬高，不能说已证明身体适应了活动。')
                comparisons.append({'id': kind,
                                    'scope': '本期前段共同记录日 → 本期后段共同记录日' if kind == 'within_week' else '前一个七日区间共同记录日 → 本期共同记录日',
                                    'metrics': metrics,
                                    'interpretation_boundaries': boundaries,
                                    'before_dates': early, 'after_dates': late,
                                    'before_days_key': put(prefix+'.before_days', len(early), '天'),
                                    'after_days_key': put(prefix+'.after_days', len(late), '天'),
                                    'meaning': '同一组日期上的联合变化；方向不是健康结论，需综合支持与反向证据。'})
                return

    if len(days) == 7:
        compare('within_week',
                {key: {d: v for d, v in current[key].items() if d in days[:3]} for key in DOMAINS},
                {key: {d: v for d, v in current[key].items() if d in days[-3:]} for key in DOMAINS}, 2)
        compare('previous_week', prior, current, 5)

    # Sleep belongs to the wake date; daily RHR is not assumed to be nocturnal RHR.
    contexts = []
    for i, day in enumerate(days):
        refs = {key: key+'.'+day for key in DOMAINS if day in current[key] and key+'.'+day in facts}
        if len(refs) < 2:
            continue
        item = {'day_key': 'DAY.'+day, 'same_day': refs}
        if 'BEDTIME.'+day in facts:
            item['bedtime_key'] = 'BEDTIME.'+day
        if i and 'STEPS.'+days[i-1] in facts and days[i-1] in current['STEPS']:
            item['preceding_day_activity_key'] = 'STEPS.'+days[i-1]
        contexts.append(item)

    evidence['body_state_evidence'] = {
        'synthesis_ready': bool(comparisons), 'comparisons': comparisons, 'daily_context': contexts,
        'available_domains': [DOMAINS[key] for key in DOMAINS if current[key]],
        'interpretation_rules': [
            '先回答身体状态的变化方向和优先事项，再解释睡眠、活动、静息读数如何相互支持或冲突。方向不一致时说明哪些方面趋缓、哪些仍需照顾，不强行给整体好坏。',
            'within_week 是本期最早与最晚三个日期内的共同记录日；previous_week 是前一个七日区间，不是长期个人基线。样本量键是共同日期数，不是各指标全部覆盖。',
            '睡眠按醒来日归属；前一日活动才发生在该夜睡眠之前，当天活动在该夜之后。日静息心率不能精确归到某次运动之后，不根据同日共现推断因果。',
            'HEART_RATE 是全天记录心率，受活动和采样时段影响，不能替代 RESTING_HEART_RATE 判断静息负担。步数不是运动强度、训练负荷或体能。',
            'SLEEP_SCORE、RECOVERY_SCORE 是衍生分，可能与睡眠和心率共用输入，不当成独立证据重复计票；设备压力不代表主观心理压力。',
            '允许基于相互支持的变化说可能的短期负担/恢复线索，但不能据此断定病因、体能提高、过度训练或已完全恢复。没有相互支持时给局部判断。',
            '只有睡眠和活动时，可以判断恢复安排是否需要优先照顾，不能声称已有静息生理证据确认身体负担变化。',
            '行动应回应综合判断，说明优先改什么、为何如此、如何结合后续睡眠/静息读数和本人感受观察效果；不保证改善，不强加步数目标。'
        ]}
    evidence['report_content_version'] = 3
    return evidence


def generation_context(evidence):
    """Keep the source snapshot complete; focus model attention on joint evidence."""
    if not evidence.get('body_state_evidence', {}).get('synthesis_ready'):
        return evidence
    context = {key: value for key, value in evidence.items()
               if key not in {'facts', 'series', 'previous_series', 'coverage', 'insight_candidates'}}
    measurement_keys = {metric+'.'+day for metric in DOMAINS
                        for day in evidence['series'].get(metric, {}).get('days', [])}
    context['facts'] = {key: value for key, value in evidence['facts'].items()
                        if key.startswith(('body.', 'period.', 'DAY.', 'BEDTIME.', 'WAKE.', 'insight.bedtime.'))
                        or key in measurement_keys or any(key == metric+'.days' for metric in DOMAINS)}
    context['series'] = {key: value for key, value in evidence['series'].items() if key in DOMAINS}
    context['coverage'] = {key: value for key, value in evidence.get('coverage', {}).items() if key in DOMAINS}
    context['focus'] = '综合解释这些联合变化与行动优先级；未提供的统计项无需补算或补写，完整覆盖表由服务器另附。'
    return context
