from __future__ import annotations

from conftest import confirmed_access
from dataclasses import replace
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from core.confluence.models import ConfluencePage
from core.confluence.project_mapper import ConfluenceProjectMapper
from core.confluence.project import (
    ConfluencePageRef,
    ProductSpaceRef,
    Project,
    ProjectDetails,
    ProjectIdentity,
    ProjectRole,
    SourceEvidence,
)
from core.domain.detail import DetailSection
from core.domain.values import FieldBag, NamedValue, PersonRef
from core.jira.gateway import JiraGatewayError
from core.jira.mapper import JiraIssueMapper
from core.jira.domain import IssueDetails
from core.weekly_audit import fixed_weekly_audit_scope
from smarttest_web.audit.confluence_adapter import WebConfluenceAuditOwner
from smarttest_web.audit.jira_adapter import WebJiraAuditOwner
from smarttest_web.audit.registry import AuditCancelled
from smarttest_web.confluence.cache_service import ConfluenceProjectCacheService
from smarttest_web.confluence.project_repository import ConfluenceProjectRepository
from smarttest_web.project_facts_api import _ProjectFactsGateway
from smarttest_web.jira.cache_service import JiraIssueCacheService
from smarttest_web.jira.issue_repository import JiraIssueRepository


def test_confluence_child_metadata_grants_reuse_parent_scope_and_version_reader():
    calls = []
    class Access:
        def __init__(self): self.grants = {('page', 'parent', 'metadata', 'P1:evidence')}
        def require_active(self): pass
        def require(self, kind, key, capability):
            assert any(row[:3] == (kind, key, capability) for row in self.grants)
        def scopes(self, kind, key, capability):
            return {row[3] for row in self.grants if row[:3] == (kind, key, capability)}
        def publish(self, grants, write):
            self.grants.update(grants)
            write()
    class Gateway:
        def get_page_children(self, page_id):
            calls.append(('children', page_id))
            return [ConfluencePage('child', 'Child', 'https://c/child', version=2)]
        def get_page(self, page_id):
            calls.append(('current', page_id))
            return ConfluencePage('child', 'Child', 'https://c/child', body='<p>new</p>', version=2,
                                  updated_at=datetime(2026, 8, 20, tzinfo=ZoneInfo('Asia/Shanghai')))
        def get_page_version(self, page_id, version):
            calls.append(('version', page_id, version))
            return ConfluencePage('child', 'Child', 'https://c/child', body='<p>old</p>', version=1,
                                  updated_at=datetime(2026, 8, 10, tzinfo=ZoneInfo('Asia/Shanghai')))
    from core.confluence.audit.models import AuditPeriod
    access = Access()
    owner = WebConfluenceAuditOwner(None, None, Gateway(), access=access)
    token = type('Token', (), {'raise_if_cancelled': lambda self: None})()
    children = owner.load_child_pages('parent', token)
    assert children == (ConfluencePageRef('child', 'Child', 'https://c/child', 2),)
    assert ('page', 'child', 'metadata', 'P1:evidence') in access.grants
    current = owner.load_current_page('child', token)
    period = AuditPeriod(datetime(2026, 8, 17, tzinfo=ZoneInfo('Asia/Shanghai')),
                         datetime(2026, 8, 24, tzinfo=ZoneInfo('Asia/Shanghai')))
    assert [page.version for page in owner.load_page_versions('child', period, current, token)] == [1, 2]
    assert ('page', 'child', 'body', 'P1:evidence') in access.grants
    assert calls == [('children', 'parent'), ('current', 'child'), ('version', 'child', 1)]


def test_fixed_confluence_scope_excludes_stage_four_and_out_of_year(
    tmp_path, *, database_factory
):
    base = Project(ProjectIdentity('1', 'P1'), 'Project', ProductSpaceRef('TV'), ConfluencePageRef('1'))
    def candidate(identity, stage, year):
        return replace(
            base,
            identity=ProjectIdentity(identity, identity),
            stage=NamedValue(stage, stage),
            facts=DetailSection.loaded(FieldBag.from_mapping({'date of commercial approval': f'{year}-06-01'})),
        )
    projects = (candidate('P1', '3 EVT', 2026), candidate('P2', '4 MP', 2026), candidate('P3', '5 CLOSED', 2026),
                candidate('P4', '2 DEV', 2024))
    class Repository:
        def list(self, query, page, size, *, visible_ids=None):
            return type('Page', (), {'projects': projects})()
        def load_many(self, rows, details):
            return rows
    owner = WebConfluenceAuditOwner(
        None,
        Repository(),
        None,
        access=confirmed_access(
            database_factory(),
            tuple((project.identity.project_id for project in projects)),
            (),
        ),
    )
    resolved = owner.resolve({
        'startDate': '2026-09-04T00:00:00+08:00', 'endDate': '2026-09-08T10:00:00+08:00',
        'filters': {'date of commercial approval': ['2025', '2026'], 'support mode': ['A', 'B'],
                    'project status': ['NORMAL']},
        'excludeCurrentStageAtOrAbove': 4,
    })
    assert [project.identity.project_id for project in resolved.projects] == ['P1']


def test_fixed_confluence_scope_excludes_support_mode_b_only_from_sdpl(
    tmp_path, *, database_factory
):
    base = Project(ProjectIdentity('1', 'P1'), 'Project', ProductSpaceRef('TV'), ConfluencePageRef('1'),
                   status=NamedValue('NORMAL', 'NORMAL'), stage=NamedValue('3', '3 EVT'),
                   facts=DetailSection.loaded(FieldBag.from_mapping({'date of commercial approval': '2026-06-01'})))
    projects = (
        replace(base, identity=ProjectIdentity('tv-b', 'TV-B'), support_mode=NamedValue('B', 'B')),
        replace(base, identity=ProjectIdentity('sdpl-a', 'SDPL-A'), product_space=ProductSpaceRef('SDPL'),
                support_mode=NamedValue('A', 'A')),
        replace(base, identity=ProjectIdentity('sdpl-b', 'SDPL-B'), product_space=ProductSpaceRef('SDPL'),
                support_mode=NamedValue('B', 'B')),
    )
    class Repository:
        def list(self, query, page, size, *, visible_ids=None):
            return type('Page', (), {'projects': projects})()
        def load_many(self, rows, details):
            return rows
    owner = WebConfluenceAuditOwner(
        None,
        Repository(),
        None,
        access=confirmed_access(
            database_factory(),
            tuple((project.identity.project_id for project in projects)),
            (),
        ),
    )
    scope = fixed_weekly_audit_scope(
        datetime(2026, 9, 18, 18, tzinfo=ZoneInfo('Asia/Shanghai')),
    )['confluence']
    scope.pop('manualProjectUrls', None)
    resolved = owner.resolve(scope | {
        'startDate': '2026-09-11T18:00:00+08:00',
        'endDate': '2026-09-18T18:00:00+08:00',
    })
    assert [project.identity.project_id for project in resolved.projects] == ['TV-B', 'SDPL-A']


def test_weekly_manual_china_projects_bypass_all_filters_and_load_without_catalog(
    tmp_path, *, database_factory
):
    from core.product_lines import PRODUCT_LINES
    database = database_factory()
    repository = ConfluenceProjectRepository(database)
    access = confirmed_access(database, (), ())
    scope = fixed_weekly_audit_scope(datetime(2026, 10, 9, 18))
    urls = scope['confluence'].get('manualProjectUrls', ())
    assert len(urls) == 3
    pages = {}
    roots = {}
    children = {}
    for index, url in enumerate(urls):
        name = ('Yak918', 'Turtle128', 'Owl422')[index]
        root = ConfluencePage(f'root-{index}', name, f'https://confluence.amlogic.com/pages/viewpage.action?pageId=root-{index}')
        entry = ConfluencePage(f'test-{index}', f'3. {name}-Test Information', url)
        basic = ConfluencePage(f'basic-{index}', f'0. {name}-Basic Information', '', body=(
            f'<table><tr><th>Project ID</th><td>REAL-{index}</td></tr>'
            '<tr><th>Current Stage</th><td>5</td></tr>'
            '<tr><th>Support Mode</th><td>C</td></tr>'
            '<tr><th>Date of Commercial Approval</th><td>2020-01-01</td></tr></table>'))
        pages[url] = entry
        pages[root.id] = root
        pages[basic.id] = basic
        roots[entry.id] = root
        children[root.id] = [basic, entry]
    class Client:
        def get_page_by_url(self, url): return pages[url]
        def get_parent_page(self, page_id): return roots[page_id]
        def get_page(self, page_id): return pages[page_id]
        def get_page_children(self, page_id): return children.get(page_id, [])
    client = Client()
    cache = ConfluenceProjectCacheService(_ProjectFactsGateway(client, repository), ConfluenceProjectMapper(), repository, access=access)
    cached = Project(
        ProjectIdentity('DOPL:REAL-0', 'REAL-0'), 'Cached Yak918', ProductSpaceRef('DOPL'),
        ConfluencePageRef('status-0', 'Yak918 status', 'original-status-url'),
        status=NamedValue('BLOCK', 'BLOCK'), stage=NamedValue('5', '5'),
        facts=DetailSection.loaded(FieldBag.from_mapping({'date of commercial approval': '2020-01-01'})),
    )
    eligible = replace(cached, identity=ProjectIdentity('TV:ELIGIBLE', 'ELIGIBLE'),
                       product_space=ProductSpaceRef('TV'), status=NamedValue('NORMAL', 'NORMAL'),
                       support_mode=NamedValue('A', 'A'), stage=NamedValue('3', '3'),
                       facts=DetailSection.loaded(FieldBag.from_mapping({'date of commercial approval': '2026-01-01'})))
    extra_china = replace(eligible, identity=ProjectIdentity('DOPL:EXTRA', 'EXTRA'), product_space=ProductSpaceRef('DOPL'))
    cache._publish_catalog((cached, eligible, extra_china), ())
    owner = WebConfluenceAuditOwner(cache, repository, client, access=access)
    resolved = owner.resolve(scope | scope['confluence'])
    assert [item.identity.project_id for item in resolved.projects] == ['ELIGIBLE', 'REAL-0', 'REAL-1', 'REAL-2']
    assert all(item.product_space.key == PRODUCT_LINES[0].confluence_space_key for item in resolved.projects[1:])
    assert resolved.projects[1] == cached
    assert all(repository.get(item.identity.confluence_id, ProjectDetails()) is not None for item in resolved.projects)
    assert all(access.allows('project', item.identity.confluence_id, 'catalog') for item in resolved.projects)
    ordinary = owner.resolve({'startDate': scope['startDate'], 'endDate': scope['endDate'],
                              'filters': {'product space': ['DOPL'], 'project status': ['NORMAL']}})
    assert [item.identity.project_id for item in ordinary.projects] == ['EXTRA']
    del pages[urls[2]]
    with pytest.raises(KeyError):
        owner.resolve(scope | scope['confluence'])


DESCRIPTION = """Steps to reproduce: open
Actual results: freeze
Expected results: play
Reproducibility rate: 1/2
Comparison: old works
Notes:
HW info: A
SW info: B
"""


def _jira_payload(key, creator, revision="2026-08-29T00:00:00+00:00"):
    return {
        "id": key, "key": key, "fields": {
            "summary": "[A][B][C][D]: issue", "project": {"key": "SH"},
            "status": {"name": "Open"}, "issuetype": {"name": "Bug"},
            "creator": {"name": creator.casefold(), "displayName": creator},
            "components": [{"id": "1", "name": "Video"}],
            "updated": revision,
        },
    }


def test_jira_adapter_refreshes_every_page_and_only_eligible_audit_details(
    tmp_path, *, database_factory
) -> None:
    class Gateway:
        config = type("Config", (), {"base_url": "https://jira.example", "page_size": 1})()
        def __init__(self): self.search_calls = []; self.detail_calls = []
        def search_issues(self, _query, page):
            self.search_calls.append(page)
            rows = [_jira_payload("SH-1", "Chao Li"), _jira_payload("SH-2", "Outside")]
            return {"issues": rows[page:page + 1], "total": 2, "maxResults": 1}
        def get_issue(self, key): return _jira_payload(key, "Chao Li")
        def load_issue_sections(self, key, sections):
            self.detail_calls.append((key, sections))
            return {"description": DESCRIPTION, 'custom_fields': {'Channel of Reporter': {'value': 'Customer-Feedback'}}}
        def fetch_filter(self, filter_id): return {"jql": f"filter={filter_id}"}
        def search_payload(self, query, **_kwargs): return {"issues": [], "total": 0}

    gateway = Gateway()
    repository = JiraIssueRepository(database_factory())
    service = JiraIssueCacheService(gateway, JiraIssueMapper("https://jira.example"), repository)
    owner = WebJiraAuditOwner(gateway, service)

    report = owner.run(owner.resolve("project=SH"), type("Token", (), {"raise_if_cancelled": lambda self: None})(), lambda *_: None)

    assert gateway.search_calls == [0, 1]
    assert gateway.detail_calls == [("SH-1", ("description",)), ("SH-1", ("custom_fields",))]
    assert [issue.key for issue in report.issues] == ["SH-1"]
    cached = repository.get("SH-1", __import__("core.jira.domain", fromlist=["IssueDetails"]).IssueDetails())
    assert cached.creator.display_name == "Chao Li"
    assert cached.components[0].name == "Video"


def test_jira_adapter_stops_before_the_next_page_after_cancellation(
    tmp_path, *, database_factory
) -> None:
    class Token:
        cancelled = False
        def raise_if_cancelled(self):
            if self.cancelled:
                raise AuditCancelled("cancelled")

    token = Token()
    class Gateway:
        config = type("Config", (), {"base_url": "https://jira.example", "page_size": 1})()
        def __init__(self): self.search_calls = []
        def search_issues(self, _query, page):
            self.search_calls.append(page)
            token.cancelled = True
            return {"issues": [_jira_payload("SH-1", "Chao Li")], "total": 2, "maxResults": 1}

    gateway = Gateway()
    owner = WebJiraAuditOwner(
        gateway,
        JiraIssueCacheService(
            gateway,
            JiraIssueMapper("https://jira.example"),
            JiraIssueRepository(database_factory()),
        ),
    )

    with pytest.raises(AuditCancelled):
        owner.run(type("Scope", (), {"jql": "project=SH"})(), token, lambda *_: None)
    assert gateway.search_calls == [0]


def test_jira_adapter_treats_failed_required_description_as_remote_failure(monkeypatch) -> None:
    import smarttest_web.audit.jira_adapter as adapter_module

    monkeypatch.setattr(adapter_module, "smart_log", lambda *_args, **_kwargs: None)
    issue = JiraIssueMapper("https://jira.example").from_search(
        _jira_payload("SH-1", "Chao Li"),
    )
    failed = replace(
        issue, description=DetailSection.failed("remote_unavailable"),
    )
    class Cache:
        def get_issue(self, _key, _details): return failed
        def refresh_sections(self, _key, _details): return failed

    owner = WebJiraAuditOwner(object(), Cache())
    with pytest.raises(RuntimeError, match="remote_unavailable"):
        owner.load_details(issue, IssueDetails(description=True))


def test_jira_adapter_logs_safe_issue_context_when_detail_loading_raises(monkeypatch) -> None:
    import smarttest_web.audit.jira_adapter as adapter_module

    issue = JiraIssueMapper("https://jira.example").from_search(
        _jira_payload("SH-123", "Chao Li"),
    )
    records = []
    monkeypatch.setattr(
        adapter_module,
        "smart_log",
        lambda message, **kwargs: records.append((message, kwargs)),
        raising=False,
    )

    class ResponseError(RuntimeError):
        response = type("Response", (), {"status_code": 503})()

    failure = JiraGatewayError("jira_issue_get_failed")
    failure.__cause__ = ResponseError("response body must not be logged")

    class Cache:
        def get_issue(self, _key, _details):
            raise failure

    owner = WebJiraAuditOwner(object(), Cache())
    with pytest.raises(JiraGatewayError, match="jira_issue_get_failed"):
        owner.load_details(issue, IssueDetails(description=True))

    assert records == [("Jira audit issue detail failed", {
        "platform": "web",
        "domain": "audit",
        "source": "jira_review",
        "level": "ERROR",
        "emit_runtime_event": False,
        "extra": {
            "stage": "load_details",
            "issue_key": "SH-123",
            "sections": ["description"],
            "error_code": "jira_issue_get_failed",
            "cause_type": "ResponseError",
            "http_status": 503,
        },
    })]


def test_jira_adapter_logs_stale_cache_refresh_decision(monkeypatch) -> None:
    import smarttest_web.audit.jira_adapter as adapter_module

    issue = JiraIssueMapper("https://jira.example").from_search(
        _jira_payload("SH-124", "Chao Li"),
    )
    stale = replace(issue, description=DetailSection.stale(DESCRIPTION))
    loaded = replace(issue, description=DetailSection.loaded(DESCRIPTION))
    records = []
    monkeypatch.setattr(
        adapter_module,
        "smart_log",
        lambda message, **kwargs: records.append((message, kwargs)),
    )

    class Cache:
        def get_issue(self, _key, _details): return stale
        def refresh_sections(self, _key, _details): return loaded
        def refresh_issue(self, _key, _details):
            raise AssertionError("audit must not refetch core fields")

    result = WebJiraAuditOwner(object(), Cache()).load_details(
        issue, IssueDetails(description=True),
    )

    assert result is loaded
    decisions = [kwargs["extra"] for message, kwargs in records
                 if message == "Jira audit issue detail decision"]
    assert decisions == [{
        "stage": "load_details",
        "issue_key": "SH-124",
        "sections": ["description"],
        "section_states": {"description": "stale"},
        "action": "refresh_sections",
        "refreshes_core": False,
    }]


def _project(project_id="P1"):
    evidence = tuple(
        SourceEvidence(source, ConfluencePageRef(page_id, source, f"https://c/{page_id}"))
        for source, page_id in {
            "test_information": "10", "test_plan": "11", "environment": "12",
            "experience": "13", "report_store": "14",
        }.items()
    )
    return Project(
        ProjectIdentity(project_id, project_id), project_id, ProductSpaceRef("DOPL"),
        ConfluencePageRef("1"),
        roles=DetailSection.loaded((ProjectRole(
            NamedValue("fae", "FAE QA"), (PersonRef("a", "a", "Alice"),),
        ),)), evidence=DetailSection.loaded(evidence),
    )


@pytest.mark.parametrize("old_evidence_loaded", [False, True])
def test_confluence_review_discovers_and_persists_audit_pages_even_with_old_loaded_cache(
    tmp_path, old_evidence_loaded, *, database_factory
) -> None:
    repository, client, cache = _discovery_cache(
        tmp_path, database_factory=database_factory
    )
    if old_evidence_loaded:
        repository.replace_roles("P1", DetailSection.loaded(()))
        repository.replace_evidence("P1", DetailSection.loaded((
            SourceEvidence("catalog", ConfluencePageRef("1")),
            SourceEvidence("basic", ConfluencePageRef("15")),
        )))
    else:
        cache.get_project("P1", ProjectDetails(roles=True, evidence=True))
        assert client.current_calls == ["15"]  # Discovery never fetches audit bodies.
        client.current_calls.clear()
        client.entry_calls.clear()

    owner = WebConfluenceAuditOwner(cache, repository, client, access=cache._access)
    resolved = owner.resolve({"projectIds": ["P1"], "startDate": "2026-08-17", "endDate": "2026-08-24"})
    batch = owner.run(resolved, _audit_token(), lambda *_: None)

    assert [finding.status.value for finding in batch.projects[0].findings] == ["updated"] * 8
    assert client.entry_calls == ["https://c/1"]
    assert client.current_calls == ["15", "10", "11", "12", "13", "14"]
    assert client.version_calls == [(page_id, 1) for page_id in ("10", "11", "12", "13", "14")]
    persisted = ConfluenceProjectRepository(repository.database).get("P1", ProjectDetails(evidence=True))
    assert {item.source: item.page.page_id for item in persisted.evidence.value} == {
        "catalog": "1", "basic": "15", "test_information": "10", "test_plan": "11",
        "environment": "12", "experience": "13", "report_store": "14",
    }


def test_confluence_review_does_not_invent_missing_discovered_page(
    tmp_path, *, database_factory
) -> None:
    repository, client, cache = _discovery_cache(
        tmp_path, database_factory=database_factory
    )
    del client.pages["14"]
    owner = WebConfluenceAuditOwner(cache, repository, client, access=cache._access)
    resolved = owner.resolve({"projectIds": ["P1"], "startDate": "2026-08-17", "endDate": "2026-08-24"})

    findings = owner.run(resolved, _audit_token(), lambda *_: None).projects[0].findings

    assert [finding.status.value for finding in findings] == ["updated"] * 7 + ["invalid_format"]
    assert findings[-1].reason == "格式有误：查询不到Test Report Store"
    assert "14" not in client.current_calls


def _audit_token():
    return type("Token", (), {"raise_if_cancelled": lambda self: None})()


def _discovery_cache(tmp_path, *, database_factory):
    class PageClient:
        def __init__(self):
            titles = {
                "1": "P1", "15": "Basic Information", "10": "Test Information",
                "11": "Test Plan", "12": "Test Environment",
                "13": "Experience and Typical Cases", "14": "Test Report Store",
            }
            self.pages = {
                page_id: ConfluencePage(page_id, title, f"https://c/{page_id}")
                for page_id, title in titles.items()
            }
            self.entry_calls, self.current_calls, self.version_calls = [], [], []

        def get_page_by_url(self, url):
            self.entry_calls.append(url)
            return self.pages["1"]

        def get_page_children(self, page_id):
            return tuple(page for key, page in self.pages.items() if key != "1") if page_id == "1" else ()

        def get_page(self, page_id):
            self.current_calls.append(page_id)
            return replace(self.pages[page_id], body=_body(page_id, "new"), version=2,
                           updated_at=datetime(2026, 8, 20, tzinfo=ZoneInfo("Asia/Shanghai")))

        def get_page_version(self, page_id, version):
            self.version_calls.append((page_id, version))
            return replace(self.pages[page_id], body=_body(page_id, "old"), version=version,
                           updated_at=datetime(2026, 8, 10, tzinfo=ZoneInfo("Asia/Shanghai")))

    repository = ConfluenceProjectRepository(database_factory())
    project = Project(ProjectIdentity("1", "P1"), "P1", ProductSpaceRef("DOPL"),
                      ConfluencePageRef("1", "P1", "https://c/1"))
    repository.save_core((project,))
    client = PageClient()
    cache = ConfluenceProjectCacheService(_ProjectFactsGateway(client, repository), ConfluenceProjectMapper(), repository, access=confirmed_access(repository.database, ('P1',)))
    return repository, client, cache


def test_confluence_adapter_uses_applied_ids_and_keeps_versions_temporary(
    tmp_path, *, database_factory
) -> None:
    project = _project()
    unselected = _project("P2")
    cache_calls = []

    class Repository:
        def get(self, project_id, _details): return {"P1": project, "P2": unselected}.get(project_id)
        def list(self, _query, _page, _size):
            return type("Page", (), {"projects": (project, unselected), "total": 2})()

    class Cache:
        def refresh_projects(self, scope): cache_calls.append(("product-lines", scope.product_space_keys)); return {"projects": (project, unselected), "failed": ()}
        def refresh_project(self, project_id, _details, cancellation=None): cache_calls.append(("project", project_id)); return project
        def get_project(self, project_id, _details, cancellation=None): cache_calls.append(("details", project_id)); return project

    class Gateway:
        def __init__(self): self.calls = []
        def get_page(self, page_id):
            self.calls.append(("current", page_id))
            return ConfluencePage(page_id, page_id, f"https://c/{page_id}", _body(page_id, "new"), _body(page_id, "new"), 2, datetime(2026, 8, 20, tzinfo=ZoneInfo("Asia/Shanghai")))
        def get_page_version(self, page_id, version):
            self.calls.append(("version", page_id, version))
            return ConfluencePage(page_id, page_id, f"https://c/{page_id}", _body(page_id, "old"), _body(page_id, "old"), version, datetime(2026, 8, 10, tzinfo=ZoneInfo("Asia/Shanghai")))

    gateway = Gateway()
    owner = WebConfluenceAuditOwner(
        Cache(),
        Repository(),
        gateway,
        access=confirmed_access(
            database_factory(), ("P1", "P2"), ("10", "11", "12", "13", "14")
        ),
    )
    resolved = owner.resolve({"projectIds": ["P1"], "startDate": "2026-08-17", "endDate": "2026-08-24"})
    batch = owner.run(resolved, type("Token", (), {"raise_if_cancelled": lambda self: None})(), lambda *_: None)

    assert [audit.project.identity.project_id for audit in batch.projects] == ["P1"]
    assert cache_calls[0] == ("project", "P1")
    assert ("project", "P2") not in cache_calls
    assert all(finding.status.value == "updated" for finding in batch.projects[0].findings)
    assert len([call for call in gateway.calls if call[0] == "current"]) == 5


def test_confluence_adapter_stops_after_refresh_when_cancelled(
    tmp_path, *, database_factory
) -> None:
    token = type("Token", (), {"cancelled": False})()
    def raise_if_cancelled():
        if token.cancelled:
            raise AuditCancelled("cancelled")
    token.raise_if_cancelled = raise_if_cancelled
    project = _project()

    class Repository:
        def get(self, _project_id, _details): return project
    class Cache:
        def refresh_project(self, _project_id, _details, cancellation=None): token.cancelled = True; return project
        def get_project(self, _project_id, _details, cancellation=None): return project
    class Gateway:
        def get_page(self, _page_id): raise AssertionError("no page request after cancellation")

    owner = WebConfluenceAuditOwner(
        Cache(),
        Repository(),
        Gateway(),
        access=confirmed_access(
            database_factory(), ("P1", "P2"), ("10", "11", "12", "13", "14")
        ),
    )
    resolved = owner.resolve({"projectIds": ["P1"], "startDate": "2026-08-17", "endDate": "2026-08-24"})
    with pytest.raises(AuditCancelled):
        owner.run(resolved, token, lambda *_: None)


def test_confluence_failed_required_details_create_project_failure(
    tmp_path, *, database_factory
) -> None:
    project = _project()
    failed = replace(
        project, roles=DetailSection.failed("remote_unavailable"),
    )
    class Repository:
        def get(self, _project_id, _details): return project
    class Cache:
        def refresh_projects(self, _scope): return {"projects": (project,), "failed": ()}
        def get_project(self, _project_id, _details, cancellation=None): return failed
        def refresh_project(self, _project_id, _details, cancellation=None): return failed
    class Gateway:
        def get_page(self, _page_id): raise AssertionError("no page request after required detail failure")

    owner = WebConfluenceAuditOwner(
        Cache(),
        Repository(),
        Gateway(),
        access=confirmed_access(
            database_factory(), ("P1", "P2"), ("10", "11", "12", "13", "14")
        ),
    )
    resolved = owner.resolve({"projectIds": ["P1"], "startDate": "2026-08-17", "endDate": "2026-08-24"})
    batch = owner.run(resolved, type("Token", (), {"raise_if_cancelled": lambda self: None})(), lambda *_: None)

    assert len(batch.projects[0].findings) == 1
    assert batch.projects[0].findings[0].rule_id == "project.audit"
    assert batch.projects[0].findings[0].reason == "remote_unavailable"


def test_confluence_exact_refresh_failure_does_not_stop_next_project(
    tmp_path, *, database_factory
) -> None:
    failed_project, good_project = _project("P1"), _project("P2")
    projects = {"P1": failed_project, "P2": good_project}
    class Repository:
        def get(self, project_id, _details): return projects.get(project_id)
    class Cache:
        def refresh_project(self, project_id, _details, cancellation=None):
            if project_id == "P1": raise RuntimeError("network details")
            return projects[project_id]
        def get_project(self, project_id, _details, cancellation=None): return projects[project_id]
    class Gateway:
        def get_page(self, page_id):
            return ConfluencePage(page_id, page_id, f"https://c/{page_id}", _body(page_id, "new"), _body(page_id, "new"), 1, datetime(2026, 8, 20, tzinfo=ZoneInfo("Asia/Shanghai")))

    owner = WebConfluenceAuditOwner(
        Cache(),
        Repository(),
        Gateway(),
        access=confirmed_access(
            database_factory(), ("P1", "P2"), ("10", "11", "12", "13", "14")
        ),
    )
    resolved = owner.resolve({"projectIds": ["P1", "P2"], "startDate": "2026-08-17", "endDate": "2026-08-24"})
    batch = owner.run(resolved, type("Token", (), {"raise_if_cancelled": lambda self: None})(), lambda *_: None)

    assert batch.projects[0].findings[0].reason == "remote_unavailable"
    assert len(batch.projects[1].findings) == 8


def test_confluence_adapter_stops_before_version_requests_after_cancellation(
    tmp_path, *, database_factory
) -> None:
    project = _project()
    token = type("Token", (), {"cancelled": False})()
    def raise_if_cancelled():
        if token.cancelled:
            raise AuditCancelled("cancelled")
    token.raise_if_cancelled = raise_if_cancelled

    class Repository:
        def get(self, _project_id, _details): return project
    class Cache:
        def refresh_project(self, _project_id, _details, cancellation=None): return project
        def get_project(self, _project_id, _details, cancellation=None): return project
    class Gateway:
        def __init__(self): self.calls = []
        def get_page(self, page_id):
            self.calls.append(("current", page_id)); token.cancelled = True
            return ConfluencePage(page_id, page_id, "https://c", "<p>x</p>", "<p>x</p>", 2, datetime(2026, 8, 20, tzinfo=ZoneInfo("Asia/Shanghai")))
        def get_page_version(self, page_id, version):
            self.calls.append(("version", page_id, version)); raise AssertionError("no version request")

    gateway = Gateway()
    owner = WebConfluenceAuditOwner(
        Cache(),
        Repository(),
        gateway,
        access=confirmed_access(
            database_factory(), ("P1", "P2"), ("10", "11", "12", "13", "14")
        ),
    )
    resolved = owner.resolve({"projectIds": ["P1"], "startDate": "2026-08-17", "endDate": "2026-08-24"})
    with pytest.raises(AuditCancelled):
        owner.run(resolved, token, lambda *_: None)
    assert gateway.calls == [("current", "10")]


def test_confluence_review_records_project_page_and_total_timings(
    tmp_path, monkeypatch, *, database_factory
) -> None:
    import smarttest_web.audit.confluence_adapter as adapter_module

    records = []
    monkeypatch.setattr(adapter_module, "smart_log", lambda message, **kwargs: records.append((message, kwargs)), raising=False)
    project = _project()
    class Repository:
        def get(self, _project_id, _details): return project
    class Cache:
        def refresh_project(self, _project_id, _details, cancellation=None): return project
    class Gateway:
        def get_page(self, page_id):
            return ConfluencePage(page_id, page_id, "https://c", "<p>x</p>", "<p>x</p>", 1, datetime(2026, 8, 20, tzinfo=ZoneInfo("Asia/Shanghai")))

    owner = WebConfluenceAuditOwner(
        Cache(),
        Repository(),
        Gateway(),
        access=confirmed_access(
            database_factory(), ("P1",), ("10", "11", "12", "13", "14")
        ),
    )
    resolved = owner.resolve({"projectIds": ["P1"], "startDate": "2026-08-17", "endDate": "2026-08-24"})
    owner.run(resolved, type("Token", (), {"raise_if_cancelled": lambda self: None})(), lambda *_: None)

    stages = {kwargs["extra"]["stage"] for _message, kwargs in records}
    assert {"review.total", "review.project.details", "review.page.current", "review.page.versions"} <= stages
    assert all(kwargs["extra"]["duration_ms"] >= 0 for _message, kwargs in records)


def _body(page_id, value):
    if page_id == "11": return f"<table><tr><td>Category</td></tr><tr><td>{value}</td></tr></table>"
    labels = {"10": ("Phase Status", "Summary", "Task Arrangement", "Blocking"), "12": ("测试环境",)}
    return "".join(f"<h2>{label}</h2><p>{value}</p>" for label in labels.get(page_id, ())) or f"<p>{value}</p>"
