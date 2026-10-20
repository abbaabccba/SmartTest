"""Backup coverage and lossless import contracts; only synthetic source data."""
from contextlib import closing
from hashlib import sha256
import sqlite3

import pytest
from sqlalchemy import Column, ForeignKeyConstraint, MetaData, Table, UniqueConstraint, create_engine, select

from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository
from smarttest_web.schema import application_metadata
from web.scripts.migrate_storage import rehearse


def backup(tmp_path):
    # The one-time importer still accepts the retired SQLite backup format.
    # Build that synthetic format independently from the production DB entry.
    path = tmp_path / 'offline.db'
    metadata = MetaData()
    primary = {'jira_analytics_snapshots': {'snapshot_id'}, 'jira_analytics_facts': {'snapshot_id', 'issue_id'}}
    for target in application_metadata().sorted_tables:
        keys = primary.get(target.name, {column.name for column in target.primary_key})
        columns = [Column(column.name, column.type,
            primary_key=column.name in keys, nullable=column.nullable,
            server_default=column.server_default.arg if column.server_default is not None else None)
            for column in target.c if not (target.name in primary and column.name == 'sequence')]
        unique = [UniqueConstraint(*(column.name for column in constraint.columns))
            for constraint in target.constraints if isinstance(constraint, UniqueConstraint)]
        foreign = [ForeignKeyConstraint([element.parent.name for element in constraint.elements],
            [element.target_fullname for element in constraint.elements], ondelete=constraint.ondelete)
            for constraint in target.foreign_key_constraints]
        Table(target.name, metadata, *columns, *unique, *foreign)
    engine = create_engine('sqlite:///' + path.as_posix())
    metadata.create_all(engine)
    scope = 'account:alice:card:task'
    with engine.begin() as connection:
        connection.execute(metadata.tables['jira_analytics_queries'].insert().values(
            session_hash=scope, account='alice', card_key='task', active_snapshot_id='first',
            pending_snapshot_id='second', last_snapshot_id='second', expires_at=100))
        for identity, state in (('first', 'active'), ('second', 'pending')):
            connection.execute(metadata.tables['jira_analytics_snapshots'].insert().values(
                snapshot_id=identity, session_hash=scope, account='alice', card_key='task',
                jql=identity, basic_json='{}', state=state, created_at=1,
                statistics_json='{"saved":true}'))
        connection.execute(metadata.tables['jira_analytics_facts'].insert(), [
            {'snapshot_id': 'second', 'issue_id': identity, 'fact_json': '{"issueId":"' + identity + '"}'}
            for identity in ('z', 'a')])
        connection.execute(metadata.tables['user_preferences'].insert(), [dict(username=name, scope=scope, key=key,
            value_json='{}', updated_at=1) for name, scope, key in (
                ('a', 's', 'k'), ('A', 's', 'k'), ('a ', 's', 'k'),
                ('a', 'S', 'k'), ('a', 's ', 'k'), ('a', 's', 'K'), ('a', 's', 'k '))])
    engine.dispose()
    return path


def test_backup_rejects_uncovered_tables_and_overlength_without_modifying_source(tmp_path):
    path = backup(tmp_path)
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('CREATE TABLE uncovered(value TEXT)')
    digest = sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='source_table_coverage'):
        rehearse(path, digest)
    assert sha256(path.read_bytes()).hexdigest() == digest
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute('DROP TABLE uncovered')
        connection.execute("UPDATE jira_analytics_queries SET session_hash=?", ('x' * 385,))
        connection.execute("UPDATE jira_analytics_snapshots SET session_hash=?", ('x' * 385,))
    digest = sha256(path.read_bytes()).hexdigest()
    report = rehearse(path, digest)
    assert report['constraint_errors'] == [{'table': 'jira_analytics_queries', 'column': 'session_hash', 'rows': 1},
        {'table': 'jira_analytics_snapshots', 'column': 'session_hash', 'rows': 2}]
    assert report['import_committed'] is False
    assert sha256(path.read_bytes()).hexdigest() == digest


def test_real_mysql_backup_import_preserves_content_pointers_and_rowid_order(tmp_path, application_database):
    path = backup(tmp_path)
    digest = sha256(path.read_bytes()).hexdigest()
    report = rehearse(path, digest, database=application_database, batch_size=1)
    assert report['import_committed'] and report['constraint_errors'] == []
    assert len(report['tables']) == 39
    assert all(table['verified'] for table in report['tables'].values())
    target_state = JiraAnalyticsRepository(application_database).state('s', 'alice', card_key='task')
    assert target_state['activeSnapshotId'] == 'first'
    assert target_state['pendingSnapshotId'] == 'second'
    assert target_state['activeJql'] == 'first'
    facts = application_metadata().tables['jira_analytics_facts']
    with application_database.connect() as connection:
        assert connection.execute(select(facts.c.issue_id).order_by(facts.c.sequence)).scalars().all() == ['z', 'a']
    assert sha256(path.read_bytes()).hexdigest() == digest
    with pytest.raises(ValueError, match='target_not_empty'):
        rehearse(path, digest, database=application_database)
