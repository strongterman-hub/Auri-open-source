import html
import json
import re

from app.agent.tools import Tool


class HealthReportTool(Tool):
    name = 'read_health_report'
    description = 'Read this account\'s saved weekly health report and exact evidence when the user asks about a report or its conclusions. Defaults to the latest saved report. Does not generate or charge for a new report.'
    parameters = {'type': 'object', 'properties': {'report_id': {'type': 'string'}}, 'required': [], 'additionalProperties': False}

    def __init__(self, store, user_id):
        self.store, self.user_id = store, user_id

    async def execute(self, report_id=None):
        if not report_id:
            report_id = next((r['id'] for r in self.store.list(self.user_id) if r['status'] == 'ready'), None)
        row = self.store.get(self.user_id, report_id) if report_id else None
        if row is None:
            return json.dumps({'error': 'No saved report for this account'}, ensure_ascii=False)
        text = re.sub(r'<svg.*?</svg>', '', row['html'], flags=re.S)
        text = re.sub(r'<style.*?</style>|<head.*?</head>', '', text, flags=re.S)
        text = html.unescape(re.sub(r'<[^>]+>', ' ', text))
        return json.dumps({'report_id': row['id'], 'from_day': row['week_start'], 'to_day': row['week_end'], 'revision': row['revision'], 'summary': row['summary'], 'report_text': text[:8000], 'evidence': json.loads(row['evidence_json'])}, ensure_ascii=False)


class SendHealthReportTool(Tool):
    name = 'send_health_report'
    description = (
        "Send this account's weekly health report directly in the current chat when the user "
        "asks about their overall health last week, asks to see a weekly report, or asks to send it again. "
        "Default: the last complete Monday-Sunday week in the user's timezone, not the latest arbitrary report. "
        "For a specific past week provide its Monday date. Reuses saved HTML without regenerating; "
        "if absent, queues generation and attaches its loading state. The app displays the rich report inline. "
        "Use read_health_report for questions about individual conclusions. Do not call for general health "
        "advice, a question about the report feature, or one metric alone."
    )
    parameters = {'type': 'object', 'properties': {'week_start': {'type': 'string', 'description': 'Optional Monday YYYY-MM-DD; omit for last week.'}}, 'required': [], 'additionalProperties': False}

    def __init__(self, store, user_id, request):
        self.store, self.user_id, self.request = store, user_id, request
        self.requested_ids = set()

    async def execute(self, week_start=None):
        try:
            row = self.request(week_start)
        except (ValueError, TypeError) as exc:
            return json.dumps({'error': str(exc)}, ensure_ascii=False)
        self.requested_ids.add(row['id'])
        return json.dumps({
            'chat_report_id': row['id'], 'from_day': row['week_start'], 'to_day': row['week_end'],
            'status': row['status'], 'summary': row['summary'], 'cached': bool(row['html']),
            'notice': 'The report is attached inline to this reply. Give a short natural introduction; do not mention buttons, ask the user to open another page, or claim it is ready when still generating.',
        }, ensure_ascii=False)

    def acknowledge(self):
        # Called only after the assistant message is durably saved. Its inline
        # reference will show the finished report, so an automatic duplicate is unnecessary.
        for report_id in self.requested_ids:
            if self.store.get(self.user_id, report_id):
                self.store.delivered(report_id)
