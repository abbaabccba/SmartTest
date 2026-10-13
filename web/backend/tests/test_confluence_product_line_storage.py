import json
import pytest

from core.confluence.project import Project, ProjectIdentity, ProductSpaceRef, ConfluencePageRef, ProjectDetails, ProjectQuery
from smarttest_web.database import WebDatabase
from smarttest_web.confluence.project_repository import ConfluenceProjectRepository
from smarttest_web.query_snapshot_repository import ConfluenceQuerySnapshotRepository
from smarttest_web.session import PersistentSessionStore
from smarttest_web.audit.email_history import AuditEmailHistory
from conftest import MemoryCredentialStore


def test_product_line_storage_keeps_names_and_restores_existing_contracts(tmp_path):
    database = WebDatabase(tmp_path / 'lines.db')
    repository = ConfluenceProjectRepository(database)
    project = Project(ProjectIdentity('123', 'DOPL:P1'), 'Project',
                      ProductSpaceRef('DOPL', 'DOPL', 'https://confluence/display/DOPL'), ConfluencePageRef('123'))
    repository.save_core([project])
    sessions = PersistentSessionStore(database.path, credential_store=MemoryCredentialStore())
    token = sessions.create('qa', 'test')
    access = sessions.resource_access(token, 'confluence:https://one', database)
    access.publish([('project', '123', 'catalog', 'DOPL'), ('catalog', 'DOPL', 'ready', 'DOPL')], lambda: None)
    snapshots = ConfluenceQuerySnapshotRepository(database, now=lambda: 100)
    snapshots.record('session', {'__product_space__': ['DOPL']}, '', ['123'], 'v1', expires_at=1000)
    sessions.upsert_preferences('qa', 'projects.html', {'field.__product_space__': ['DOPL'], 'other': ['DOPL']})
    history = AuditEmailHistory(database)
    run = history.create('qa', {'confluence': {'excludeSupportModeBProductLines': ['SDPL'],
        'filters': {'product space': ['SDPL', 'TV', 'OOPL']},
        'manualProjectUrls': ['https://confluence/display/DOPL/3.+Yak918-Test+Information']}})
    with database.connect() as c:
        assert c.execute('SELECT product_space_key,product_space_name,product_space_url FROM confluence_projects').fetchone() == (
            'China Operator', 'China Operator', project.product_space.url)
        assert c.execute("SELECT resource_id,scope FROM web_resource_access WHERE kind='catalog'").fetchone() == ('China Operator', 'China Operator')
        assert 'China Operator' in c.execute('SELECT filters_json FROM web_query_snapshots').fetchone()[0]
        assert 'China Operator' in c.execute("SELECT value_json FROM user_preferences WHERE key='field.__product_space__'").fetchone()[0]
        assert 'Smart Device' in c.execute('SELECT payload FROM audit_email_runs WHERE id=?', (run['id'],)).fetchone()[0]
        stored_scope = json.loads(c.execute('SELECT payload FROM audit_email_runs WHERE id=?', (run['id'],)).fetchone()[0])['scope']['confluence']
        assert stored_scope['filters']['product space'] == ['Smart Device', 'TV', 'Global Operator & STB']
        assert stored_scope['manualProjectUrls'] == run['scope']['confluence']['manualProjectUrls']
    assert repository.get('123', ProjectDetails()).product_space == project.product_space
    assert repository.list(ProjectQuery(filters=(('product line', ('DOPL',)),))).total == 1
    assert access.ids('catalog', 'ready') == {'DOPL'}
    assert access.scopes('project', '123', 'catalog') == {'DOPL'}
    assert snapshots.get('session').filters == {'__product_space__': ['DOPL']}
    assert sessions.get_preferences('qa', 'projects.html')['items'] == {'field.__product_space__': ['DOPL'], 'other': ['DOPL']}
    assert history.get('qa', run['id'])['scope'] == run['scope']
    access.publish([], lambda: None, replace_scopes=('DOPL',))
    assert access.ids('project', 'catalog') == set()
    assert access.ids('catalog', 'ready') == set()


def test_startup_migrates_only_product_line_fields_and_preserves_identities(tmp_path):
    from smarttest_web.schema import initialize_web_schema

    database = WebDatabase(tmp_path / 'legacy.db')
    repository = ConfluenceProjectRepository(database)
    project = Project(ProjectIdentity('123', 'DOPL:P1'), 'Project',
                      ProductSpaceRef('DOPL', 'DOPL', 'https://confluence/display/DOPL'), ConfluencePageRef('123'))
    repository.save_core([project])
    history = AuditEmailHistory(database)
    run = history.create('qa', {'confluence': {'excludeSupportModeBProductLines': ['SDPL']}})
    with database.transaction() as c:
        c.execute("DELETE FROM smarttest_schema WHERE component='confluence_product_line_names'")
        c.execute("UPDATE confluence_projects SET product_space_key='DOPL',product_space_name='DOPL'")
        c.execute("INSERT INTO web_resource_access VALUES('qa','confluence:https://one','catalog','DOPL','ready','DOPL',100)")
        c.execute("INSERT INTO web_resource_access VALUES('qa','jira:https://one','project','DOPL:P1','catalog','DOPL',100)")
        c.execute("INSERT INTO user_preferences VALUES('qa','projects.html','field.__product_space__','[\"DOPL\"]',1,100)")
        c.execute("INSERT INTO web_query_snapshots(session_hash,scope,filters_json,search,project_ids_json,facts_version,created_at,updated_at,expires_at) VALUES('s','confluence-project-facts','{\"__product_space__\":[\"DOPL\"]}','','[\"DOPL:P1\"]','v',100,100,1000)")
        c.execute('UPDATE audit_email_runs SET payload=? WHERE id=?', (json.dumps(run), run['id']))
    initialize_web_schema(database)
    with database.connect() as c:
        assert c.execute('SELECT product_space_key,product_space_name,project_id,product_space_url FROM confluence_projects').fetchone() == (
            'China Operator', 'China Operator', 'DOPL:P1', project.product_space.url)
        assert c.execute("SELECT resource_id,scope FROM web_resource_access WHERE platform LIKE 'confluence:%'").fetchone() == ('China Operator', 'China Operator')
        assert c.execute("SELECT resource_id,scope FROM web_resource_access WHERE platform LIKE 'jira:%'").fetchone() == ('DOPL:P1', 'DOPL')
        assert json.loads(c.execute('SELECT filters_json FROM web_query_snapshots').fetchone()[0]) == {'__product_space__': ['China Operator']}
        assert json.loads(c.execute('SELECT value_json FROM user_preferences').fetchone()[0]) == ['China Operator']
        assert json.loads(c.execute('SELECT payload FROM audit_email_runs WHERE id=?', (run['id'],)).fetchone()[0])['scope']['confluence']['excludeSupportModeBProductLines'] == ['Smart Device']
    assert history.get('qa', run['id'])['scope'] == run['scope']
    assert repository.get('123', ProjectDetails()).product_space == project.product_space
    initialize_web_schema(database)


@pytest.mark.parametrize('damage', ['history_conflict', 'jira_conflict'])
def test_product_line_repair_rolls_back_all_tables_and_marker_on_invalid_history(tmp_path, damage):
    from smarttest_web.schema import initialize_web_schema

    database = WebDatabase(tmp_path / 'atomic.db')
    history = AuditEmailHistory(database)
    run = history.create('qa', {})
    if damage == 'history_conflict':
        run['summary']['confluence'] = {'China Operator Business': [1, 2], 'China Operator': [3, 4]}
        run['reports']['confluence'] = {'state': 'completed', 'sourceIds': [run['id']], 'html': 'invalid'}
    repository = ConfluenceProjectRepository(database)
    repository.save_core([Project(ProjectIdentity('123', 'DOPL:P1'), 'Project', ProductSpaceRef('DOPL'), ConfluencePageRef('123'))])
    with database.transaction() as c:
        c.execute("UPDATE smarttest_schema SET version=1 WHERE component='confluence_product_line_names'")
        c.execute("UPDATE confluence_projects SET product_space_key='DOPL',product_space_name='DOPL'")
        c.execute('UPDATE audit_email_runs SET payload=? WHERE id=?', (json.dumps(run), run['id']))
        if damage == 'jira_conflict':
            statistics = {'productLines': [{'id': 'China Operator', 'label': 'China Operator'},
                                          {'id': 'China Operator Business', 'label': 'China Operator Business'}]}
            c.execute("INSERT INTO jira_analytics_queries(session_hash,account,expires_at) VALUES('s','qa',1000)")
            c.execute("INSERT INTO jira_analytics_snapshots(snapshot_id,session_hash,account,jql,basic_json,state,created_at,statistics_json) VALUES('s','s','qa','','{}','active',100,?)", (json.dumps(statistics),))
    def state():
        with database.connect() as c:
            return {table: c.execute('SELECT * FROM ' + table).fetchall() for table in
                    ('confluence_projects', 'web_resource_access', 'web_query_snapshots', 'user_preferences', 'audit_email_runs', 'jira_analytics_snapshots', 'smarttest_schema')}
    before = state()
    with pytest.raises(ValueError):
        initialize_web_schema(database)
    assert state() == before


def test_history_rejects_bad_saved_summary_and_bad_comparison_source(tmp_path):
    from core.product_lines import PRODUCT_LINES
    database = WebDatabase(tmp_path / 'invalid.db')
    history = AuditEmailHistory(database)
    run = history.create('qa', {})
    run['summary']['confluence'] = {'China Operator': [1, 2]}
    with pytest.raises(ValueError):
        history.save('qa', run)
    with database.transaction() as c:
        c.execute('UPDATE audit_email_runs SET payload=? WHERE id=?', (json.dumps(run), run['id']))
    with pytest.raises(ValueError):
        history.get('qa', run['id'])
    current = history.create('qa', {})
    current['summary']['confluence'] = {line.name: [0, 0] for line in PRODUCT_LINES}
    current['reports']['confluence'] = {'state': 'historical_preview', 'sourceIds': [current['id'], run['id']], 'html': 'stale invalid comparison'}
    history.save('qa', current)
    with pytest.raises(ValueError):
        history.get('qa', current['id'])


def test_jira_snapshot_names_migrate_without_changing_people_or_counts(tmp_path):
    from smarttest_web.schema import initialize_web_schema
    database = WebDatabase(tmp_path / 'jira.db')
    line = {'id': 'China Operator Business', 'label': 'China Operator Business',
            'people': [{'id': 'qa', 'bugCount': 3, 'resolvedCount': 2}]}
    original = {'current': {'productLines': [dict(line)]}, 'previous': {'productLines': [dict(line)]},
                'productLines': [dict(line)], 'teamTotal': 7, 'unassignedCount': 1}
    with database.transaction() as c:
        c.execute("UPDATE smarttest_schema SET version=1 WHERE component='confluence_product_line_names'")
        c.execute("INSERT INTO jira_analytics_queries(session_hash,account,expires_at) VALUES('s','qa',1000)")
        c.execute("INSERT INTO jira_analytics_snapshots(snapshot_id,session_hash,account,jql,basic_json,state,created_at,statistics_json) VALUES('s','s','qa','','{}','active',100,?)", (json.dumps(original),))
    initialize_web_schema(database)
    with database.connect() as c:
        migrated = json.loads(c.execute("SELECT statistics_json FROM jira_analytics_snapshots WHERE snapshot_id='s'").fetchone()[0])
    for group in (migrated, migrated['current'], migrated['previous']):
        actual = group['productLines'][0]
        assert actual == {**line, 'id': 'China Operator', 'label': 'China Operator'}
    assert migrated['teamTotal'] == 7 and migrated['unassignedCount'] == 1
