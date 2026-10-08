import asyncio
import json
import time
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.reports import router
from app.config import Settings
from app.core.errors import register_exception_handlers
from app.reports.facts import collect_facts, last_week
from app.reports.html import render_output, sanitize, chart
from app.reports.store import ReportStore
from app.reports.tool import HealthReportTool
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
    ('2026-10-05T00:00:00+00:00', 'Asia/Shanghai', '2026-09-28'),
    ('2026-10-04T17:00:00+00:00', 'Asia/Shanghai', '2026-09-28'),
    ('2026-10-05T00:00:00+00:00', 'America/Los_Angeles', '2026-09-21'),
    ('2026-01-01T00:00:00+00:00', 'Asia/Shanghai', '2025-12-22'),
])
def test_natural_week(stamp, zone, start):
    assert last_week(datetime.fromisoformat(stamp), zone)[0].isoformat() == start


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
    assert row['status'] == 'queued' and row['html'] == '' and row['notified_at'] is None


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
