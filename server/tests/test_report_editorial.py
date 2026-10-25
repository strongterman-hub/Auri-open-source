"""Editorial coverage, provenance and visuals use fictional data only."""
import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest

from test_reports import container, START, END, add_steps, output
from app.reports.editorial import baseline_profile, prepare_editorial, readable, validate_story, writer_context
from app.reports.charts import sleep_schedule, baseline_chart
from app.reports.facts import collect_facts
from app.reports.html import render_output
from app.schemas.health import HealthMetricIn, HealthSyncRequest


@pytest.mark.parametrize('raw,expected', [('480分钟','8小时'),('469分钟','7小时49分'),('12分钟','12分钟'),('-90分钟','-1小时30分'),('6000.4步','6000步')])
def test_readable_units(raw, expected):
    assert readable(raw) == expected


def test_personal_baseline_excludes_report_period_and_requires_history(container):
    rows=[HealthMetricIn(metric_type='STEPS',day=str(START+timedelta(days=i)),value1=10000 if i>=0 else 4000) for i in range(-28,7)]
    container.health_service.sync('u',HealthSyncRequest(from_day=str(START-timedelta(days=28)),to_day=str(END),metrics=rows))
    e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai')
    assert e['facts']['baseline.STEPS.median']=='4000.0步'
    assert e['facts']['STEPS.avg']=='10000步'
    assert e['baseline']['STEPS']['end']==str(START-timedelta(days=1))
    chart=baseline_chart(e,'STEPS')
    assert '10000步' in chart and '不是医学正常范围' in chart


def test_baseline_requires_coverage_and_same_source():
    days=[str(START-timedelta(days=28-i)) for i in range(28)]
    history={'STEPS':{'days':days,'values':[4000]*13+[None]*15,'unit':'步'}}
    assert not baseline_profile(history,{})
    history['STEPS']['values']=[4000]*28
    assert not baseline_profile(history,{},blocked=['STEPS'])
    assert baseline_profile(history,{})['STEPS']['valid_days']==28


def test_full_context_keeps_scores_workouts_and_personal_story(container):
    add_steps(container,'u');e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai')
    for key,unit,val in [('SLEEP_SCORE','分',85),('WORKOUT','分钟',30)]:
        e['series'][key]={'days':e['series']['STEPS']['days'],'values':[val]*7,'unit':unit,'label':key}
        e['facts'][key+'.avg']=str(val)+unit;e['facts'][key+'.days']='7/7天'
    e['facts']['WORKOUT.total']='210分钟'
    e['owner_notes']=[{'id':'n1','text':'最近睡得挺好','intent':'unspecified'}]
    prepare_editorial(e,[{'topic_id':'sleep'}]);original=deepcopy(e)
    context=writer_context(e)
    assert {'sleep_quality','movement','personal_context'} <= {c['id'] for c in context['editorial']['candidates']}
    assert 'SLEEP_SCORE' in context['series'] and 'WORKOUT' in context['series']
    assert context['owner_notes'][0]['text']=='最近睡得挺好'
    assert 'historical_series' not in context and e==original
    assert prepare_editorial(e,e['editorial']['topic_history'])==original
    assert e['facts']['STEPS.delta_abs']==e['facts']['STEPS.delta'].lstrip('-')


def test_schedule_midnight_and_missing_dates_do_not_become_zero_sleep():
    days=[str(START+timedelta(days=i)) for i in range(7)]
    e={'series':{'MAIN_SLEEP':{'days':days}},'sleep_windows':[{'day':days[0],'start_minutes':-30,'end_minutes':450}]}
    rendered=sleep_schedule(e)
    assert '23:30—07:30' in rendered and rendered.count('<rect ')==1
    assert rendered.count('>—</text>')==6
    e['sleep_windows'][0]['end_minutes']=-40
    with pytest.raises(ValueError,match='invalid_sleep_windows'):sleep_schedule(e)


@pytest.mark.parametrize('mutation,error', [
    (lambda v:v['story'].update(topic_id='invented'),'unknown_editorial_topic'),
    (lambda v:v['story'].update(note_ids=['someone-else']),'unknown_owner_note'),
    (lambda v:v['story'].update(fact_keys=['fake']),'invalid_story_fact_keys')])
def test_editorial_references_are_validated(container,mutation,error):
    add_steps(container,'u');e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai');v=output()
    mutation(v)
    with pytest.raises(ValueError,match=error):validate_story(v,e)


def test_short_stable_report_has_specific_title_and_evidence(container):
    add_steps(container,'u');e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai')
    v=output();v['title']='走动节奏可以继续回看'
    v['html']='<p class="lead">本期步数记录日均 {{fact:STEPS.avg}}。这份回顾只说明日常走动，不能据此判断训练强度或整体恢复状态。</p>{{chart:STEPS:bar}}<p>如果日常感觉舒服，可以按自己的安排继续活动；无需为了周报额外增加任务。</p>'
    summary,html=render_output(v,e,require_story=True)
    assert '<h1>走动节奏可以继续回看</h1>' in html
    v['title']='<script>恶意</script>'
    with pytest.raises(ValueError,match='title_length_or_markup'):render_output(v,e,require_story=True)


def test_story_cannot_list_unused_supporting_facts(container):
    add_steps(container,'u');e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai');v=output()
    v['story']['fact_keys'].append('STEPS.min')
    with pytest.raises(ValueError,match='story_not_supported_in_report'):validate_story(v,e)


def test_fact_units_are_not_repeated_in_prose(container):
    add_steps(container,'u');e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai');v=output()
    v['summary']='本期记录日均 {{fact:STEPS.avg}}步，可以回看走动节奏。'
    with pytest.raises(ValueError,match='repeated_fact_unit:summary'):render_output(v,e)


def test_raw_fact_references_also_use_readable_display(container):
    add_steps(container,'u');e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai');v=output()
    e['facts']['STEPS.avg']='4666.7步';e['facts']['MAIN_SLEEP.avg']='490分钟'
    v['html']+='<p>睡眠记录日均 {{fact:MAIN_SLEEP.avg}}。</p>'
    document=render_output(v,e,require_story=True)[1]
    assert '4667步' in document and '8小时10分' in document
    assert '4666.7步' not in document and '490分钟' not in document


def test_joint_story_accepts_readable_fact_aliases(container):
    add_steps(container,'u');e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai');v=output()
    e['facts']['MAIN_SLEEP.avg']='480分钟';e['facts']['read.MAIN_SLEEP.avg']='8小时'
    e['body_state_evidence']['synthesis_ready']=True
    v['story']['topic_id']='body_state';v['html']+='<p>主睡眠记录日均 {{fact:read.MAIN_SLEEP.avg}}，可与走动放在一起回看。</p>'
    assert render_output(v,e)[1]


def test_notes_keep_user_provenance_negation_and_period(container):
    srv=container.weekly_report_service
    def msg(text,day,role='user'):
        return {'role':role,'content':text,'timestamp':day+'T04:00:00Z','id':'message-'+text}
    async def sessions(uid):
        assert uid=='u'
        return [SimpleNamespace(id='s',messages=[msg('我决定坚持运动','2026-09-27'),msg('我不想增加运动','2026-09-28'),msg('建议每天散步','2026-09-28','assistant'),msg('最近睡得挺好','2026-09-29'),msg('睡得不好','2026-10-05')])]
    srv.sessions=SimpleNamespace(list_by_user=sessions)
    notes=asyncio.run(srv.owner_notes('u',{'week_start':str(START),'week_end':str(END),'timezone':'Asia/Shanghai'}))
    assert len(notes)==3 and [n['intent'] for n in notes]==['accepted','declined','unspecified']
    assert not notes[0]['in_period'] and notes[1]['in_period']
    assert all(n['source_role']=='user' and n['source_session_id']=='s' for n in notes)
    assert [n['id'] for n in notes]==['n1','n2','n3']


def test_topic_history_is_account_and_time_scoped(container):
    store=container.weekly_report_service.store
    for uid,offset in [('u',-7),('other',-7),('u',0)]:
        start=START+timedelta(days=offset);row=store.queue(uid,str(start),str(start+timedelta(days=6)),'Asia/Shanghai')
        store.claim(row['id']);store.complete(row['id'],'摘要','正文',{'report_story':{'topic_id':'activity','title':'过去的走动'}})
    history=store.topic_history('u',str(START))
    assert len(history)==1 and history[0]['week_start']==str(START-timedelta(days=7))
    assert '不是用户原话' in history[0]['use']
