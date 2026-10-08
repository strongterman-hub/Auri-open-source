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
