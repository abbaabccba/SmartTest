from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select
from core.async_tasks import CancellationToken, TaskCancelled
from core.jira.services.jql_statistics_cards import QARoster, build_annual_facts
from smarttest_web import schema
from smarttest_web.jira.analytics_refresh import refresh_metric
from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository


def test_qa_evidence_schema_matches_explicit_alembic_head():
    table = schema.application_metadata().tables.get('jira_analytics_qa_events')
    assert table is not None
    keys = [tuple(column.name for column in constraint.columns) for constraint in table.constraints]
    assert ('snapshot_id', 'issue_id', 'event_kind', 'source_id') in keys
    assert next(iter(table.c.snapshot_id.foreign_keys)).ondelete == 'CASCADE'
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    assert ScriptDirectory.from_config(config).get_current_head() == schema.CURRENT_SCHEMA_REVISION


@pytest.mark.parametrize('empty', [False, True])
@pytest.mark.parametrize('metric', ['comment', 'verify'])
def test_metric_refresh_writes_evidence_with_unchanged_page_facts_then_publishes_complete_metadata(empty, metric):
    roster = QARoster('fingerprint', (('qa', ()),))
    row = {'id': '1', 'fields': {'created': '2026-01-01', 'comment': {'comments': [
        {'id': 'c1', 'author': {'name': 'qa'}, 'body': 'Raw QA comment', 'created': '2020-01-01T00:00:00Z'}]}, 'attachment': []}}
    row['changelog'] = {'histories': [{'id': 'h1', 'author': {'name': 'qa'}, 'created': '2020-01-01T00:00:00Z',
                                      'items': [{'field': 'status', 'fromString': 'Resolved', 'toString': 'Verified'}]}]}
    writes, published = [], []
    class Repo:
        def begin(self, *_args, **_kwargs): return 'snapshot'
        def update_statistics(self, snapshot, metadata, **kwargs):
            writes.append((snapshot, deepcopy(metadata), kwargs)); return True
        def activate(self, snapshot, metadata): published.append((snapshot, deepcopy(metadata))); return True
        def finish(self, *_args): pytest.fail('Successful refresh cannot finish as failed')
    class Pages:
        def run(self, _gateway, _query, _metric, _token, consume, _report): consume([] if empty else [row])
    gateway = Mock()
    refresh_metric(Repo(), Pages(), gateway, 'account', 'task', 'query', roster, metric, CancellationToken(), lambda *_: None)
    snapshot, metadata, values = writes[0]
    assert values['facts'] == build_annual_facts(gateway, [], [], current_activities=[] if empty else [row], previous_activities=[], activity_roster=roster)['_facts']
    assert [event['source_id'] for event in values['qa_events']] == ([] if empty else ['c1' if metric == 'comment' else 'h1'])
    assert metadata['qaEvidence']['complete'] is False
    assert published[0][1]['qaEvidence'] == {'version': 1, 'roster': [('qa', ())], 'kinds': ['comment', 'attachment'] if metric == 'comment' else ['verify'], 'complete': True}


def test_replaced_pending_page_does_not_activate_qa_evidence():
    repo = Mock()
    repo.begin.return_value = 'replaced'
    repo.update_statistics.return_value = False
    class Pages:
        def run(self, _gateway, _query, _metric, _token, consume, _report): consume([])
    with pytest.raises(TaskCancelled):
        refresh_metric(repo, Pages(), Mock(), 'account', 'task', 'query', QARoster('fp', ()), 'verify', CancellationToken(), lambda *_: None)
    repo.activate.assert_not_called()
    assert repo.finish.call_args.args[1] == 'cancelled'


def test_mysql_qa_evidence_is_atomic_idempotent_and_failure_preserves_active_snapshot(application_database):
    events = schema.application_metadata().tables['jira_analytics_qa_events']
    facts = schema.application_metadata().tables['jira_analytics_facts']
    repo = JiraAnalyticsRepository(application_database)
    old = repo.begin('session', 'account', 'query', {}, '', expires_at=0, card_key='task')
    assert repo.activate(old, {'old': True})
    pending = repo.begin('session', 'account', 'query', {}, '', expires_at=0, card_key='task')
    event = {'issue_id': '1', 'event_kind': 'comment', 'source_id': 'c1', 'author_identity': 'qa', 'author_account': 'qa', 'author_display_name': 'QA', 'created_at': '2020-01-01T00:00:00Z', 'updated_at': None, 'payload': {'body': 'raw'}}
    for _ in range(2):
        assert repo.update_statistics(pending, {'evidence': 'pending'}, facts=[{'issueId': '1'}], qa_events=[event])
    with application_database.connect() as connection:
        assert len(connection.execute(select(events).where(events.c.snapshot_id == pending)).all()) == 1
    with pytest.raises(TypeError):
        repo.update_statistics(pending, {'should': 'rollback'}, facts=[{'issueId': 'failed-page'}], qa_events=[{**event, 'payload': object()}])
    with application_database.connect() as connection:
        assert connection.execute(select(facts.c.issue_id).where(facts.c.snapshot_id == pending, facts.c.issue_id == 'failed-page')).first() is None
    repo.finish(pending, 'cancelled')
    assert repo.state('session', 'account', card_key='task')['activeSnapshotId'] == old


def test_qa_evidence_migration_compiles_mysql_ddl_without_connecting():
    from io import StringIO
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    migration = ScriptDirectory.from_config(config).get_revision(schema.CURRENT_SCHEMA_REVISION).module
    output = StringIO()
    context = MigrationContext.configure(dialect_name='mysql', opts={'as_sql': True, 'output_buffer': output})
    with Operations.context(context):
        migration.upgrade()
        migration.downgrade()
    ddl = output.getvalue()
    assert 'UNIQUE (snapshot_id, issue_id, event_kind, source_id)' in ddl
    assert 'FOREIGN KEY(snapshot_id) REFERENCES jira_analytics_snapshots (snapshot_id) ON DELETE CASCADE' in ddl
    assert 'payload_json LONGTEXT NOT NULL' in ddl
    assert 'DROP TABLE jira_analytics_qa_events' in ddl
