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
    return {'summary': '这一周的活动记录已经整理好了，回顾里有每天的变化，也可以一起看看接下来想保持什么。',
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
    assert e['facts']['STEPS.avg'] in document


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


def test_sleep_defers_delivery_and_fixed_id_survives_retry(container):
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
