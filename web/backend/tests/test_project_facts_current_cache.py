from __future__ import annotations

from core.confluence.project import ConfluencePageRef, ProductSpaceRef, Project, ProjectIdentity, ProjectRole
from core.confluence.models import ConfluencePage
from core.domain.detail import DetailSection
from core.domain.values import FieldBag, NamedValue, PersonRef
from fastapi.testclient import TestClient
from smarttest_web.confluence.project_repository import ConfluenceProjectRepository
from smarttest_web.app import create_app
from smarttest_web.project_facts_api import ProjectFactsWebOwner, _ProjectFactsGateway
from test_web_session import FakeAuthenticator
from conftest import confirmed_access, MemoryCredentialStore
from smarttest_web.session import PersistentSessionStore


def test_project_facts_owner_reads_and_invalidates_new_project_repository(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    repository.save_core((Project(
        ProjectIdentity("900", "P100"), "Project One",
        ProductSpaceRef("China Operator", "China Operator"), ConfluencePageRef("10", "Catalog", version=1),
        status=NamedValue("normal", "Normal"), stage=NamedValue("evt", "EVT"),
    ),))
    owner = ProjectFactsWebOwner(repository=repository)
    access = confirmed_access(repository.database, ('P100',))

    result = owner.query(access, filters={"current stage": ("EVT",)})
    empty_filter = owner.query(access, filters={"current stage": ("DVT",)})

    assert result["state"] == "ready"
    assert [project["project_id"] for project in result["projects"]] == ["P100"]
    assert result["projects"][0]["fields"]["current stage"] == "EVT"
    assert all(not key.endswith("Color") for key in result["projects"][0])
    assert empty_filter["state"] == "ready"
    assert empty_filter["projects"] == []
    owner.invalidate_project("P100", access)
    empty = owner.query(access)
    assert empty["state"] == "no_snapshot"
    assert [facet["key"] for facet in empty["facets"]] == [
        "__product_space__", "date of commercial approval", "project id", "project owner",
    ]


def test_project_facts_exposes_core_product_labels_and_only_catalog_ready_filter_candidates(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    repository.save_core((Project(
        ProjectIdentity("900", "P100"), "Project One",
        ProductSpaceRef("China Operator", "China Operator"), ConfluencePageRef("10", "Catalog"),
        status=NamedValue("normal", "Normal"),
    ),))
    access = confirmed_access(repository.database, ("P100",))
    access.publish([
        ("catalog", "DOPL", "ready", "DOPL"),
        ("catalog", "TV", "ready", "TV"),
    ], lambda: None)

    result = ProjectFactsWebOwner(repository=repository).query(
        access, filters={"project status": ("DOES NOT MATCH",)},
    )

    assert result["productSpaces"] == [
        {"value": "DOPL", "label": "China Operator", "projectGrouping": None},
        {"value": "SDPL", "label": "Smart Device", "projectGrouping": None},
        {"value": "TV", "label": "TV", "projectGrouping": "launch_os"},
        {"value": "OOPL", "label": "Global Operator & STB", "projectGrouping": None},
        {"value": "WIRELESS", "label": "Wireless Connection", "projectGrouping": None},
    ]
    product_space = next(facet for facet in result["facets"] if facet["key"] == "__product_space__")
    assert product_space["options"] == [
        {"value": "DOPL", "label": "China Operator", "projectGrouping": None},
        {"value": "TV", "label": "TV", "projectGrouping": "launch_os"},
    ]
    assert [facet["key"] for facet in result["facets"]] == [
        "__product_space__", "date of commercial approval", "project id", "project owner",
    ]


def test_block_and_warning_counts_use_the_final_matched_collection(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    projects = tuple(Project(
        ProjectIdentity(f"90{index}", project_id), project_id,
        ProductSpaceRef("China Operator", "China Operator"), ConfluencePageRef(f"10{index}", "Catalog"),
        status=NamedValue(status.casefold(), status),
    ) for index, (project_id, status) in enumerate((
        ("P100", "BLOCK 09/12 owner note"), ("P200", "WARNING - needs attention"),
        ("P300", "NORMAL"), ("P400", "warning 09/05"), ("P500", "BLOCKED"),
    )))
    repository.save_core(projects)
    access = confirmed_access(repository.database, tuple(project.identity.project_id for project in projects))

    owner = ProjectFactsWebOwner(repository=repository)
    result = owner.query(
        access, filters={"project id": ("P300",)}, search="P300",
    )
    blocked = owner.query(access, search="P100")
    warning = owner.query(access, search="P200")
    lowercase_warning = owner.query(access, search="P400")
    unrelated = owner.query(access, search="P500")

    assert [project["project_id"] for project in result["projects"]] == ["P300"]
    assert (result["blockProjectCount"], result["warningProjectCount"]) == (0, 0)
    assert (blocked["blockProjectCount"], blocked["warningProjectCount"]) == (1, 0)
    assert (warning["blockProjectCount"], warning["warningProjectCount"]) == (0, 1)
    assert (lowercase_warning["blockProjectCount"], lowercase_warning["warningProjectCount"]) == (0, 1)
    assert (unrelated["blockProjectCount"], unrelated["warningProjectCount"]) == (0, 0)


def test_project_facts_query_keeps_its_access_snapshot_during_catalog_replacement(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    repository.save_core((Project(
        ProjectIdentity("900", "P100"), "Project One",
        ProductSpaceRef("China Operator", "China Operator"), ConfluencePageRef("10", "Catalog", version=1),
        roles=DetailSection.loaded((ProjectRole(
            NamedValue("fae", "FAE QA"), (PersonRef("alice", display_name="Alice"),),
        ),)),
    ),))
    repository.replace_roles("P100", DetailSection.loaded((ProjectRole(
        NamedValue("fae", "FAE QA"), (PersonRef("alice", display_name="Alice"),),
    ),)))

    class Access:
        def __init__(self): self.catalog_reads = 0
        def require_active(self): return None
        def ids(self, kind, capability):
            if kind == "project" and capability == "catalog":
                self.catalog_reads += 1
                return {"P100"} if self.catalog_reads == 1 else set()
            if kind == "project" and capability == "roles": return {"P100"}
            return set()
        def require(self, kind, resource_id, capability):
            if str(resource_id) not in self.ids(kind, capability): raise PermissionError("permission_denied")
        def allows(self, kind, resource_id, capability): return str(resource_id) in self.ids(kind, capability)

    result = ProjectFactsWebOwner(repository=repository).query(Access())

    assert [row["project_id"] for row in result["projects"]] == ["P100"]
    assert next(group for group in result["ownerHierarchy"] if group["role"] == "FAE QA")["people"][0]["name"] == "Alice"


def test_project_facts_web_path_pages_current_projects(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    repository.save_core(tuple(
        Project(
            ProjectIdentity(str(index), f"P{index}"), f"Project {index}",
            ProductSpaceRef("China Operator"), ConfluencePageRef("10"),
        )
        for index in (1, 2)
    ))
    owner = ProjectFactsWebOwner(repository=repository)
    confirmed_access(repository.database, ('P1', 'P2'))
    sessions = PersistentSessionStore(
        credential_store=MemoryCredentialStore(), database=database_factory()
    )
    client = TestClient(create_app(
        project_facts_owner=lambda: owner, authenticator=FakeAuthenticator,
        session_store=lambda: sessions,
    ), base_url="https://testserver")
    client.post("/api/auth/login", json={"username": "coco", "password": "secret"})

    response = client.get("/api/confluence/project-facts", params={"page": 1, "pageSize": 1})

    assert [row["project_id"] for row in response.json()["projects"]] == ["P2"]
    assert response.json()["pagination"] == {"page": 1, "pageSize": 1, "total": 2}


def test_page_entry_catalog_refresh_is_limited_to_four_product_spaces(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    owner = ProjectFactsWebOwner(repository=repository)

    class Service:
        def __init__(self):
            self.scopes = []

        def refresh_projects(self, scope):
            self.scopes.append(scope)
            return {"projects": (), "failed": ()}

    service = Service()
    owner._service = lambda _username, _password: service

    owner.refresh(confirmed_access(repository.database), "secret")

    assert len(service.scopes) == 1
    assert service.scopes[0].product_space_keys == (
        "DOPL", "SDPL", "TV", "OOPL",
    )


def test_project_facts_owner_queries_persisted_dynamic_fields_and_owner_clusters(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    project = Project(
        ProjectIdentity("TV:P100", "P100"), "Project One",
        ProductSpaceRef("TV", "TV"),
        ConfluencePageRef("900", "Project One", "https://c/pages/900"),
        roles=DetailSection.loaded((ProjectRole(
            NamedValue(name="FAE QA"), (PersonRef("u1", display_name="Alice"),),
        ),)),
        facts=DetailSection.loaded(FieldBag.from_mapping({
            "project id": "P100", "project owner": "Alice", "odm": "ODM-X", "launch os": "Android 16",
            "unexpected owner": "Alice",
        })),
    )
    repository.save_core((project,))
    repository.replace_roles("P100", project.roles)
    repository.replace_facts("P100", project.facts)

    owner = ProjectFactsWebOwner(repository=repository)
    access = confirmed_access(repository.database, ('P100',))
    result = owner.query(access, filters={"project owner": ("Alice",)}, search="Alice")
    unmatched = owner.query(access, filters={"project owner": ("Bob",)})

    assert [row["project_id"] for row in result["projects"]] == ["P100"]
    assert [facet["key"] for facet in result["facets"]] == [
        "__product_space__", "date of commercial approval", "project id", "project owner",
    ]
    assert unmatched["projects"] == []
    assert result["projects"][0]["fields"]["launch os"] == "Android 16"
    fae = next(group for group in result["ownerHierarchy"] if group["role"] == "FAE QA")
    assert fae["people"][0]["name"] == "Alice"


def test_wireless_module_adds_an_independent_project_resource_without_replacing_original(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    roles = DetailSection.loaded((
        ProjectRole(NamedValue("role.major_fae_qa", "Major FAE QA"), (PersonRef("original", display_name="Original QA"),)),
        ProjectRole(NamedValue("role.wifi_major_fae_qa", "WiFi Major FAE QA"), (PersonRef("wifi", display_name="WiFi Owner"),)),
        ProjectRole(NamedValue("role.wifi_fae_qa", "WiFi FAE QA"), (PersonRef("wifi", display_name="WiFi Owner"),)),
        ProjectRole(NamedValue("role.wifi_qa_reviewer", "WiFi QA Reviewer"), (PersonRef("zijie.chen", account="zijie.chen", display_name="zijie.chen"),)),
    ))
    project = Project(
        ProjectIdentity("TV:P100", "P100"), "Project One", ProductSpaceRef("TV", "TV"),
        ConfluencePageRef("900", "Project One", "https://c/pages/900"), roles=roles,
        facts=DetailSection.loaded(FieldBag.from_mapping({"wifi module": "Module W2 Pro"})),
    )
    repository.save_core((project,))
    repository.replace_roles("P100", roles)
    repository.replace_facts("P100", project.facts)

    result = ProjectFactsWebOwner(repository=repository).query(confirmed_access(repository.database, ("TV:P100",)))

    assert [(row["space_key"], row["project_id"]) for row in result["projects"]] == [("TV", "P100"), ("WIRELESS", "P100")]
    original, wireless = result["projects"]
    assert original["roles"]["Major FAE QA"][0]["name"] == "Original QA"
    assert wireless["roles"]["Major FAE QA"] == wireless["roles"]["FAE QA"] == [{
        "identity": "wifi", "account": "", "name": "WiFi Owner",
    }]
    assert wireless["roles"]["QA Reviewer"][0]["identity"] == "zijie.chen"
    assert wireless["fields"]["wifi module"] == "Module W2 Pro"
    product_space = next(facet for facet in result["facets"] if facet["key"] == "__product_space__")
    assert product_space["options"][-1] == {
        "value": "WIRELESS", "label": "Wireless Connection", "projectGrouping": None,
    }
    assert len(next(role for role in result["ownerHierarchy"] if role["role"] == "Major FAE QA")["people"]) == 2


def test_missing_details_acquire_authoritative_catalog_scope_when_derived_filter_is_empty(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    project = Project(
        ProjectIdentity("TV:P100", "P100"), "Project One", ProductSpaceRef("TV", "TV"),
        ConfluencePageRef("900", "Project One"),
    )
    repository.save_core((project,))
    access = confirmed_access(repository.database, ("TV:P100",))
    synced = []

    class Coordinator:
        def __init__(self, _service): pass
        def sync(self, project_ids, _details, **_kwargs): synced.extend(project_ids)

    owner = ProjectFactsWebOwner(
        repository=repository, sync_coordinator_factory=Coordinator,
        client_factory=lambda *_args: object(),
    )

    initial = owner.query(access, filters={"__product_space__": ("WIRELESS",)})
    owner.sync_details(access, "secret", filters={"__product_space__": ("WIRELESS",)})

    assert initial["projects"] == []
    assert initial["detailState"] == "missing"
    assert synced == ["TV:P100"]


def test_synchronized_empty_result_is_complete_and_does_not_reacquire_details(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    project = Project(
        ProjectIdentity("TV:P100", "P100"), "Project One", ProductSpaceRef("TV", "TV"),
        ConfluencePageRef("900", "Project One"),
        roles=DetailSection.loaded(()), facts=DetailSection.loaded(FieldBag()),
    )
    repository.save_core((project,))
    repository.replace_roles(project.identity.confluence_id, project.roles)
    repository.replace_facts(project.identity.confluence_id, project.facts)
    access = confirmed_access(repository.database, ("TV:P100",))
    coordinator_calls = []

    class Coordinator:
        def __init__(self, _service): pass
        def sync(self, project_ids, _details, **_kwargs): coordinator_calls.append(tuple(project_ids))

    owner = ProjectFactsWebOwner(
        repository=repository, sync_coordinator_factory=Coordinator,
        client_factory=lambda *_args: object(),
    )

    result = owner.query(access, filters={"__product_space__": ("WIRELESS",)})
    owner.sync_details(access, "secret", filters={"__product_space__": ("WIRELESS",)})

    assert result["projects"] == []
    assert result["detailState"] == "ready"
    assert coordinator_calls == []


def test_non_wireless_module_project_is_not_duplicated(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    project = Project(
        ProjectIdentity("TV:P100", "P100"), "Project One", ProductSpaceRef("TV", "TV"),
        ConfluencePageRef("900", "Project One"), facts=DetailSection.loaded(FieldBag.from_mapping({"wifi module": "W3"})),
    )
    repository.save_core((project,))
    repository.replace_facts("P100", project.facts)

    result = ProjectFactsWebOwner(repository=repository).query(confirmed_access(repository.database, ("TV:P100",)))

    assert [(row["space_key"], row["project_id"]) for row in result["projects"]] == [("TV", "P100")]


def test_page_entry_persists_recent_client_catalog_contract_for_all_four_spaces(
    tmp_path, *, database_factory
) -> None:
    class CatalogClient:
        def get_page_by_url(self, url, *, prefer_export=False):
            del prefer_export
            space = url.split("/display/")[1].split("/")[0]
            body = (
                "<table><tr><th>页面</th><th>Project ID</th><th>ODM</th>"
                "<th>Current Stage</th></tr>"
                f'<tr><td><a href="/pages/viewpage.action?pageId={space}01">{space} Project</a></td>'
                f"<td>{space}-100</td><td>ODM-{space}</td><td>EVT</td></tr></table>"
            )
            return ConfluencePage(
                f"catalog-{space}", "Project Space", url,
                view_body=body, version=3,
            )

    repository = ConfluenceProjectRepository(database_factory())
    owner = ProjectFactsWebOwner(
        repository=repository,
        client_factory=lambda _username, _password: CatalogClient(),
    )

    access = confirmed_access(repository.database)
    owner.refresh(access, "secret")
    result = owner.query(access)

    assert result["accessibleProjectCount"] == 4
    assert {row["space_key"] for row in result["projects"]} == {"TV", "SDPL", "DOPL", "OOPL"}
    assert [facet["key"] for facet in result["facets"]] == [
        "__product_space__", "date of commercial approval", "project id", "project owner",
    ]


def test_apply_catalog_roundtrip_keeps_dynamic_fields_for_detail_extraction(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    project = Project(
        ProjectIdentity("TV:P100", "P100"), "Project One", ProductSpaceRef("TV"),
        ConfluencePageRef("900", "Project One", "https://c/pages/900"),
        facts=DetailSection.loaded(FieldBag.from_mapping({"odm": "ODM-X"})),
    )
    repository.save_core((project,))
    repository.replace_facts("P100", project.facts)

    payload = _ProjectFactsGateway(object(), repository).get_project_catalog("P100")

    assert payload["fields"]["odm"] == "ODM-X"


def test_apply_refreshes_the_matching_cross_space_identity_only(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    projects = tuple(
        Project(
            ProjectIdentity(f"{space}:P100", "P100"), f"{space} Project",
            ProductSpaceRef(space), ConfluencePageRef(f"{space}-page"),
            facts=DetailSection.loaded(FieldBag.from_mapping({"support mode": "A"})),
        )
        for space in ("China Operator", "TV")
    )
    repository.save_core(projects)
    for project in projects:
        repository.replace_facts(project.identity.confluence_id, project.facts)
    synced = []

    class Coordinator:
        def __init__(self, _service): pass
        def sync(self, project_ids, _details, **_kwargs): synced.extend(project_ids)

    access = confirmed_access(repository.database, ("China Operator:P100", "TV:P100"))
    owner = ProjectFactsWebOwner(
        repository=repository, sync_coordinator_factory=Coordinator,
        client_factory=lambda *_args: object(),
    )

    owner.sync_details(access, "secret", filters={"__product_space__": ("TV",)})

    assert synced == ["TV:P100"]


def test_catalog_refresh_does_not_requery_all_project_facts(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    owner = ProjectFactsWebOwner(repository=repository)

    class Service:
        def refresh_projects(self, _scope):
            return {"projects": (), "failed": ()}

    owner._service = lambda *_args: Service()
    owner.query = lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("duplicate query"))

    assert owner.refresh(confirmed_access(repository.database), "secret") is None


def test_project_facts_query_uses_one_repository_batch_instead_of_per_project_get(
    tmp_path, monkeypatch, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    repository.save_core(tuple(
        Project(ProjectIdentity(str(index), f"P{index}"), f"Project {index}",
                ProductSpaceRef("China Operator"), ConfluencePageRef(str(index)))
        for index in range(3)
    ))
    access = confirmed_access(repository.database, tuple(f"P{index}" for index in range(3)))
    monkeypatch.setattr(repository, "get", lambda *_args: (_ for _ in ()).throw(AssertionError("N+1 get")))

    result = ProjectFactsWebOwner(repository=repository).query(access)

    assert len(result["projects"]) == 3


def test_apply_refreshes_catalog_before_recomputing_detail_scope(
    tmp_path, *, database_factory
) -> None:
    repository = ConfluenceProjectRepository(database_factory())
    old = Project(ProjectIdentity("DOPL:OLD", "OLD"), "Old", ProductSpaceRef("China Operator"),
                  ConfluencePageRef("1"), support_mode=NamedValue(name="A"))
    new = Project(ProjectIdentity("DOPL:NEW", "NEW"), "New", ProductSpaceRef("China Operator"),
                  ConfluencePageRef("2"), support_mode=NamedValue(name="A"))
    repository.save_core((old,))
    access = confirmed_access(repository.database, ("DOPL:OLD",))
    synced = []

    class Service:
        def refresh_projects(self, _scope):
            repository.save_core((new,))
            access.publish((("project", "DOPL:NEW", "catalog", "China Operator"),
                            ("project", "DOPL:NEW", "roles", "China Operator"),
                            ("project", "DOPL:NEW", "facts", "China Operator")), lambda: None,
                           replace_scopes=("China Operator",))
            return {"projects": (new,), "failed": ()}

    class Coordinator:
        def __init__(self, _service): pass
        def sync(self, project_ids, _details, **_kwargs): synced.extend(project_ids)

    owner = ProjectFactsWebOwner(repository=repository, sync_coordinator_factory=Coordinator)
    owner._service = lambda *_args: Service()

    owner.refresh_and_sync_details(access, "secret", filters={"support mode": ("A",)})

    assert synced == ["DOPL:NEW"]
