from sqlalchemy import text
import json
import pytest

from core.confluence.project import Project, ProjectIdentity, ProductSpaceRef, ConfluencePageRef, ProjectDetails, ProjectQuery
from smarttest_web.confluence.project_repository import ConfluenceProjectRepository
from smarttest_web.query_snapshot_repository import ConfluenceQuerySnapshotRepository
from smarttest_web.session import PersistentSessionStore
from smarttest_web.audit.email_history import AuditEmailHistory
from conftest import MemoryCredentialStore


def test_product_line_storage_keeps_names_and_restores_existing_contracts(
    tmp_path, *, database_factory
):
    database = database_factory()
    repository = ConfluenceProjectRepository(database)
    project = Project(ProjectIdentity('123', 'DOPL:P1'), 'Project',
                      ProductSpaceRef('DOPL', 'DOPL', 'https://confluence/display/DOPL'), ConfluencePageRef('123'))
    repository.save_core([project])
    sessions = PersistentSessionStore(
        credential_store=MemoryCredentialStore(), database=database_factory()
    )
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
        assert c.execute(
            text(
                "SELECT product_space_key,product_space_name,product_space_url FROM confluence_projects"
            )
        ).fetchone() == ("China Operator", "China Operator", project.product_space.url)
        assert c.execute(
            text(
                "SELECT resource_id,scope FROM web_resource_access WHERE kind='catalog'"
            )
        ).fetchone() == ("China Operator", "China Operator")
        assert (
            "China Operator"
            in c.execute(
                text("SELECT filters_json FROM web_query_snapshots")
            ).fetchone()[0]
        )
        assert (
            "China Operator"
            in c.execute(
                text(
                    "SELECT value_json FROM user_preferences WHERE `key`='field.__product_space__'"
                )
            ).fetchone()[0]
        )
        assert (
            "Smart Device"
            in c.execute(
                text("SELECT payload FROM audit_email_runs WHERE id=:id"),
                {"id": run["id"]},
            ).fetchone()[0]
        )
        stored_scope = json.loads(
            c.execute(
                text("SELECT payload FROM audit_email_runs WHERE id=:id"),
                {"id": run["id"]},
            ).fetchone()[0]
        )["scope"]["confluence"]
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


def test_history_rejects_bad_saved_summary_and_bad_comparison_source(
    tmp_path, *, database_factory
):
    from core.product_lines import PRODUCT_LINES
    database = database_factory()
    history = AuditEmailHistory(database)
    run = history.create('qa', {})
    run['summary']['confluence'] = {'China Operator': [1, 2]}
    with pytest.raises(ValueError):
        history.save('qa', run)
    with database.transaction() as c:
        c.execute(
            text("UPDATE audit_email_runs SET payload=:payload WHERE id=:id"),
            {"payload": json.dumps(run), "id": run["id"]},
        )
    with pytest.raises(ValueError):
        history.get('qa', run['id'])
    current = history.create('qa', {})
    current['summary']['confluence'] = {line.name: [0, 0] for line in PRODUCT_LINES}
    current['reports']['confluence'] = {'state': 'historical_preview', 'sourceIds': [current['id'], run['id']], 'html': 'stale invalid comparison'}
    history.save('qa', current)
    with pytest.raises(ValueError):
        history.get('qa', current['id'])
