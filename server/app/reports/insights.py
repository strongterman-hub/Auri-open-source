"""Descriptive candidates, not diagnoses: every derived value is a fact key."""
from statistics import mean


def add_insight_candidates(evidence):
    facts, series = evidence['facts'], evidence['series']
    candidates = []

    def candidate(key, topic, observation, refs, meaning, action):
        candidates.append(dict(id=key, topic=topic, observation=observation,
                               fact_keys=refs, interpretation=meaning, optional_action=action))

    def fact(key, value, unit=''):
        facts[key] = f'{value:g}{unit}' if isinstance(value, (int, float)) else value
        return key

    # Do not count distance and calories as additional independent activity findings.
    for metric in ('STEPS', 'MAIN_SLEEP', 'RESTING_HEART_RATE', 'STRESS'):
        if metric not in series:
            continue
        item = series[metric]
        values, unit = item['values'], item['unit']
        valid = [(d, v) for d, v in zip(item['days'], values) if v is not None]
        prefix = f'insight.{metric}'
        if metric + '.delta_pct' in facts and abs(float(facts[metric+'.delta_pct'].rstrip('%'))) >= 15:
            candidate(prefix+'.previous', metric, '与前一个七日区间的记录日均值比较',
                      [metric+'.avg', metric+'.previous_avg', metric+'.delta_pct', metric+'.days', metric+'.previous_days'],
                      '只描述记录量的改变，不把方向自动等同于健康好坏，也不猜测改变原因。',
                      '如果这个变化并非自己有意安排，可回看日常活动或睡眠安排中哪一段发生了变化。')
        # Compare complete first/last three dates; avoid cherry-picking isolated days.
        first, last = values[:3], values[-3:]
        if all(v is not None for v in [*first, *last]):
            before, after = mean(first), mean(last)
            minimum = {'STEPS': 1500, 'MAIN_SLEEP': 30, 'RESTING_HEART_RATE': 4, 'STRESS': 8}[metric]
            if abs(after-before) >= minimum:
                refs = [fact(prefix+'.early_avg', round(before, 1), unit),
                        fact(prefix+'.late_avg', round(after, 1), unit),
                        fact(prefix+'.segment_change', round(after-before, 1), unit)]
                refs += ['DAY.'+d for d in [item['days'][0], item['days'][2], item['days'][-3], item['days'][-1]]]
                candidate(prefix+'.segments', metric, '前段与后段的记录均值有差异', refs,
                          '这是本期两个连续片段的描述，不是长期趋势，也不能据此认定生活原因。',
                          '可优先回看变化发生的片段，选择自己愿意保留或调整的安排。')
        if metric == 'STEPS' and len(valid) >= 5:
            low, high = min(valid, key=lambda p: p[1]), max(valid, key=lambda p: p[1])
            if high[1]-low[1] >= 3000 and high[1] >= 2*max(low[1], 1):
                refs = [fact(prefix+'.lowest_day', low[0]), fact(prefix+'.highest_day', high[0]),
                        metric+'.'+low[0], metric+'.'+high[0], metric+'.days']
                total = sum(v for _, v in valid)
                top = sorted(valid, key=lambda p: p[1], reverse=True)[:2]
                share = sum(v for _, v in top)/total*100 if total > 0 else 0
                if len(valid) == 7:
                    refs += [fact(prefix+'.top_pair_share', round(share, 1), '%')]
                candidate(prefix+'.distribution', metric, '逐日活动差异明显，均值不能代表每天', refs,
                          '高活动日会抬高平均数；先区分分布不均与持续偏低，不将低值日当成未佩戴。',
                          '若想让活动更均匀，可在较少出门的日子找一个顺手散步的时段，无需照搬最高日。')

    bedtimes = []
    for key, value in list(facts.items()):
        if key.startswith('BEDTIME.'):
            hour, minute = map(int, value.split(':'))
            bedtimes.append((key.split('.', 1)[1], (hour*60+minute-720) % 1440))
    if len(bedtimes) >= 5:
        early, late = min(bedtimes, key=lambda p: p[1]), max(bedtimes, key=lambda p: p[1])
        span = late[1]-early[1]
        if span >= 90:
            refs = [fact('insight.bedtime.span', span, '分钟'),
                    fact('insight.bedtime.earliest_day', early[0]), fact('insight.bedtime.latest_day', late[0]),
                    'BEDTIME.'+early[0], 'BEDTIME.'+late[0], 'MAIN_SLEEP.days']
            candidate('bedtime.range', 'SLEEP_TIMING', '主睡眠开始时间在本期记录之间跨度较大', refs,
                      '睡了多久与何时睡是不同问题；即使平均时长接近，也可能存在作息分散。最大跨度不是每天都在波动。',
                      '若作息分散让自己不舒服，可先试着让睡前收尾更接近日常时段，观察醒来感受；不推定熬夜原因。')

    evidence['insight_candidates'] = candidates
    evidence['report_content_version'] = 2
    return evidence
