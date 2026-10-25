"""Editorial evidence and provenance, separate from clinical interpretation."""
from copy import deepcopy
from datetime import date
import re
from statistics import mean, median, quantiles


VERSION = 4


def readable(value):
    match = re.fullmatch(r'(-?\d+(?:\.\d+)?)分钟', str(value))
    if match:
        raw = float(match[1]); minutes = int(abs(raw) + .5)
        hours, minutes = divmod(minutes, 60)
        return ('-' if raw < 0 else '') + (f'{hours}小时' if hours else '') + (f'{minutes}分' if hours and minutes else '' if hours else f'{minutes}分钟')
    match = re.fullmatch(r'(-?\d+(?:\.\d+)?)步', str(value))
    return f'{float(match[1]):.0f}步' if match else value


def prepare_editorial(evidence, history=()):
    facts, series = evidence['facts'], evidence['series']
    for key, value in list(facts.items()):
        if key.startswith('read.'):
            continue
        if re.fullmatch(r'-?\d+(?:\.\d+)?(?:分钟|步)', str(value)):
            facts['read.' + key] = readable(value)
        if key.endswith(('.delta', '.change', '.segment_change')):
            facts[key + '_abs'] = str(value).lstrip('-')
            facts['read.' + key + '_abs'] = readable(str(value).lstrip('-'))
    candidates = []

    def add(key, domains, refs, observation, priority=1, charts=()):
        refs = [ref for ref in refs if ref in facts]
        if refs:
            candidates.append({'id': key, 'domains': domains, 'fact_keys': refs,
                               'observation': observation, 'priority': priority,
                               'charts': list(charts)})

    windows = evidence.get('sleep_windows', [])
    starts = [(row['day'], row['start_minutes']) for row in windows]
    if len(starts) >= 5 and max(v for _, v in starts)-min(v for _, v in starts) >= 90:
        refs = ['insight.bedtime.span'] + ['BEDTIME.'+d for d, _ in starts]
        weekends = [v for d, v in starts if date.fromisoformat(d).weekday() >= 5]
        weekdays = [v for d, v in starts if date.fromisoformat(d).weekday() < 5]
        if min(len(weekends), len(weekdays)) >= 2:
            gap = round(mean(weekends)-mean(weekdays))
            facts['timing.weekend_gap'] = f'{abs(gap)}分钟'
            facts['read.timing.weekend_gap'] = readable(f'{abs(gap)}分钟')
            refs += ['read.timing.weekend_gap']
        add('sleep_timing', ['sleep'], refs,
            '开始时间有可回看的差异；可区分周内与周末。周末不是已知休息日，不能仅凭晚睡认定睡眠差。',
            3, ['SLEEP_TIMING:schedule'])
    if 'MAIN_SLEEP' in series:
        add('sleep', ['sleep'], ['read.MAIN_SLEEP.avg', 'read.MAIN_SLEEP.delta_abs', 'MAIN_SLEEP.days'],
            '结合入睡时间、质量与本人感受看睡眠，不将小幅时长差当成问题或睡眠债。', 1, ['MAIN_SLEEP:bar'])
    if 'SLEEP_SCORE' in series:
        add('sleep_quality', ['sleep'], ['SLEEP_SCORE.avg', 'SLEEP_SCORE.days'],
            '设备睡眠评分提供另一种观察，不能否定本人感受，也不能与原始睡眠输入重复计票。', 2, ['SLEEP_SCORE:line'])
    if 'STEPS' in series:
        add('activity', ['activity'], ['STEPS.avg', 'read.STEPS.avg', 'STEPS.days', 'STEPS.delta_pct'],
            '看日常走动及连续性，不将步数当作运动类型、强度或完成散步目标。', 1, ['STEPS:bar'])
    if 'WORKOUT' in series:
        add('movement', ['activity'], ['read.WORKOUT.total', 'WORKOUT.days'],
            '回看明确记录的运动；未给类型时不编跑步或训练种类，单次记录不等于习惯形成。', 2, ['WORKOUT:bar'])
    body = evidence.get('body_state_evidence', {})
    if body.get('synthesis_ready'):
        refs = [ref for item in body['comparisons'] for m in item['metrics'] for ref in m['fact_keys'].values()]
        add('body_state', body['available_domains'], refs,
            '联合证据可帮助理解状态方向或冲突，但不强制以综合判断开篇；片段均值不是整周指标均值。',
            2, ['MAIN_SLEEP:line'] if 'MAIN_SLEEP' in series else ['STEPS:bar'])
    stable = []
    for key, minimum in [('MAIN_SLEEP', 60), ('RESTING_HEART_RATE', 6), ('STEPS', None)]:
        values = [v for v in series.get(key, {}).get('values', []) if v is not None]
        if len(values) < 5:
            continue
        limit = minimum if minimum is not None else max(500, mean(values)*.5)
        if max(values)-min(values) <= limit:
            stable.append(key)
    available = [k for k in ('MAIN_SLEEP', 'RESTING_HEART_RATE', 'STEPS') if k in series]
    if len(stable) >= 2 and set(stable) == set(available):
        add('steady', stable, [k+'.avg' for k in stable],
            '这些记录在相近范围，可写短回顾；稳定不等于医学正常或恢复充分，不为制造新意加任务。', 3,
            [stable[0]+':line'])
    notes = evidence.get('owner_notes', [])
    note_ids = [n['id'] for n in notes if n.get('id')]
    if note_ids:
        for item in candidates:
            item['note_ids'] = note_ids
        add('personal_context', list(series), [k+'.avg' for k in series],
            '把可靠记录与用户原话放回生活；只有本人明确说过的经历、感受与意图可归给本人。', 3,
            ['SLEEP_TIMING:schedule'] if windows else [next(iter(series))+':bar'] if series else [])
    if not candidates and series:
        key = next(iter(series))
        add('local', [key], [key+'.avg', key+'.days'], '只有局部观察时简短说明能学到什么，不外推整体身体状态。',
            1, [key+':bar'])
    evidence['editorial'] = {
        'version': VERSION, 'candidates': candidates,
        'topic_history': list(history)[:4],
        'note_ids': note_ids,
        'selection_rules': [
            '先看证据是否可靠，再选择个人相关、有实质意义、持续或新的发现；priority 是编辑线索，不是健康风险分。',
            '通常一个主发现，相关时少量补充；稳定周可以更短，不凑新意、进步或任务。',
            '尊重本人感受，保留积极进展；不因均值少了十来分钟就认定偏短，不由一晚睡得较长推断补觉。',
            '旧主题仅查重，不作健康事实；建议、表达意愿、明确接受、拒绝要分开，不能假称用户答应。',
            '标题和摘要先交付发现；正文解释并兑现，不能卖关子、制造焦虑或反复解释缺哪些数据。'
        ]}
    evidence['report_content_version'] = VERSION
    return evidence


def baseline_profile(history, facts, blocked=()):
    result = {}
    for key, item in history.items():
        valid = [(d, v) for d, v in zip(item['days'], item['values']) if v is not None]
        if key in blocked or len(valid) < 14:
            continue
        if (date.fromisoformat(valid[-1][0])-date.fromisoformat(valid[0][0])).days < 13:
            continue
        values = [v for _, v in valid]; lower, _, upper = quantiles(values, n=4, method='inclusive')
        refs = {}
        for label, value in [('median', median(values)), ('lower', lower), ('upper', upper)]:
            ref = f'baseline.{key}.{label}'; facts[ref] = f'{value:.1f}{item["unit"]}'; refs[label] = ref
        facts[f'baseline.{key}.days'] = f'{len(valid)}天'
        result[key] = {'start': item['days'][0], 'end': item['days'][-1], 'valid_days': len(valid),
                       'fact_keys': refs, 'meaning': '本人报告期之前的历史分布，中间范围不是医学正常范围。'}
    return result


def writer_context(evidence):
    context = deepcopy(evidence)
    context.pop('historical_series', None)
    context.pop('previous_series', None)
    # Full-period facts, quality and workouts remain selectable, not just three metrics.
    context.pop('insight_candidates', None)
    return context


def validate_story(value, evidence):
    story = value.get('story')
    if not isinstance(story, dict) or not isinstance(value.get('title'), str):
        raise ValueError('missing_editorial_plan_or_title')
    candidates = {c['id'] for c in evidence.get('editorial', {}).get('candidates', [])}
    if story.get('topic_id') not in candidates:
        raise ValueError('unknown_editorial_topic')
    refs = story.get('fact_keys'); notes = story.get('note_ids', [])
    if not isinstance(refs, list) or not 1 <= len(refs) <= 12:
        raise ValueError('invalid_story_fact_keys')
    unknown = [str(k) for k in refs if not isinstance(k,str) or k not in evidence['facts']]
    if unknown:
        raise ValueError('invalid_story_fact_keys:' + ','.join(unknown)[:250])
    if not isinstance(notes, list) or any(n not in evidence['editorial']['note_ids'] for n in notes):
        raise ValueError('unknown_owner_note')
    used = set(re.findall(r'\{\{fact:([A-Za-z0-9_.:-]+)\}\}', value.get('html', '') + value.get('summary', '')))
    if not set(refs).issubset(used):
        raise ValueError('story_not_supported_in_report:' + ','.join(sorted(set(refs)-used))[:250])
    return {'topic_id': story['topic_id'], 'fact_keys': refs, 'note_ids': notes,
            'title': value['title'][:180]}
