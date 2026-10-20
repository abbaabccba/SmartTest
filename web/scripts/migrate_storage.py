"""Explicit, read-only backup rehearsal; never discovers or opens the live source."""
from __future__ import annotations

import argparse
import base64
from contextlib import closing
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
for value in (str(ROOT), str(ROOT / 'web' / 'backend')):
    if value not in sys.path:
        sys.path.insert(0, value)

from sqlalchemy import Integer, Float, BigInteger, select, func, text, and_, or_, create_engine, URL
from smarttest_web.config import ApplicationDatabaseSettings, storage_environment
from smarttest_web.database import WebDatabase
from smarttest_web.schema import application_metadata, verify_schema_revision
from core.config.jsonTool import app_data_dir

ARCHIVED_TABLES = frozenset({
    'audit_email_events', 'audit_email_schedule_meta', 'audit_email_schedules',
    'jira_team_bug_accounts', 'jira_team_bug_lines', 'jira_team_bug_rows', 'jira_team_bug_snapshots',
})
SQLITE_METADATA = frozenset({'smarttest_schema', 'sqlite_sequence'})
SEQUENCE_TABLES = frozenset({'jira_analytics_snapshots', 'jira_analytics_facts'})


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def digest_rows(rows, columns=None):
    digest, count = sha256(), 0
    for row in rows:
        values = []
        for index, value in enumerate(row):
            if isinstance(value, bytes):
                value = {'binary': base64.b64encode(value).decode('ascii')}
            elif value is not None and columns is not None and isinstance(columns[index].type, Float):
                value = float(value)
            values.append(value)
        encoded = json.dumps(values, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
        digest.update(len(encoded).to_bytes(8, 'big'))
        digest.update(encoded)
        count += 1
    return {'rows': count, 'sha256': digest.hexdigest()}


def source_rows(source, table):
    columns = [('rowid AS sequence' if table.name in SEQUENCE_TABLES and column.name == 'sequence'
                else quote(column.name)) for column in table.c]
    order = [('rowid' if table.name in SEQUENCE_TABLES and column.name == 'sequence'
              else quote(column.name)) for column in table.primary_key]
    return source.execute('SELECT ' + ','.join(columns) + ' FROM ' + quote(table.name)
                          + ' ORDER BY ' + ','.join(order))


def inspect_backup(source, metadata):
    schemas = dict(source.execute("SELECT name,sql FROM sqlite_master WHERE type='table'"))
    expected = set(metadata.tables)
    unknown = set(schemas) - expected - ARCHIVED_TABLES - SQLITE_METADATA
    missing = expected - set(schemas)
    if unknown or missing:
        raise ValueError('source_table_coverage unknown=' + ','.join(sorted(unknown))
                         + ' missing=' + ','.join(sorted(missing)))
    report = {'tables': {}, 'archive_tables': {}, 'sqlite_metadata': {}, 'constraint_errors': [], 'max_row_bytes': {}}
    for name in sorted(set(schemas) - expected):
        rows = digest_rows(source.execute('SELECT * FROM ' + quote(name) + ' ORDER BY rowid'))
        rows['schema_sql'] = schemas[name]
        rows['schema_sha256'] = sha256(schemas[name].encode()).hexdigest()
        report['archive_tables' if name in ARCHIVED_TABLES else 'sqlite_metadata'][name] = rows
    violations = sum(1 for _ in source.execute('PRAGMA foreign_key_check'))
    if violations:
        report['constraint_errors'].append({'type': 'foreign_key', 'rows': violations})
    for table in metadata.sorted_tables:
        existing = {row[1] for row in source.execute('PRAGMA table_info(' + quote(table.name) + ')')}
        declared = set(table.c.keys()) - ({'sequence'} if table.name in SEQUENCE_TABLES else set())
        if existing != declared:
            raise ValueError('source_column_coverage:' + table.name)
        for column in table.c:
            if column.name not in existing:
                continue
            conditions = []
            if not column.nullable:
                conditions.append(quote(column.name) + ' IS NULL')
            if getattr(column.type, 'length', None):
                conditions.append('length(' + quote(column.name) + ')>' + str(column.type.length))
            if isinstance(column.type, Integer):
                bits = 64 if isinstance(column.type, BigInteger) else 32
                conditions.append(quote(column.name) + ' < ' + str(-(2 ** (bits-1)))
                    + ' OR ' + quote(column.name) + ' > ' + str(2 ** (bits-1)-1))
            if conditions:
                count = source.execute('SELECT count(*) FROM ' + quote(table.name)
                                       + ' WHERE ' + ' OR '.join(conditions)).fetchone()[0]
                if count:
                    report['constraint_errors'].append({'table': table.name, 'column': column.name, 'rows': count})
        sizes = '+'.join('coalesce(length(CAST(' + quote(name) + ' AS BLOB)),0)' for name in existing)
        report['max_row_bytes'][table.name] = source.execute('SELECT max(' + sizes + ') FROM '
            + quote(table.name)).fetchone()[0] or 0
        report['tables'][table.name] = digest_rows(source_rows(source, table), list(table.c))
    for column in ('active_snapshot_id', 'pending_snapshot_id', 'last_snapshot_id'):
        invalid = source.execute('SELECT count(*) FROM jira_analytics_queries q '
            'LEFT JOIN jira_analytics_snapshots s ON s.snapshot_id=q.' + column
            + " WHERE q." + column + "<>'' AND s.snapshot_id IS NULL").fetchone()[0]
        if invalid:
            report['constraint_errors'].append({'type': 'snapshot_pointer', 'column': column, 'rows': invalid})
    return report


def target_rows(connection, table, batch_size):
    # mysql-connector's SQLAlchemy dialect buffers result sets and does not support
    # server-side cursors. Bound each real query instead of pretending yield_per streams it.
    keys = list(table.primary_key.columns)
    last = None
    while True:
        query = select(table).order_by(*keys).limit(batch_size)
        if last is not None:
            query = query.where(or_(*(and_(*(keys[prior] == last[prior] for prior in range(index)),
                key > last[index]) for index, key in enumerate(keys))))
        rows = connection.execute(query).fetchall()
        if not rows:
            return
        yield from rows
        last = tuple(rows[-1]._mapping[column.name] for column in keys)


def import_and_verify(source, database, report, *, batch_size):
    metadata = application_metadata()
    verify_schema_revision(database)
    with database.transaction() as connection:
        packet = connection.execute(text('SELECT @@max_allowed_packet')).scalar_one()
        report['max_allowed_packet'] = packet
        for table in metadata.sorted_tables:
            if connection.execute(select(func.count()).select_from(table)).scalar_one():
                raise ValueError('target_not_empty:' + table.name)
        for table in metadata.sorted_tables:
            cursor = source_rows(source, table)
            row_budget = 2 * report['max_row_bytes'][table.name] + 64 * len(table.c)
            if row_budget > packet // 4:
                raise ValueError('row_exceeds_safe_packet_budget:' + table.name)
            bounded_batch_size = min(batch_size, max(1, (packet // 4) // max(1, row_budget)))
            while batch := cursor.fetchmany(bounded_batch_size):
                connection.execute(table.insert(), [dict(zip(table.c.keys(), row)) for row in batch])
        verify_target(connection, report, batch_size=batch_size)
    report['import_committed'] = True


def verify_target(connection, report, *, batch_size=1000):
    metadata = application_metadata()
    packet = connection.execute(text('SELECT @@max_allowed_packet')).scalar_one()
    for table in metadata.sorted_tables:
        row_budget = 2 * report['max_row_bytes'][table.name] + 64 * len(table.c)
        bounded = min(batch_size, max(1, (packet // 4) // max(1, row_budget)))
        actual = digest_rows(target_rows(connection, table, bounded), list(table.c))
        expected = {key: report['tables'][table.name][key] for key in ('rows', 'sha256')}
        if actual != expected:
            raise ValueError('content_mismatch:' + table.name)
        report['tables'][table.name]['verified'] = True
    relations = {}
    for table in metadata.sorted_tables:
        for constraint in table.foreign_key_constraints:
            pairs = [(element.parent, element.column) for element in constraint.elements]
            parent = pairs[0][1].table
            missing = connection.execute(select(func.count()).select_from(table.outerjoin(parent,
                and_(*(child == referenced for child, referenced in pairs)))).where(
                and_(*(child.is_not(None) for child, _ in pairs)),
                pairs[0][1].is_(None))).scalar_one()
            name = table.name + '(' + ','.join(child.name for child, _ in pairs) + ')'
            relations[name] = missing
            if missing:
                raise ValueError('dangling_relation:' + name)
    pointers = {}
    for column in ('active_snapshot_id', 'pending_snapshot_id', 'last_snapshot_id'):
        invalid = connection.execute(text(
            'SELECT count(*) FROM jira_analytics_queries q LEFT JOIN jira_analytics_snapshots s '
            'ON s.snapshot_id=q.' + column + ' WHERE q.' + column + "<>'' AND s.snapshot_id IS NULL"
        )).scalar_one()
        pointers[column] = invalid
        if invalid:
            raise ValueError('dangling_snapshot_pointer:' + column)
    report['target_relations'] = relations
    report['target_pointers'] = pointers


def verify_owner_outputs(source_path, database):
    from datetime import date
    from core.jira.domain import IssueDetails
    from core.confluence.project import ProjectDetails
    from smarttest_web.jira.issue_repository import JiraIssueRepository
    from smarttest_web.confluence.project_repository import ConfluenceProjectRepository
    from smarttest_web.release_query import ProjectReleaseQueryService
    engine = create_engine(URL.create('sqlite', database='file:' + source_path.as_posix(),
        query={'mode': 'ro', 'immutable': '1', 'uri': 'true'}), hide_parameters=True)
    results = {}
    try:
        with engine.connect() as connection:
            issues = connection.execute(text('SELECT issue_key FROM jira_issues ORDER BY issue_key LIMIT 3')).scalars().all()
            projects = connection.execute(text('SELECT confluence_id FROM confluence_projects ORDER BY confluence_id LIMIT 3')).scalars().all()
            visible = connection.execute(text('SELECT confluence_id FROM confluence_projects ORDER BY confluence_id')).scalars().all()
        for name, owner, identifiers, details in (
            ('jira_details', JiraIssueRepository, issues, IssueDetails(True, True, True, True, True)),
            ('confluence_details', ConfluenceProjectRepository, projects, ProjectDetails(True, True, True, True, True, True)),
        ):
            source_owner, target_owner = owner(engine), owner(database)
            outputs = []
            for identifier in identifiers:
                original = source_owner.get(identifier, details)
                if original != target_owner.get(identifier, details):
                    raise ValueError('owner_output_mismatch:' + name)
                outputs.append(original)
            results[name] = {'samples': len(outputs), 'sha256': sha256(repr(outputs).encode()).hexdigest()}
        today = date.today()
        original = ProjectReleaseQueryService(engine, today=lambda: today).dashboard(visible_ids=visible)
        if original != ProjectReleaseQueryService(database, today=lambda: today).dashboard(visible_ids=visible):
            raise ValueError('owner_output_mismatch:release_dashboard')
        results['release_dashboard'] = {'releases': len(original['releases']),
            'sha256': sha256(json.dumps(original, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
        return results
    finally:
        engine.dispose()


def rehearse(source_path, expected_sha256, *, database=None, batch_size=100):
    source_path = Path(source_path).resolve(strict=True)
    if source_path == (app_data_dir() / 'web' / 'smarttest-web.db').resolve() or any(
            Path(str(source_path) + suffix).exists() for suffix in ('-wal', '-shm')):
        raise ValueError('source_must_be_an_independent_offline_backup')
    if type(batch_size) is not int or batch_size <= 0:
        raise ValueError('batch_size_must_be_positive')
    digest = sha256()
    with source_path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ValueError('backup_sha256_mismatch')
    metadata = application_metadata()
    with closing(sqlite3.connect(source_path.as_uri() + '?mode=ro&immutable=1', uri=True)) as source:
        report = inspect_backup(source, metadata)
        report.update({'source_backup': str(source_path), 'source_sha256': digest.hexdigest(),
                       'source_bytes': source_path.stat().st_size, 'import_committed': False})
        if report['constraint_errors']:
            return report
        if database is not None:
            if database.dialect != 'mysql' or database.location not in {
                    'smarttest_preparation', 'smarttest_preparation_test', 'smarttest_web'}:
                raise ValueError('target_must_be_an_explicit_isolated_mysql_schema')
            import_and_verify(source, database, report, batch_size=batch_size)
            report['owner_outputs'] = verify_owner_outputs(source_path, database)
        return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-backup', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--target-schema', choices=('smarttest_preparation', 'smarttest_preparation_test', 'smarttest_web'))
    parser.add_argument('--batch-size', type=int, default=100)
    args = parser.parse_args(argv)
    database = None
    try:
        if args.report.resolve() == args.source_backup.resolve():
            raise ValueError('report_must_not_overwrite_source_backup')
        if args.target_schema:
            settings = ApplicationDatabaseSettings.from_environment(storage_environment())
            if settings.database != args.target_schema:
                raise ValueError('configured_database_does_not_match_explicit_target')
            database = WebDatabase.from_settings(settings)
        report = rehearse(args.source_backup, args.expected_sha256, database=database, batch_size=args.batch_size)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return 1 if report['constraint_errors'] else 0
    except Exception as error:
        # Database exceptions may embed private values; never print their bodies.
        print('Storage rehearsal failed: ' + (str(error) if isinstance(error, ValueError) else type(error).__name__))
        return 1
    finally:
        if database is not None:
            database.close()


if __name__ == '__main__':
    raise SystemExit(main())
