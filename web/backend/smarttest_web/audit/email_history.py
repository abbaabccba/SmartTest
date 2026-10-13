"""Persistent audit email history, separate from disposable query caches."""

import json
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from core.email.audit_report import render_history_report, validate_confluence_summary
from core.product_lines import PRODUCT_LINES, product_line_name, confluence_space_key


class AuditEmailHistory:
    def __init__(self, database):
        self.database = database
        with database.transaction() as connection:
            summaries = []
            for label, total, passed, rate, confluence in (
                ('0807', 261, 58, '22.22%', None),
                ('0814', 151, 53, '35.10%', None),
                ('0824', 191, 79, '41.36%', dict(zip((line.name for line in PRODUCT_LINES), ([0, 16], [5, 152], [7, 176], [29, 152])))),
                ('0828', 118, 58, '49.15%', dict(zip((line.name for line in PRODUCT_LINES), ([0, 8], [11, 128], [12, 168], [6, 128])))),
            ):
                run_id = f'screenshot-{label}'
                summaries.insert(0, {'id': run_id, 'label': label,
                                     'jira': {'total': total, 'passed': passed, 'failed': total - passed, 'rate': rate},
                                     'confluence': confluence})
                reports = self._render(summaries)
                if confluence is None:
                    reports['confluence'] = render_history_report('confluence', [])
                payload = {'id': run_id, 'label': label, 'state': 'historical_seed',
                           'source': 'screenshot', 'createdAt': None,
                           'reports': reports, 'summary': summaries[0],
                           'evidence': '截图历史；无执行时间、审查明细、附件或发送记录。Jira 年份未确认；Confluence 截图年份为 2026。'}
                connection.execute(
                    'INSERT OR IGNORE INTO audit_email_runs(id,account,label,state,payload) VALUES(?,?,?,?,?)',
                    (run_id, '', label, 'historical_seed', json.dumps(payload, ensure_ascii=False)),
                )

    @staticmethod
    def _migrate_confluence_names(connection):
        runs = {row[0]: json.loads(row[1]) for row in connection.execute(
            'SELECT id,payload FROM audit_email_runs')}
        for run in runs.values():
            summary = run.get('summary', {}).get('confluence')
            if summary is None:
                continue
            for mapping in (summary, summary.get('counts', {})):
                normalized = {}
                for old_name, value in mapping.items():
                    name = product_line_name(old_name)
                    if name in normalized:
                        raise ValueError('conflicting_confluence_product_line')
                    normalized[name] = value
                mapping.clear()
                mapping.update(normalized)
            for project in summary.get('projects', []):
                project['productLine'] = product_line_name(project['productLine'])
            validate_confluence_summary(summary)
        for run in runs.values():
            report = run.get('reports', {}).get('confluence', {})
            if report.get('state') in {'completed', 'historical_preview'}:
                records = []
                for source_id in report['sourceIds']:
                    source = runs[source_id]
                    label = source['label']
                    if source.get('createdAt'):
                        label = datetime.fromisoformat(source['createdAt']).astimezone(
                            ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
                    records.append({'id': source_id, 'label': label,
                                    'summary': source['summary']['confluence']})
                report['html'] = render_history_report(
                    'confluence', records, current=report['state'] == 'completed')['html']
            connection.execute('UPDATE audit_email_runs SET payload=? WHERE id=?',
                               (AuditEmailHistory._stored_payload(run), run['id']))

    def claim_scheduled_occurrence(self, account, occurrence):
        with self.database.transaction() as connection:
            return bool(connection.execute(
                'INSERT OR IGNORE INTO audit_email_occurrences(account,occurrence,claimed_at) VALUES(?,?,?)',
                (account, occurrence, datetime.now(timezone.utc).isoformat()),
            ).rowcount)

    @staticmethod
    def _render(summaries):
        return {kind: render_history_report(kind, [
            {'id': row['id'], 'label': row['label'], 'summary': row[kind]}
            for row in summaries if row[kind] is not None
        ]) for kind in ('jira', 'confluence')}

    def list_runs(self, account, offset=0):
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id,label,state,payload FROM audit_email_runs WHERE account IN ('',?) ORDER BY sequence DESC LIMIT 4 OFFSET ?",
                (account, offset),
            ).fetchall()
            total = connection.execute(
                "SELECT COUNT(*) FROM audit_email_runs WHERE account IN ('',?)", (account,),
            ).fetchone()[0]
        return {'runs': [dict(id=row[0], label=row[1], state=row[2],
                             source=json.loads(row[3])['source']) for row in rows], 'total': total}

    def get(self, account, run_id):
        with self.database.connect() as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            row = connection.execute(
                "SELECT payload FROM audit_email_runs WHERE id=? AND account IN ('',?)", (run_id, account),
            ).fetchone()
            if row is None:
                raise LookupError(run_id)
            result = json.loads(row[0])
            self._validate(result)
            report = result.get('reports', {}).get('confluence', {})
            if report.get('state') in {'completed', 'historical_preview'}:
                for source_id in report['sourceIds']:
                    source = connection.execute("SELECT payload FROM audit_email_runs WHERE id=? AND account IN ('',?)", (source_id, account)).fetchone()
                    if source is None:
                        raise ValueError('missing_confluence_history')
                    source_result = json.loads(source[0])
                    self._validate(source_result)
                    if source_result.get('summary', {}).get('confluence') is None:
                        raise ValueError('missing_confluence_history')
        self._convert_scope(result, confluence_space_key)
        return result

    def create(self, account, scope):
        run_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        result = {'id': run_id, 'label': now, 'createdAt': now, 'source': 'manual',
                  'state': 'queued', 'scope': scope, 'summary': {}, 'evidence': '',
                  'reports': {kind: {'state': 'queued', 'html': '', 'attachments': []}
                              for kind in ('jira', 'confluence')}}
        with self.database.transaction() as connection:
            connection.execute(
                'INSERT INTO audit_email_runs(id,account,label,state,payload) VALUES(?,?,?,?,?)',
                (run_id, account, now, result['state'], self._stored_payload(result)),
            )
        return result

    def save(self, account, result):
        with self.database.transaction() as connection:
            connection.execute('UPDATE audit_email_runs SET state=?,payload=? WHERE id=? AND account=?',
                               (result['state'], self._stored_payload(result), result['id'], account))

    @staticmethod
    def _convert_scope(result, convert):
        scope = result.get('scope', {}).get('confluence', {})
        filters = scope.get('filters', {})
        if 'product space' in filters:
            filters['product space'] = [convert(value) for value in filters['product space']]
        if 'excludeSupportModeBProductLines' in scope:
            scope['excludeSupportModeBProductLines'] = [convert(value) for value in scope['excludeSupportModeBProductLines']]

    @classmethod
    def _stored_payload(cls, result):
        cls._validate(result)
        stored = deepcopy(result)
        cls._convert_scope(stored, product_line_name)
        return json.dumps(stored, ensure_ascii=False)

    @staticmethod
    def _validate(result):
        report = result.get('reports', {}).get('confluence', {})
        state = report.get('state')
        if state in {'completed', 'historical_preview'} and not report.get('sourceIds'):
            raise ValueError('missing_confluence_history')
        summary = result.get('summary', {}).get('confluence')
        if summary is not None:
            validate_confluence_summary(summary)
            if state == 'completed' and ('counts' not in summary or 'scope' not in summary):
                raise ValueError('missing_confluence_audit_details')
        elif state in {'completed', 'historical_preview'}:
            raise ValueError('missing_confluence_summary')

    def previous_jira_audit(self, account, previous_period):
        expected = {key: datetime.fromisoformat(previous_period[key])
                    for key in ('startDate', 'endDate')}
        with self.database.connect() as connection:
            rows = connection.execute(
                'SELECT payload FROM audit_email_runs WHERE account=? ORDER BY sequence DESC',
                (account,),
            ).fetchall()
        for row in rows:
            old = json.loads(row[0])
            old_scope = old.get('scope', {})
            if any(not old_scope.get(key) or datetime.fromisoformat(old_scope[key]) != value
                   for key, value in expected.items()):
                continue
            if old.get('reports', {}).get('jira', {}).get('state') == 'completed':
                # Latest completed adjacent audit; delivery does not establish the baseline.
                return old
        return None

    def complete_report(self, account, result, kind, summary, attachments, remediation=None):
        with self.database.transaction() as connection:
            previous = connection.execute(
                """SELECT payload FROM audit_email_runs WHERE account IN ('',?) AND sequence <
                (SELECT sequence FROM audit_email_runs WHERE id=?) ORDER BY sequence DESC""",
                (account, result['id']),
            ).fetchall()
            label = datetime.fromisoformat(result['createdAt']).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
            records = [{'id': result['id'], 'label': label, 'summary': summary}]
            for row in previous:
                old = json.loads(row[0])
                if (old.get('summary', {}).get(kind) is not None
                        and old['reports'][kind]['state'] in {'completed', 'historical_preview'}):
                    old_label = old['label']
                    if old.get('createdAt'):
                        old_label = datetime.fromisoformat(old['createdAt']).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
                    records.append({'id': old['id'], 'label': old_label, 'summary': old['summary'][kind]})
                if len(records) == 4:
                    break
            rendered = render_history_report(kind, records, current=True, remediation=remediation)
            result['summary'][kind] = summary
            result['reports'][kind] = {**rendered, 'attachments': attachments}
            if kind == 'jira':
                result['reports'][kind]['remediation'] = remediation
            self.save(account, result)
