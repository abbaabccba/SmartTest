"""Persistent audit email history, separate from disposable query caches."""

import json
from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo

from core.logging import smart_log
from core.email.audit_report import render_history_report, validate_confluence_summary
from core.product_lines import PRODUCT_LINES, product_line_name, confluence_space_key


class AuditEmailHistory:
    def __init__(self, database):
        self.database = database

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

    def list_runs(self, account, offset=0):
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT id,label,state,payload FROM audit_email_runs ORDER BY sequence DESC LIMIT 4 OFFSET ?",
                (offset,),
            ).fetchall()
            total = connection.execute(
                "SELECT COUNT(*) FROM audit_email_runs",
            ).fetchone()[0]
        return {'runs': [dict(id=row[0], label=row[1], state=row[2],
                             source=json.loads(row[3])['source']) for row in rows], 'total': total}

    def browse(self, account, kind, offset=0, start_date=None, end_date=None):
        if kind not in {'jira', 'confluence'}:
            raise ValueError('invalid_audit_kind')
        with self.database.connect() as connection:
            records = connection.execute(
                """SELECT id,label,json_extract(payload,'$.source'),
                   json_extract(payload,'$.createdAt'),json_extract(payload,?),sequence,
                   json_extract(payload,'$.reports.jira.remediation.currentSummary.passed'),
                   json_extract(payload,'$.reports.jira.remediation.currentSummary.rate')
                   FROM audit_email_runs WHERE json_type(payload,?) = 'object' ORDER BY sequence DESC""",
                (f'$.summary.{kind}', f'$.summary.{kind}'),
            ).fetchall()
        rows = []
        candidate_rows = 0
        for run_id, label, source, created_at, stored_summary, sequence, modified_passed, modified_rate in records:
            candidate_rows += 1
            summary = json.loads(stored_summary)
            timestamp = created_at or summary.get('generatedAt')
            audit_date = datetime.fromisoformat(timestamp).astimezone(
                ZoneInfo('Asia/Shanghai')).date() if timestamp else None
            if ((start_date and (audit_date is None or audit_date < start_date))
                    or (end_date and (audit_date is None or audit_date > end_date))):
                continue
            common = {'id': run_id, 'label': label, 'source': source,
                      'date': audit_date.isoformat() if audit_date else None}
            if kind == 'jira':
                rows.append({**common, **{key: summary.get(key) for key in
                             ('total', 'passed', 'failed', 'rate')},
                             'modifiedPassed': modified_passed, 'modifiedRate': modified_rate,
                             '_sequence': sequence})
            else:
                lines = []
                for line in PRODUCT_LINES:
                    passed, total = summary[line.name]
                    projects = summary.get('projects')
                    lines.append({'productLine': line.name,
                                 'projectCount': sum(project['productLine'] == line.name
                                     for project in projects) if projects is not None else None,
                                 'passed': passed, 'failed': total - passed})
                rows.append({**common, 'lines': lines, '_sequence': sequence})
        rows.sort(key=lambda row: (row['date'] or '', row['_sequence']), reverse=True)
        total = len(rows)
        page = rows[offset:offset + 10]
        for row in page:
            del row['_sequence']
        fields = {'database_path': str(self.database.path.resolve()), 'account': account,
                  'kind': kind, 'database_runs': len(records), 'visible_runs': len(records), 'visibility': 'shared_authenticated',
                  'candidate_rows': candidate_rows, 'filtered_rows': total, 'offset': offset,
                  'start_date': start_date.isoformat() if start_date else '',
                  'end_date': end_date.isoformat() if end_date else '', 'returned_rows': len(page)}
        smart_log('Audit email history query ' + ' '.join(f'{key}={value}' for key, value in fields.items()),
                  platform='web', domain='audit', source='audit_email_history',
                  extra=fields, emit_runtime_event=False)
        return {'rows': page, 'total': total, **({'productLines': [line.name for line in PRODUCT_LINES]} if kind == 'confluence' else {})}

    def get(self, account, run_id):
        with self.database.connect() as connection:
            if not connection.in_transaction:
                connection.execute('BEGIN')
            row = connection.execute(
                "SELECT payload FROM audit_email_runs WHERE id=?", (run_id,),
            ).fetchone()
            if row is None:
                raise LookupError(run_id)
            result = json.loads(row[0])
            self._validate(result)
            report = result.get('reports', {}).get('confluence', {})
            if report.get('state') in {'completed', 'historical_preview'}:
                for source_id in report['sourceIds']:
                    source = connection.execute("SELECT payload FROM audit_email_runs WHERE id=?", (source_id,)).fetchone()
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
