import asyncio
import json
import time
from datetime import date, datetime, timezone, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.reports import router
from app.config import Settings
from app.core.errors import register_exception_handlers
from app.reports.facts import collect_facts, recent_week
from app.reports.html import render_output, sanitize, chart
from app.reports.store import ReportStore
from app.reports.tool import HealthReportTool, SendHealthReportTool
from app.schemas.health import HealthMetricIn, HealthSampleIn, HealthSyncRequest
from app.state.container import create_container


START, END = date(2026, 9, 28), date(2026, 10, 4)


@pytest.fixture
def container(tmp_dir):
    return create_container(Settings(_env_file=None, data_dir=tmp_dir, llm_provider='echo', weekly_reports_enabled=True, proactive_enabled=False, event_extraction_enabled=False))


def add_steps(container, uid, current=7, previous=7):
    from datetime import timedelta
    rows = [HealthMetricIn(metric_type='STEPS', day=(START + timedelta(days=i)).isoformat(), value1=0 if i == 0 else 6000) for i in range(current)]
    rows += [HealthMetricIn(metric_type='STEPS', day=(START - timedelta(days=7-i)).isoformat(), value1=4000) for i in range(previous)]
    container.health_service.sync(uid, HealthSyncRequest(from_day='2026-09-21', to_day=END.isoformat(), metrics=rows, metric_types=['STEPS']))


def output():
    return {'title':'回看这一周的走动','story':{'topic_id':'activity','fact_keys':['STEPS.avg'],'note_ids':[]},
            'summary': '这一周的活动记录已经整理好了，回顾里有每天的变化，也可以一起看看接下来想保持什么。',
            'html': '<section class="card"><h2>慢慢回看活动节奏</h2><p>记录覆盖 {{fact:STEPS.days}}，记录日平均 {{fact:STEPS.avg}}。</p>{{chart:STEPS:bar}}<table><tr><th>项目</th><th>记录</th></tr><tr><td>本周记录日均值</td><td>{{fact:STEPS.avg}}</td></tr></table><p>' + ('这些记录可以帮助回看这一周的活动。每天的步数与设备佩戴、同步情况都有关系，缺少记录的日期需要留空。可以先看看自己愿意保持的习惯，再选择一个适合日常的小调整。' * 5) + '</p></section>'}


class Model:
    def __init__(self, value=None):
        self.calls = 0
        self.value = value or output()
    async def complete_structured(self, messages, max_tokens=8192):
        self.calls += 1
        if '事实审查员' in messages[0]['content']:
            return SimpleNamespace(finish_reason='stop', content='{"valid":true,"reason":"一致"}')
        return SimpleNamespace(finish_reason='stop', content=json.dumps(self.value, ensure_ascii=False))


@pytest.mark.parametrize('stamp,zone,start', [
    ('2026-10-16T06:00:00+00:00', 'Asia/Shanghai', '2026-10-09'),
    ('2026-10-15T15:59:00+00:00', 'Asia/Shanghai', '2026-10-08'),
    ('2026-10-15T16:00:00+00:00', 'Asia/Shanghai', '2026-10-09'),
    ('2026-10-04T17:00:00+00:00', 'Asia/Shanghai', '2026-09-28'),
    ('2026-10-05T00:00:00+00:00', 'America/Los_Angeles', '2026-09-27'),
    ('2026-01-01T00:00:00+00:00', 'Asia/Shanghai', '2025-12-25'),
    ('2026-03-08T10:30:00+00:00', 'America/Los_Angeles', '2026-03-01'),
])
def test_rolling_seven_local_dates(stamp, zone, start):
    begin, end = recent_week(datetime.fromisoformat(stamp), zone)
    assert begin.isoformat() == start and (end - begin).days == 6
    from zoneinfo import ZoneInfo
    assert end == datetime.fromisoformat(stamp).astimezone(ZoneInfo(zone)).date() - timedelta(days=1)


def test_zero_missing_coverage_and_comparison(container):
    add_steps(container, 'u', current=5)
    e = collect_facts(container.health_service, 'u', START, END, 'Asia/Shanghai')
    assert e['series']['STEPS']['values'] == [0, 6000, 6000, 6000, 6000, None, None]
    assert e['facts']['STEPS.avg'] == '4800步'
    assert e['facts']['STEPS.delta'] == '800步'
    add_steps(container, 'sparse', current=4)
    sparse = collect_facts(container.health_service, 'sparse', START, END, 'Asia/Shanghai')
    assert 'STEPS.delta' not in sparse['facts']


def test_main_sleep_actual_time_and_explicit_nap(container):
    samples = [HealthSampleIn(metric_type='SLEEP_SESSION', day='2026-09-28', bucket_start='2026-09-27T15:00:00Z', bucket_end='2026-09-27T23:00:00Z', value1=480, value2=420, value4='main'),
               HealthSampleIn(metric_type='SLEEP_SESSION', day='2026-09-28', bucket_start='2026-09-28T05:00:00Z', bucket_end='2026-09-28T06:00:00Z', value1=60, value2=40, value4='nap')]
    container.health_service.sync('u', HealthSyncRequest(from_day='2026-09-27', to_day='2026-09-28', samples=samples))
    e = collect_facts(container.health_service, 'u', START, END, 'Asia/Shanghai')
    assert e['facts']['MAIN_SLEEP.avg'] == '420分钟'
    assert e['facts']['NAP.total'] == '40分钟'
    assert not any(k.startswith('RECOVERY_SCORE') for k in e['facts'])


@pytest.mark.parametrize('attack', [
    '<script>alert(1)</script>', '<img src="https://evil.example" onerror="steal()">',
    '<iframe src="https://evil.example"></iframe>', '<svg><foreignObject><script>steal()</script></foreignObject></svg>',
    '<a href="javascript:steal()">链接</a>', '<div style="background:url(https://evil.example)" onclick="steal()">正常内容</div>',
])
def test_sanitizer_no_active_or_remote_content(attack):
    safe = sanitize(attack)
    for term in ('script', 'iframe', 'svg', 'onerror', 'onclick', 'href', 'src=', 'style=', 'steal', 'evil.example'):
        assert term not in safe


def test_real_values_bound_to_html_and_summary(container):
    add_steps(container, 'u')
    e = collect_facts(container.health_service, 'u', START, END, 'Asia/Shanghai')
    summary, document = render_output(output(), e)
    assert len(summary) <= 80 and '{{' not in document
    assert '<svg' in document and '<table' in document
    assert "default-src 'none'" in document
    from app.reports.editorial import readable
    assert readable(e['facts']['STEPS.avg']) in document


@pytest.mark.parametrize('change', ['unknown_fact', 'raw_number', 'short', 'long_summary', 'unknown_chart'])
def test_untrusted_ai_output_rejected(container, change):
    add_steps(container, 'u')
    e = collect_facts(container.health_service, 'u', START, END, 'Asia/Shanghai')
    value = output()
    if change == 'unknown_fact': value['html'] += '{{fact:made_up.avg}}'
    if change == 'raw_number': value['html'] += '<p>活动提升了99%</p>'
    if change == 'short': value['html'] = '<p>简短</p>'
    if change == 'long_summary': value['summary'] = '长' * 81
    if change == 'unknown_chart': value['html'] += '{{chart:UNKNOWN:bar}}'
    with pytest.raises(ValueError): render_output(value, e)


def test_chart_does_not_bridge_missing_dates():
    doc = chart({'label': '记录', 'unit': '步', 'days': ['09-28'] * 7, 'values': [1, 2, None, 4, None, 6, 7]}, 'line')
    assert doc.count('<line') == 2
    assert doc.count('>—<') == 2


def test_store_claim_cache_retry_and_delete(tmp_dir):
    store = ReportStore(tmp_dir / 'reports.db')
    row = store.queue('u', str(START), str(END), 'Asia/Shanghai')
    assert store.queue('u', str(START), str(END), 'UTC')['id'] == row['id']
    assert store.claim(row['id']) and not store.claim(row['id'])
    store.complete(row['id'], '摘要', 'html', {})
    store.delivered(row['id'])
    updated = store.queue('u', str(START), str(END), 'Asia/Shanghai', refresh=True)
    assert updated['notified_at'] is not None and updated['html'] == 'html'
    store.delete_user('u')
    assert store.get('u', row['id']) is None
    assert not store.complete(row['id'], '摘要', 'html', {})


def test_expired_final_lease_becomes_retryable_failure(tmp_dir):
    store = ReportStore(tmp_dir / 'reports.db')
    row = store.queue('u', str(START), str(END), 'UTC')
    with store.connect() as db:
        db.execute("UPDATE weekly_reports SET status='generating',attempts=3,lease_until=0 WHERE id=?", (row['id'],))
    assert store.pending() == []
    assert store.get('u', row['id'])['status'] == 'failed'
    store.queue('u', str(START), str(END), 'UTC', refresh=True)
    assert store.claim(row['id'])


def test_generation_cache_and_unchanged_refresh(container):
    add_steps(container, 'u')
    srv = container.weekly_report_service
    srv.llm = Model()
    row = srv.store.queue('u', str(START), str(END), 'Asia/Shanghai')
    asyncio.run(srv.generate(row))
    ready = srv.store.get('u', row['id'])
    assert ready['status'] == 'ready' and srv.llm.calls == 2
    asyncio.run(srv.generate(ready))
    row = srv.store.queue('u', str(START), str(END), 'Asia/Shanghai', refresh=True)
    asyncio.run(srv.generate(row))
    assert srv.llm.calls == 2 and srv.store.get('u', row['id'])['revision'] == 1


def test_fact_review_failure_never_publishes_report(container):
    class RejectingModel(Model):
        async def complete_structured(self, messages, max_tokens=8192):
            if '事实审查员' in messages[0]['content']:
                return SimpleNamespace(finish_reason='stop', content='{"valid":false,"reason":"日期列使用了测量值"}')
            return await super().complete_structured(messages, max_tokens)
    add_steps(container, 'u')
    srv = container.weekly_report_service
    srv.llm = RejectingModel()
    row = srv.store.queue('u', str(START), str(END), 'Asia/Shanghai')
    asyncio.run(srv.generate(row))
    row = srv.store.get('u', row['id'])
    assert row['status'] == 'failed' and row['html'] == '' and row['notified_at'] is None


def test_routes_are_owner_scoped_and_old_clients_unsupported(container):
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(router, prefix='/v1')
    app.state.container = container
    users = [container.auth_store.create_user(f'{n}@example.com', 'hash') for n in ('u', 'v')]
    tokens = [container.auth_store.create_token(u.id) for u in users]
    headers = [{'Authorization': 'Bearer ' + t} for t in tokens]
    row = container.weekly_report_service.store.queue(users[0].id, str(START), str(END), 'Asia/Shanghai')
    with TestClient(app) as client:
        assert client.get('/v1/health/reports').status_code == 401
        assert client.get('/v1/health/reports/' + row['id'], headers=headers[1]).status_code == 404
        assert client.get('/v1/health/reports/' + row['id'], headers=headers[0]).status_code == 200
        store = container.weekly_report_service.store
        assert store.claim(row['id'])
        assert store.complete(row['id'], '一份简短摘要', '<p>仅详情页读取的完整正文</p>', {})
        preview = client.get('/v1/health/reports/' + row['id'] + '?include_html=false', headers=headers[0]).json()
        assert preview['summary'] == '一份简短摘要' and preview['revision'] == 1 and 'html' not in preview
        detail = client.get('/v1/health/reports/' + row['id'], headers=headers[0]).json()
        assert detail['html'] == '<p>仅详情页读取的完整正文</p>'
        assert client.get('/v1/health/reports/' + row['id'] + '?include_html=false', headers=headers[1]).status_code == 404
        assert client.post('/v1/health/reports/client', json={'version_code': 39}, headers=headers[0]).status_code == 422
        assert client.post('/v1/health/reports/weekly', json={}, headers=headers[0]).status_code == 409
        assert client.post('/v1/health/reports/client', json={'version_code': 40}, headers=headers[0]).status_code == 200
        assert client.put('/v1/health/reports/settings', json={'enabled': False}, headers=headers[0]).json()['enabled'] is False
        assert client.post('/v1/health/reports/weekly', json={}, headers=headers[0]).status_code == 202
    assert json.loads(asyncio.run(HealthReportTool(container.weekly_report_service.store, users[1].id).execute(row['id'])))['error']


def test_sleep_defers_delivery_and_fixed_id_survives_retry(container, monkeypatch):
    import app.reports.service as module
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 5, 6, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(module, 'datetime', Clock)
    user = container.auth_store.create_user('delivery@example.com', 'hash')
    srv = container.weekly_report_service
    row = srv.store.queue(user.id, str(START), str(END), 'Asia/Shanghai')
    srv.store.claim(row['id']); srv.store.complete(row['id'], '这一周的活动记录已经整理好，可以一起看看其中的变化。', 'html', {})
    row = srv.store.get(user.id, row['id'])
    srv.store.register(user.id, 40)
    container.proactive_settings_store.set_enabled(user.id, 'default', True)
    container.proactive_engine._is_do_not_disturb = lambda *args: True
    container.proactive_engine._is_pacing_blocked = lambda *args: False
    asyncio.run(srv.notify(row))
    assert srv.store.get(user.id, row['id'])['notified_at'] is None
    container.proactive_engine._is_do_not_disturb = lambda *args: False
    asyncio.run(srv.notify(row)); asyncio.run(srv.notify(row))
    sessions = asyncio.run(container.session_service.list_by_user(user.id))
    messages = [m for s in sessions for m in s.messages]
    assert len(messages) == 1
    assert messages[0]['content'][1]['actions'][0]['report_id'] == row['id']


def test_chat_request_selects_rolling_window_not_latest_saved(container, monkeypatch):
    import app.reports.service as module
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 16, 6, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(module, 'datetime', Clock)
    srv = container.weekly_report_service
    srv.timezone_resolver = SimpleNamespace(get=lambda _: 'Asia/Shanghai')
    srv.store.register('u', 42)
    old = srv.store.queue('u', '2026-10-05', '2026-10-11', 'Asia/Shanghai')
    srv.store.claim(old['id']); srv.store.complete(old['id'], '旧周期摘要', '<p>原报告</p>', {})
    row = srv.request_for_chat('u')
    assert row['id'] != old['id'] and srv.store.get('u',old['id'])['html']=='<p>原报告</p>'
    assert (row['week_start'], row['week_end']) == ('2026-10-09', '2026-10-15')
    explicit = srv.request_for_chat('u', '2026-09-23')
    assert (explicit['week_start'], explicit['week_end']) == ('2026-09-23', '2026-09-29')
    with pytest.raises(ValueError):
        srv.request_for_chat('u', '2026-10-10')
    with pytest.raises(ValueError):
        srv.request_for_chat('u', '2026-10-17')
    with pytest.raises(ValueError):
        srv.request_for_chat('other')


@pytest.mark.parametrize('mode', ['send', 'stream', 'batch', 'api'])
def test_explicit_resend_persists_new_messages_with_same_cached_report(container, mode):
    from app.agent.runner import AgentTurn, AgentStreamEvent
    from app.session.models import SessionCreate
    srv = container.weekly_report_service
    user = container.auth_store.create_user('resend@example.com', 'hash')
    srv.store.register(user.id, 42)
    row = srv.request_for_chat(user.id)
    srv.store.claim(row['id'])
    srv.store.complete(row['id'], '这一周的报告', '<html>cached</html>', {})
    srv.store.delivered(row['id'])
    class Runner:
        async def run(self, context):
            tool = next(t for t in context.tools if t.name == 'send_health_report')
            result = await tool.execute()
            assert json.loads(result)['cached'] is True
            return AgentTurn(text='这一周的报告发来了。', tool_results=[{'name': tool.name, 'result': result}])
        async def run_stream(self, context):
            turn = await self.run(context)
            yield AgentStreamEvent(content=turn.text)
            yield AgentStreamEvent(done=True, turn=turn)
    container.agent_service.runner = Runner()
    async def scenario():
        session = await container.session_service.ensure(SessionCreate(user_id=user.id))
        for index in range(2):
            if mode == 'api':
                from app.api.routes.agent import router as agent_router
                app = FastAPI()
                app.state.container = container
                app.include_router(agent_router, prefix='/v1/agent')
                token = container.auth_store.create_token(user.id)
                with TestClient(app) as client:
                    response = client.post('/v1/agent/' + session.id + '/messages', json={'content': '再发一下我上周的健康周报'}, headers={'Authorization': 'Bearer ' + token})
                    assert response.status_code == 200
                    assert response.json()['message']['content'][1]['actions'][0]['report_id'] == row['id']
            elif mode == 'send':
                await container.agent_service.send(session.id, '再发一下我上周的健康周报')
            elif mode == 'stream':
                async for _ in container.agent_service.send_stream(session.id, '再发一下我上周的健康周报'):
                    pass
            else:
                saved = await container.session_service.get(session.id)
                mid = 'user-' + str(index)
                saved.messages.append({'id': mid, 'role': 'user', 'content': '我上一周的健康情况', 'timestamp': datetime.now(timezone.utc).isoformat()})
                await container.session_service.save(saved)
                await container.agent_service.reply_to_messages(session.id, [mid], reply_job_id='job-' + str(index))
        saved = await container.session_service.get(session.id)
        replies = [m for m in saved.messages if m['role'] == 'assistant']
        assert len(replies) == 2 and replies[0]['id'] != replies[1]['id']
        for message in replies:
            assert '按钮' not in message['content'][0]['text']
            assert message['content'][1]['actions'][0]['report_id'] == row['id']
    asyncio.run(scenario())
    assert srv.store.get(user.id, row['id'])['revision'] == 1
    assert srv.store.waiting_delivery() == []


def test_pending_chat_report_acknowledges_only_after_message_is_saved(container):
    srv = container.weekly_report_service
    srv.store.register('u', 42)
    tool = SendHealthReportTool(srv.store, 'u', lambda start=None: srv.request_for_chat('u', start))
    payload = json.loads(asyncio.run(tool.execute()))
    row = srv.store.get('u', payload['chat_report_id'])
    assert row['status'] == 'queued' and row['notified_at'] is None
    assert srv.store.get('other', row['id']) is None
    tool.acknowledge()
    assert srv.store.get('u', row['id'])['notified_at'] is not None


def test_untrusted_tool_results_cannot_attach_report():
    from app.services.agent_service import AgentService
    assert AgentService._assistant_message_content('原文', [], [{'name': 'health_stats', 'result': '{"chat_report_id":"' + 'a'*32 + '"}'}]) == '原文'
    assert AgentService._assistant_message_content('原文', [], [{'name': 'send_health_report', 'result': '{"chat_report_id":"../other"}'}]) == '原文'


@pytest.mark.parametrize('stamp', [1791165600, 1791165600000])
def test_real_sync_timestamp_units_collect_and_generate(container, stamp):
    uid = 'timestamp-fixture'
    metric = HealthMetricIn(metric_type='STEPS', day=str(START), value1=6000, updated_at=stamp)
    container.health_service.sync(uid, HealthSyncRequest(from_day=str(START), to_day=str(END), metrics=[metric], metric_types=['STEPS']))
    evidence = collect_facts(container.health_service, uid, START, END, 'Asia/Shanghai')
    assert evidence['facts']['data.updated_at'] == '2026-10-05 10:00'
    srv = container.weekly_report_service
    srv.llm = Model()
    row = srv.store.queue(uid, str(START), str(END), 'Asia/Shanghai')
    asyncio.run(srv.generate(row))
    saved = srv.store.get(uid, row['id'])
    assert saved['status'] == 'ready' and saved['html'] and srv.llm.calls == 2


def test_mixed_and_malformed_sync_timestamps():
    from zoneinfo import ZoneInfo
    from app.reports.facts import updated_time
    zone = ZoneInfo('Asia/Shanghai')
    # The later record is in seconds, the older record in milliseconds.
    rows = [SimpleNamespace(updated_at=v) for v in [1791165600000, 1791252000, None, float('inf'), 10**30, 'bad']]
    assert updated_time(rows, zone) == '2026-10-06 10:00'
    assert updated_time(rows[2:], zone) == '未提供同步时间'


def test_missing_data_is_terminal_after_first_attempt(container):
    srv = container.weekly_report_service
    srv.llm = Model()
    row = srv.store.queue('empty', str(START), str(END), 'Asia/Shanghai')
    asyncio.run(srv.generate(row))
    saved = srv.store.get('empty', row['id'])
    assert saved['status'] == 'failed' and saved['attempts'] == 1 and saved['retry_at'] == 0
    assert '没有可用健康记录' in saved['error'] and 'Credits' not in saved['error']
    assert not srv.store.pending() and srv.llm.calls == 0


@pytest.mark.parametrize('failure,terminal,expected', [('credits', True, 'Credits'), ('network', False, '服务暂时不可用')])
def test_generation_error_reason_and_retry_policy(container, failure, terminal, expected):
    from app.billing.service import InsufficientCreditsError
    class FailureModel(Model):
        async def complete_structured(self, *args, **kwargs):
            raise InsufficientCreditsError() if failure == 'credits' else OSError('synthetic network failure')
    add_steps(container, 'u')
    srv = container.weekly_report_service
    srv.llm = FailureModel()
    row = srv.store.queue('u', str(START), str(END), 'Asia/Shanghai')
    before = time.time()
    asyncio.run(srv.generate(row))
    saved = srv.store.get('u', row['id'])
    assert saved['status'] == ('failed' if terminal else 'queued') and expected in saved['error']
    if not terminal:
        assert before + 55 <= saved['retry_at'] <= time.time() + 65
        assert 'Credits' not in saved['error']
        with srv.store.connect() as db:
            db.execute('UPDATE weekly_reports SET retry_at=0 WHERE id=?', (row['id'],))
        assert srv.store.claim(row['id'])
        assert srv.store.get('u', row['id'])['error'] is None


def test_whole_generation_deadline_cancels_stalled_work(container):
    srv = container.weekly_report_service
    srv.generation_timeout_seconds = 0.01
    cancelled = []
    async def stalled(row):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
    srv._generate = stalled
    row = srv.store.queue('u', str(START), str(END), 'Asia/Shanghai')
    asyncio.run(srv.generate(row))
    saved = srv.store.get('u', row['id'])
    assert cancelled and saved['status'] == 'queued' and '超时' in saved['error']
    assert 'Credits' not in saved['error']


def test_only_closed_days_are_reported_without_period_boilerplate(container):
    add_steps(container, 'u')
    with pytest.raises(ValueError, match='report_period_must_end_before_today'):
        collect_facts(container.health_service, 'u', START, END, 'Asia/Shanghai', as_of=datetime(2026,10,4,6,tzinfo=timezone.utc))
    closed = collect_facts(container.health_service, 'u', START, END, 'Asia/Shanghai', as_of=datetime(2026,10,5,6,tzinfo=timezone.utc))
    assert 'STEPS.delta' in closed['facts'] and 'period.note' not in closed['facts']
    _, html = render_output(output(), closed)
    assert '2026-09-28 — 2026-10-04' in html
    for phrase in ('尚未结束','记录不完整','本报告排除今天'):
        assert phrase not in html


def test_original_report_resend_by_id_keeps_dates_and_never_queues(container):
    store = container.weekly_report_service.store
    row = store.queue('u', '2026-09-28','2026-10-04','Asia/Shanghai')
    store.claim(row['id']); store.complete(row['id'],'已保存摘要','<p>cached</p>',{})
    def no_queue(*args):raise AssertionError('resend must not generate another period')
    tool = SendHealthReportTool(store,'u',no_queue)
    result = json.loads(asyncio.run(tool.execute(report_id=row['id'])))
    assert result['cached'] and result['from_day']=='2026-09-28' and result['to_day']=='2026-10-04'
    assert 'error' in json.loads(asyncio.run(SendHealthReportTool(store,'other',no_queue).execute(report_id=row['id'])))
    assert 'error' in json.loads(asyncio.run(tool.execute(week_start='2026-10-01',report_id=row['id'])))
    assert store.get('u',row['id'])['revision']==1 and len(store.list('u'))==1


def test_automatic_cadence_uses_requests_and_resends_to_avoid_daily_reports(container):
    store=container.weekly_report_service.store
    assert store.automatic_due('u',1000)
    row=store.queue('u','2026-09-28','2026-10-04','Asia/Shanghai')
    with store.connect() as db:db.execute('UPDATE weekly_reports SET created_at=1000,notified_at=NULL WHERE id=?',(row['id'],))
    assert not store.automatic_due('u',1000+86400)
    assert not store.automatic_due('u',1000+7*86400-1)
    assert store.automatic_due('u',1000+7*86400)
    with store.connect() as db:db.execute('UPDATE weekly_reports SET notified_at=? WHERE id=?',(1000+6*86400,row['id']))
    assert not store.automatic_due('u',1000+7*86400)


def test_scheduler_creates_rolling_window_once_not_each_day(container):
    from unittest.mock import AsyncMock
    srv=container.weekly_report_service
    user=container.auth_store.create_user('rolling-scheduler@example.com','hash')
    srv.store.register(user.id,44)
    srv.timezone_resolver=SimpleNamespace(get=lambda _: 'Asia/Shanghai')
    container.proactive_settings_store.set_enabled(user.id,'default',True)
    add_steps(container,user.id)
    srv.generate=AsyncMock()
    now=datetime(2026,10,5,4,tzinfo=timezone.utc)
    asyncio.run(srv.tick(now))
    rows=srv.store.list(user.id)
    assert len(rows)==1 and (rows[0]['week_start'],rows[0]['week_end'])==('2026-09-28','2026-10-04')
    with srv.store.connect() as db:db.execute('UPDATE weekly_reports SET created_at=? WHERE id=?',(now.timestamp(),rows[0]['id']))
    asyncio.run(srv.tick(now+timedelta(days=1)))
    assert len(srv.store.list(user.id))==1


def test_repaired_period_preserves_original_message_id_and_refresh(container):
    store=container.weekly_report_service.store
    row=store.queue('u','2026-10-10','2026-10-16','Asia/Shanghai')
    store.claim(row['id']);store.complete(row['id'],'摘要','<p>缓存</p>',{})
    with store.connect() as db:db.execute("UPDATE weekly_reports SET week_start='2026-10-09',week_end='2026-10-15' WHERE id=?",(row['id'],))
    fixed=store.queue('u','2026-10-09','2026-10-15','Asia/Shanghai')
    assert fixed['id']==row['id'] and fixed['html']=='<p>缓存</p>'
    refreshed=store.queue('u','2026-10-09','2026-10-15','Asia/Shanghai',refresh=True)
    assert refreshed['id']==row['id'] and refreshed['status']=='queued' and refreshed['html']=='<p>缓存</p>'
    assert len(store.list('u'))==1 and store.get('other',row['id']) is None


def test_insight_distribution_preserves_zero_and_does_not_fill_missing_days():
    from app.reports.insights import add_insight_candidates
    days=[str(START+timedelta(days=i)) for i in range(7)]
    def evidence(values):
        return {'facts':{},'series':{'STEPS':{'label':'步数','unit':'步','days':days,'values':values}},'coverage':{}}
    e=add_insight_candidates(evidence([0,2000,2500,3000,3500,9000,12000]))
    assert e['facts']['insight.STEPS.lowest_day']==days[0]
    assert e['facts']['insight.STEPS.top_pair_share']=='65.6%'
    assert any(c['id']=='insight.STEPS.distribution' for c in e['insight_candidates'])
    partial=add_insight_candidates(evidence([None,2000,2500,3000,3500,9000,12000]))
    assert partial['facts']['insight.STEPS.lowest_day']==days[1]
    assert 'insight.STEPS.top_pair_share' not in partial['facts']
    sparse=add_insight_candidates(evidence([None,None,None,None,0,9000,12000]))
    assert sparse['insight_candidates']==[]


def test_insight_segments_use_calendar_blocks_not_selected_extremes():
    from app.reports.insights import add_insight_candidates
    days=[str(START+timedelta(days=i)) for i in range(7)]
    e={'facts':{},'series':{'STEPS':{'unit':'步','days':days,'values':[2000,2000,2000,9999,6000,6000,6000]}},'coverage':{}}
    add_insight_candidates(e)
    assert e['facts']['insight.STEPS.early_avg']=='2000步'
    assert e['facts']['insight.STEPS.late_avg']=='6000步'
    e['series']['STEPS']['values'][0]=None;e['facts']={}
    add_insight_candidates(e)
    assert all(c['id']!='insight.STEPS.segments' for c in e['insight_candidates'])


def test_insight_bedtime_range_handles_midnight_and_requires_coverage():
    from app.reports.insights import add_insight_candidates
    days=[str(START+timedelta(days=i)) for i in range(7)]
    facts={'MAIN_SLEEP.days':'5/7天',**{'BEDTIME.'+d:t for d,t in zip(days,['23:30','00:15','01:30','00:00','23:50'])}}
    e=add_insight_candidates({'facts':facts,'series':{},'coverage':{}})
    assert e['facts']['insight.bedtime.span']=='120分钟'
    assert e['facts']['insight.bedtime.earliest_day']==days[0]
    assert e['facts']['insight.bedtime.latest_day']==days[2]
    assert e['insight_candidates'][0]['id']=='bedtime.range'
    small={'facts':{'MAIN_SLEEP.days':'2/7天','BEDTIME.'+days[0]:'23:30','BEDTIME.'+days[1]:'04:00'},'series':{},'coverage':{}}
    assert add_insight_candidates(small)['insight_candidates']==[]


def test_content_review_feedback_rewrites_statistical_listing_with_bounded_calls(container):
    srv=container.weekly_report_service;add_steps(container,'u')
    class ReviewModel(Model):
        def __init__(self):super().__init__();self.reviews=0;self.drafts=[]
        async def complete_structured(self,messages,max_tokens=8192):
            if '事实审查员' in messages[0]['content']:
                self.reviews+=1
                assert '有用性检查' in messages[0]['content']
                valid=self.reviews>1
                return SimpleNamespace(finish_reason='stop',content=json.dumps({'valid':valid,'reason':'只有均值清单，缺少解释和对应行动' if not valid else '通过'},ensure_ascii=False))
            self.drafts.append(messages[:])
            assert json.loads(messages[1]['content'])['editorial']['candidates']
            return SimpleNamespace(finish_reason='stop',content=json.dumps(output(),ensure_ascii=False))
    model=ReviewModel();srv.llm=model
    row=srv.store.queue('u',str(START),str(END),'Asia/Shanghai')
    asyncio.run(srv.generate(row))
    assert model.reviews==2 and len(model.drafts)==2 and srv.store.get('u',row['id'])['status']=='ready'
    assert '只有均值清单' in json.dumps(model.drafts[1],ensure_ascii=False)


def test_explicit_refresh_upgrades_legacy_content_once_then_reuses_unchanged_evidence(container):
    srv=container.weekly_report_service;add_steps(container,'u');model=Model();srv.llm=model
    e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai');e['owner_notes']=[]
    legacy=json.loads(json.dumps(e));legacy.pop('insight_candidates');legacy.pop('report_content_version')
    legacy['facts']={k:v for k,v in legacy['facts'].items() if not k.startswith('insight.')}
    row=srv.store.queue('u',str(START),str(END),'Asia/Shanghai');srv.store.claim(row['id']);srv.store.complete(row['id'],'旧摘要','旧正文',legacy)
    refreshed=srv.store.queue('u',str(START),str(END),'Asia/Shanghai',refresh=True)
    asyncio.run(srv.generate(refreshed))
    assert model.calls==2 and srv.store.get('u',row['id'])['revision']==2
    refreshed=srv.store.queue('u',str(START),str(END),'Asia/Shanghai',refresh=True)
    asyncio.run(srv.generate(refreshed))
    assert model.calls==2 and srv.store.get('u',row['id'])['revision']==2


def test_sparse_report_can_be_brief_without_padding(container):
    add_steps(container,'sparse',current=1,previous=0)
    e=collect_facts(container.health_service,'sparse',START,END,'Asia/Shanghai')
    value={'summary':'这期只看到局部活动记录，还不足以判断日常节奏，可以先从回看那天的安排开始。','html':'<section class="card"><h2>先看清记录对应的那一天</h2><p>记录覆盖 {{fact:STEPS.days}}，记录日均值为 {{fact:STEPS.avg}}。这只能描述该日设备记录的活动量，不能据此判断日常活动是否减少，也不能当作这一周的生活状态。如果你想了解活动是否均匀，可以下次留意普通工作日与休息日的差别，等有连续记录后再一起比较，而不必根据这次局部数值设定新的运动目标。</p>{{chart:STEPS:bar}}</section>'}
    assert len(value['html'])<400
    assert render_output(value,e)[0]==value['summary']


@pytest.mark.parametrize('joiner',['为','的'])
def test_numeric_identity_summary_is_rejected_even_with_valid_fact_keys(container, joiner):
    add_steps(container,'u');e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai')
    value=output();value['summary']='本期活动的记录日均 {{fact:STEPS.avg}} '+joiner+' {{fact:STEPS.avg}}，可以看看日常活动情况。'
    with pytest.raises(ValueError,match='repeated_fact_statement'):render_output(value,e)


def body_evidence(values, previous=None):
    from app.reports.body_state import add_body_state_evidence
    units={'STEPS':'步','MAIN_SLEEP':'分钟','RESTING_HEART_RATE':'次/分','HEART_RATE':'次/分','RECOVERY_SCORE':'分'}
    days=[str(START+timedelta(days=i)) for i in range(7)]
    series={key:{'days':days,'values':rows,'unit':units[key]} for key,rows in values.items()}
    facts={f'DAY.{day}':day for day in days}
    facts.update({key+'.'+d:str(v) for key,rows in values.items() for d,v in zip(days,rows) if v is not None})
    prior={key:{'days':[str(START-timedelta(days=7-i)) for i in range(7)],'values':rows,'unit':units[key]}
           for key,rows in (previous or {}).items()}
    return add_body_state_evidence({'facts':facts,'series':series,'previous_series':prior})


def test_body_synthesis_uses_shared_calendar_dates_and_keeps_conflicting_directions():
    e=body_evidence({'STEPS':[6000,8000,1000,6000,6000,12000,4000],
                     'MAIN_SLEEP':[560,450,None,500,440,450,460],
                     'RESTING_HEART_RATE':[76,70,69,74,65,69,None]})
    c=e['body_state_evidence']['comparisons'][0]
    assert c['before_dates']==['2026-09-28','2026-09-29']
    assert c['after_dates']==['2026-10-02','2026-10-03']
    assert e['facts']['body.within_week.STEPS.before']=='7000步'
    assert e['facts']['body.within_week.STEPS.after']=='9000步'
    assert e['facts']['body.within_week.MAIN_SLEEP.change']=='-60分钟'
    assert e['facts']['body.within_week.RESTING_HEART_RATE.change']=='-6次/分'
    assert [m['direction'] for m in c['metrics']]==['down','up','down']
    assert e['facts'][c['before_days_key']]=='2天'
    assert len(c['metrics'])==3 and e['report_content_version']==3
    # The packet describes evidence; it never assigns a diagnostic state or score.
    assert 'state' not in c and 'score' not in c


def test_body_synthesis_never_substitutes_recorded_hr_or_scores_for_resting_hr():
    e=body_evidence({'STEPS':[4000]*7,'MAIN_SLEEP':[480]*7,'HEART_RATE':[90]*7,'RECOVERY_SCORE':[95]*7})
    c=e['body_state_evidence']['comparisons'][0]
    assert {m['metric'] for m in c['metrics']}=={'STEPS','MAIN_SLEEP'}
    only_score=body_evidence({'STEPS':[4000]*7,'RECOVERY_SCORE':[95]*7,'HEART_RATE':[90]*7})
    assert not only_score['body_state_evidence']['synthesis_ready']


def test_body_comparison_requires_overlap_without_filling_missing_or_zero():
    e=body_evidence({'STEPS':[0,2000,None,None,None,7000,9000],
                     'MAIN_SLEEP':[None,None,480,470,460,450,440]})
    assert not e['body_state_evidence']['synthesis_ready']
    e=body_evidence({'STEPS':[0,2000,None,None,5000,7000,9000],
                     'MAIN_SLEEP':[480,470,None,None,460,450,440]})
    assert e['facts']['body.within_week.STEPS.before']=='1000步'
    assert e['facts']['body.within_week.STEPS.after']=='7000步'


def test_body_prior_comparison_requires_five_joint_dates_and_preserves_period():
    current={'STEPS':[6000]*7,'MAIN_SLEEP':[450]*7,'RESTING_HEART_RATE':[72]*7}
    prior={'STEPS':[4000]*7,'MAIN_SLEEP':[480]*7,'RESTING_HEART_RATE':[65]*5+[None,None]}
    e=body_evidence(current,prior)
    c=next(c for c in e['body_state_evidence']['comparisons'] if c['id']=='previous_week')
    assert c['before_dates']==['2026-09-21','2026-09-22','2026-09-23','2026-09-24','2026-09-25']
    assert e['facts']['body.previous_week.RESTING_HEART_RATE.change']=='7次/分'
    assert e['facts'][c['before_days_key']]=='5天'
    # Fewer resting days use sleep/activity only, without faking a physiological trend.
    prior['RESTING_HEART_RATE'][4]=None
    e=body_evidence(current,prior)
    c=next(c for c in e['body_state_evidence']['comparisons'] if c['id']=='previous_week')
    assert {m['metric'] for m in c['metrics']}=={'MAIN_SLEEP','STEPS'}


def test_body_daily_alignment_keeps_previous_activity_separate_from_wake_day():
    e=body_evidence({'STEPS':[1000,2000,3000,4000,5000,6000,7000],'MAIN_SLEEP':[480]*7})
    c=e['body_state_evidence']['daily_context'][1]
    assert c['day_key']=='DAY.2026-09-29'
    assert c['same_day']['MAIN_SLEEP']=='MAIN_SLEEP.2026-09-29'
    assert c['same_day']['STEPS']=='STEPS.2026-09-29'
    assert c['preceding_day_activity_key']=='STEPS.2026-09-28'
    assert 'preceding_day_activity_key' not in e['body_state_evidence']['daily_context'][0]


def test_collect_exposes_previous_core_series_and_cross_domain_render_guard(container):
    add_steps(container,'u')
    rows=[HealthMetricIn(metric_type='RESTING_HEART_RATE',day=str(START-timedelta(days=7)+timedelta(days=i)),value1=65+i/2) for i in range(14)]
    container.health_service.sync('u',HealthSyncRequest(from_day=str(START-timedelta(days=7)),to_day=str(END),metrics=rows,metric_types=['RESTING_HEART_RATE']))
    e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai')
    assert e['previous_series']['RESTING_HEART_RATE']['values'][0]==65
    assert e['body_state_evidence']['synthesis_ready']
    value=output();value['story']['topic_id']='body_state'
    with pytest.raises(ValueError,match='missing_cross_domain_evidence'):render_output(value,e)
    value['html']+='<p>活动增加时，静息读数也从 {{fact:body.previous_week.RESTING_HEART_RATE.before}} 到 {{fact:body.previous_week.RESTING_HEART_RATE.after}}。</p>'
    assert render_output(value,e)[1]


def test_generation_context_focuses_joint_evidence_without_truncating_saved_snapshot():
    from app.reports.body_state import generation_context
    e=body_evidence({'STEPS':[5000]*7,'MAIN_SLEEP':[480]*7,'RESTING_HEART_RATE':[65]*7})
    e['facts'].update({'STEPS.avg':'5000步','STEPS.days':'7/7天','SPO2.avg':'96%','BEDTIME.2026-09-28':'23:00'})
    e['insight_candidates']=[{'id':'distribution'}];e['owner_notes']=[{'text':'合成背景'}]
    view=generation_context(e)
    assert 'STEPS.avg' not in view['facts'] and 'SPO2.avg' not in view['facts']
    assert 'body.within_week.STEPS.before' in view['facts'] and 'STEPS.days' in view['facts']
    assert 'BEDTIME.2026-09-28' in view['facts'] and view['owner_notes']==e['owner_notes']
    assert 'insight_candidates' not in view and 'STEPS.avg' in e['facts']
    sparse=body_evidence({'STEPS':[5000]*7})
    assert generation_context(sparse) is sparse


@pytest.mark.parametrize('text,error',[
    ('<p>每天增加十五分钟步行。</p>','unreferenced_number'),
    ('<p>睡眠少了 {{fact:MAIN_SLEEP.delta}}。</p>','signed_delta_direction'),
    ('<p>从 {{fact:STEPS.2026-09-28}} 的 {{fact:STEPS.min}} 开始。</p>','measurement_used_as_date')])
def test_semantic_numeric_errors_are_rejected(container,text,error):
    add_steps(container,'u');e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai')
    e['facts']['MAIN_SLEEP.delta']='-30分钟'
    value=output();value['html']+=text
    with pytest.raises(ValueError,match=error):render_output(value,e)


def test_format_feedback_reports_both_summary_and_body_errors_in_one_attempt(container):
    add_steps(container,'u');e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai')
    value=output();value['summary']='步数一共达到12345步，需要关注。'
    value['html']+='<p>{{fact:STEPS.avg}} 为 {{fact:STEPS.avg}}</p>'
    with pytest.raises(ValueError) as error:render_output(value,e)
    assert 'unreferenced_number:summary' in str(error.value)
    assert 'repeated_fact_statement:html' in str(error.value)


def test_report_writer_uses_analysis_call_without_changing_review_client(container):
    add_steps(container,'u');srv=container.weekly_report_service
    class AnalysisModel(Model):
        def __init__(self):super().__init__();self.writes=0
        async def complete_report(self,messages,*,max_tokens):
            self.writes+=1
            assert max_tokens==12288 and '事实审查员' not in messages[0]['content']
            return SimpleNamespace(finish_reason='stop',content=json.dumps(output(),ensure_ascii=False))
    model=AnalysisModel();srv.llm=model
    row=srv.store.queue('u',str(START),str(END),'Asia/Shanghai')
    asyncio.run(srv.generate(row))
    assert model.writes==1 and model.calls==1 and srv.store.get('u',row['id'])['status']=='ready'


def test_previous_comparison_dates_can_be_cited_as_dates(container):
    add_steps(container,'u');e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai')
    assert e['facts']['DAY.2026-09-21']=='2026-09-21'
    value=output();value['html']+='<p>前一个七日区间从 {{fact:DAY.2026-09-21}} 开始。</p>'
    assert '2026-09-21' in render_output(value,e)[1]


def test_format_feedback_includes_unknown_keys_with_other_errors(container):
    add_steps(container,'u');e=collect_facts(container.health_service,'u',START,END,'Asia/Shanghai')
    value=output();value['summary']='该日有123步记录，值得回看。';value['html']+='{{fact:not_available}}'
    with pytest.raises(ValueError) as error:render_output(value,e)
    assert 'unknown_fact:not_available' in str(error.value) and 'unreferenced_number:summary' in str(error.value)
