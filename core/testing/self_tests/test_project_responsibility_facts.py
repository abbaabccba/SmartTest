from datetime import datetime, timezone

import pytest

from core.confluence.models import ConfluencePage
from core.product_lines import ProductLine as CoreProductLine
from core.confluence.project_catalog import (
    PRODUCT_SPACE_FACET, extract_project_detail, query_project_facts,
    refresh_project_catalogs,
)


def ProductLine(space_key, source_url, display_name):
    return CoreProductLine(display_name, (), display_name, space_key, source_url)


def _space(key="X", project_id="Alpha-ID", page_id="101", stage="Stage 1"):
    body = (
        "<table><tr><th>Page</th><th>Project ID</th><th>Support Mode</th>"
        "<th>Project Status</th><th>Current Stage</th><th>Unexpected Owner</th></tr>"
        f'<tr><td><a href="/pages/viewpage.action?pageId={page_id}">Alpha</a></td>'
        f"<td>{project_id}</td><td>A</td><td>NORMAL</td><td>{stage}</td><td>Owner-X</td></tr>"
        "</table>"
    )
    return ConfluencePage("space", "Project Space", f"https://c/display/{key}/Project+Space",
                          view_body=body, version=1,
                          updated_at=datetime(2026, 8, 1, tzinfo=timezone.utc))


class MemoryStore:
    def __init__(self):
        self.snapshot = None
        self.saved = []

    def load(self):
        return self.snapshot

    def save(self, snapshot):
        self.snapshot = snapshot
        self.saved.append(snapshot)


class CatalogClient:
    def __init__(self, pages):
        self.pages = pages

    def get_page_by_url(self, url, *, prefer_export=False):
        return self.pages[url]


def test_catalog_sync_preserves_dynamic_fields_and_publishes_ready_snapshot():
    page = _space()
    store = MemoryStore()
    snapshot = refresh_project_catalogs(
        CatalogClient({page.url: page}), store,
        (ProductLine("X", page.url, "Line X"),),
    )
    assert snapshot["phase"] == "catalog_ready"
    assert snapshot["projects"][0]["space_key"] == "X"
    assert snapshot["projects"][0]["fields"]["unexpected owner"] == "Owner-X"
    assert snapshot["field_discrepancies"] == ["Unexpected Owner"]
    assert store.saved[-1] == snapshot


def test_recent_client_product_space_contract_accepts_chinese_page_header_and_dynamic_fields():
    body = (
        "<table><tbody><tr><th>页面</th><th>Project ID</th><th>ODM</th>"
        "<th>Project Owner</th><th>Current Stage</th></tr>"
        '<tr><td><a href="/pages/viewpage.action?pageId=900">Apollo</a></td>'
        "<td>TV-100</td><td>ODM-X</td><td>Alice</td><td>EVT</td></tr>"
        "</tbody></table>"
    )
    page = ConfluencePage(
        "10", "Project Space", "https://c/display/TV/Project+Space",
        view_body=body, version=7,
    )

    snapshot = refresh_project_catalogs(
        CatalogClient({page.url: page}), MemoryStore(),
        (ProductLine("TV", page.url, "TV Business"),),
    )

    assert snapshot["projects"][0]["page_id"] == "900"
    assert snapshot["projects"][0]["fields"] == {
        "page": "Apollo", "project id": "TV-100", "odm": "ODM-X",
        "project owner": "Alice", "current stage": "EVT",
    }


@pytest.mark.parametrize(("source_name", "expected_name"), (
    ("1. ★ Apollo - Project Status Report", "Apollo"),
    ("** 02) Orion Project Status Report **", "Orion"),
    ("Clean Project", "Clean Project"),
))
def test_catalog_canonicalizes_only_the_project_display_name(source_name, expected_name):
    body = (
        "<table><tr><th>Page</th><th>Project ID</th><th>Project Owner</th></tr>"
        f'<tr><td><a href="/pages/viewpage.action?pageId=900">{source_name}</a></td>'
        "<td>P100</td><td>Owner One, Owner Two</td></tr></table>"
    )
    page = ConfluencePage("10", "Project Space", "https://c/display/X/Project+Space", view_body=body)

    project = refresh_project_catalogs(
        CatalogClient({page.url: page}), MemoryStore(),
        (ProductLine("X", page.url, "Line X"),),
    )["projects"][0]

    assert project["name"] == expected_name
    assert project["fields"]["page"] == source_name
    assert [person["name"] for person in project["project_owners"]] == ["Owner One", "Owner Two"]


def test_catalog_sync_publishes_each_space_without_detail_fetches():
    first, second = _space("X"), _space("Y", "Beta-ID", "102", "Stage 4")
    store = MemoryStore()
    refresh_project_catalogs(CatalogClient({first.url: first, second.url: second}), store, (
        ProductLine("X", first.url, "Line X"), ProductLine("Y", second.url, "Line Y"),
    ))
    assert [row["catalog_progress"]["completed"] for row in store.saved] == [1, 2]
    assert store.saved[-1]["phase"] == "catalog_ready"


def test_forbidden_catalog_space_is_silently_absent_without_removing_other_spaces():
    allowed = _space("X")

    class ForbiddenError(RuntimeError):
        response = type("Response", (), {
            "status_code": 403,
            "headers": {"content-type": "application/json;charset=UTF-8"},
        })()

    class PartialClient:
        def get_page_by_url(self, url, *, prefer_export=False):
            if "/Y/" in url:
                raise ForbiddenError("private response")
            return allowed

    snapshot = refresh_project_catalogs(PartialClient(), MemoryStore(), (
        ProductLine("X", allowed.url, "Line X"),
        ProductLine("Y", "https://c/display/Y/Project+Space", "Line Y"),
    ))
    assert [row["space_key"] for row in snapshot["projects"]] == ["X"]
    assert [source["space_key"] for source in snapshot["sources"]] == ["X"]


def test_catalog_authentication_failure_is_not_published_as_empty_ready_snapshot():

    class Unauthorized(RuntimeError):
        response = type("Response", (), {
            "status_code": 401, "headers": {"content-type": "text/html"},
        })()

    class Client:
        def get_page_by_url(self, _url, *, prefer_export=False):
            raise Unauthorized("credentials rejected")

    store = MemoryStore()
    with pytest.raises(Unauthorized):
        refresh_project_catalogs(Client(), store, (
            ProductLine("TV", "https://c/display/TV/Project+Space", "TV Business"),
        ))

    assert store.saved == []


def test_single_project_extraction_expands_structured_and_delimited_people():
    catalog = refresh_project_catalogs(
        CatalogClient({"https://c/display/X/Project+Space": _space()}), MemoryStore(),
        (ProductLine("X", "https://c/display/X/Project+Space", "Line X"),),
    )["projects"][0]

    class DetailClient:
        def get_page_by_url(self, url, *, prefer_export=False):
            return ConfluencePage("101", "Alpha Project", url)

        def get_page_children(self, page_id):
            return ([ConfluencePage("basic", "Alpha-Basic Information", "https://c/basic", version=2)]
                    if page_id == "101" else [])

        def get_page(self, page_id):
            return ConfluencePage(page_id, "Alpha-Basic Information", "https://c/basic", body=(
                '<table><tr><th>FAE QA</th><td><ri:user ri:userkey="u1"/>, Fae Two</td></tr></table>'
            ), version=2)

        def get_user_display_name(self, identity):
            return "Fae One"

    result = extract_project_detail(DetailClient(), catalog)
    assert [person["name"] for person in result["roles"]["FAE QA"]] == ["Fae One", "Fae Two"]
    assert result["detail_source"]["version"] == 2


def test_single_project_extraction_reads_launch_os_from_basic_information():
    catalog = refresh_project_catalogs(
        CatalogClient({"https://c/display/TV/Project+Space": _space(key="TV")}), MemoryStore(),
        (ProductLine("TV", "https://c/display/TV/Project+Space", "TV Business"),),
    )["projects"][0]
    catalog["fields"]["launch os"] = "Catalog value"
    catalog.update(status="current", detail_source={"role_parser_version": 2})

    class DetailClient:
        def get_page_by_url(self, url, *, prefer_export=False):
            return ConfluencePage("101", "Alpha Project", url)

        def get_page_children(self, page_id):
            return ([ConfluencePage("basic", "Alpha-Basic Information", "https://c/basic", version=2)]
                    if page_id == "101" else [])

        def get_page(self, page_id):
            return ConfluencePage(page_id, "Alpha-Basic Information", "https://c/basic", body=(
                "<table><tr><th>Lunch OS</th><td>Wrong label</td></tr>"
                "<tr><th>Launch OS</th><td>Android 16</td></tr></table>"
            ), version=2)

        def get_user_display_name(self, identity):
            raise AssertionError(identity)

    result = extract_project_detail(DetailClient(), catalog)

    assert result["fields"]["launch os"] == "Android 16"


@pytest.mark.parametrize("wifi_module", ["W1", "module W2 pro", "w1 + bt"])
def test_wifi_module_project_extracts_wifi_test_owner_roles(wifi_module):
    catalog = refresh_project_catalogs(
        CatalogClient({"https://c/display/TV/Project+Space": _space(key="TV")}), MemoryStore(),
        (ProductLine("TV", "https://c/display/TV/Project+Space", "TV Business"),),
    )["projects"][0]
    root = ConfluencePage("root", "Alpha", "https://c/root")
    basic = ConfluencePage("basic", "Alpha-Basic Information", "https://c/basic", version=2)
    test_information = ConfluencePage("test-info", "Test Information", "https://c/test-info")
    test_plan = ConfluencePage("test-plan", "Test Plan", "https://c/test-plan")
    wifi_plan = ConfluencePage("wifi-plan", "WiFi Test Plan", "https://c/wifi-plan", version=3)

    class DetailClient:
        def get_page_by_url(self, _url, *, prefer_export=False):
            return root

        def get_page_children(self, page_id):
            return {
                "root": [basic], "basic": [test_information],
                "test-info": [test_plan], "test-plan": [wifi_plan],
            }.get(page_id, [])

        def get_page(self, page_id):
            if page_id == "basic":
                return ConfluencePage(page_id, basic.title, basic.url, body=(
                    f"<table><tr><th>WIFI Module</th><td>{wifi_module}</td></tr>"
                    "<tr><th>FAE QA</th><td>Original QA</td></tr></table>"
                ), version=2)
            return ConfluencePage(page_id, wifi_plan.title, wifi_plan.url, body=(
                '<table><tr><th>Test Owner</th><td><ri:user ri:userkey="wifi.owner"/></td></tr></table>'
            ), version=3)

        def get_user_display_name(self, identity):
            return {"wifi.owner": "WiFi Owner"}.get(identity, identity)

    result = extract_project_detail(DetailClient(), catalog)

    assert result["fields"]["wifi module"] == wifi_module
    assert result["roles"]["WiFi Major FAE QA"] == result["roles"]["WiFi FAE QA"] == [{
        "identity": "wifi.owner", "name": "WiFi Owner", "role": "Test Owner",
        "source_evidence": {"kind": "ri:user", "segment": 0},
    }]
    assert result["roles"]["WiFi QA Reviewer"][0]["identity"] == "zijie.chen"


def test_wifi_module_project_uses_unknown_when_test_owner_is_missing():
    catalog = refresh_project_catalogs(
        CatalogClient({"https://c/display/TV/Project+Space": _space(key="TV")}), MemoryStore(),
        (ProductLine("TV", "https://c/display/TV/Project+Space", "TV Business"),),
    )["projects"][0]
    root = ConfluencePage("root", "Alpha", "https://c/root")
    basic = ConfluencePage("basic", "Basic Information", "https://c/basic")
    wifi_plan = ConfluencePage("wifi", "WiFi Test Plan", "https://c/wifi")

    class DetailClient:
        def get_page_by_url(self, _url, *, prefer_export=False): return root
        def get_page_children(self, page_id): return [basic, wifi_plan] if page_id == "root" else []
        def get_page(self, page_id):
            body = "<table><tr><th>WIFI Module</th><td>w2</td></tr></table>" if page_id == "basic" else "<table></table>"
            return ConfluencePage(page_id, "Basic Information" if page_id == "basic" else "WiFi Test Plan", "", body=body)
        def get_user_display_name(self, identity): return identity

    result = extract_project_detail(DetailClient(), catalog)

    assert result["roles"]["WiFi Major FAE QA"] == result["roles"]["WiFi FAE QA"] == [{
        "identity": "", "name": "Unknown", "role": "Test Owner",
        "source_evidence": {"kind": "missing", "segment": 0},
    }]


def test_realistic_role_cell_expands_eleven_people_without_turning_notes_into_people():
    catalog = refresh_project_catalogs(
        CatalogClient({"https://c/display/X/Project+Space": _space()}), MemoryStore(),
        (ProductLine("X", "https://c/display/X/Project+Space", "Line X"),),
    )["projects"][0]
    identities = [f"user-{index}" for index in range(11)]
    segments = [
        f'<ri:user ri:userkey="{identity}"/> {"NPI Owner" if index == 0 else "certification description"}'
        for index, identity in enumerate(identities)
    ]
    segments.append('<ri:user ri:userkey="user-0"/> NPI HDMI/Audio')

    class DetailClient:
        def get_page_by_url(self, url, *, prefer_export=False):
            return ConfluencePage("101", "Alpha Project", url)

        def get_page_children(self, page_id):
            return ([ConfluencePage("basic", "Alpha-Basic Information", "https://c/basic", version=2)]
                    if page_id == "101" else [])

        def get_page(self, page_id):
            body = "<br/>".join(segments)
            return ConfluencePage(page_id, "Alpha-Basic Information", "https://c/basic",
                                  body=f"<table><tr><th>FAE QA</th><td>{body}</td></tr></table>", version=2)

        def get_user_display_name(self, identity):
            return f"Member {identity.split('-')[-1]}"

    result = extract_project_detail(DetailClient(), catalog)
    people = result["roles"]["FAE QA"]
    assert [person["identity"] for person in people] == identities
    assert len(people) == 11
    assert not {"NPI Owner", "NPI HDMI/Audio", "certification description"} & {
        person["name"] for person in people
    }
    hierarchy = query_project_facts({"projects": [result]})["ownerHierarchy"]
    fae_people = next(role["people"] for role in hierarchy if role["role"] == "FAE QA")
    assert len(fae_people) == 11
    assert all(len(person["projects"]) == 1 for person in fae_people)


def test_cached_root_id_avoids_url_resolution_and_still_locates_basic_sibling():
    catalog = refresh_project_catalogs(
        CatalogClient({"https://c/display/X/Project+Space": _space()}), MemoryStore(),
        (ProductLine("X", "https://c/display/X/Project+Space", "Line X"),),
    )["projects"][0]
    catalog.update(page_id="", entry_page_id="status", root_page_id="root")
    root = ConfluencePage("root", "Alpha", "https://c/root")
    status = ConfluencePage("status", "1. Alpha-Project Status Report", "https://c/status")
    basic = ConfluencePage("basic", "2. Alpha-Basic Information", "https://c/basic", version=3)

    class CachedRootClient:
        def get_page_by_url(self, url, *, prefer_export=False):
            raise AssertionError("cached root must avoid URL resolution")

        def get_page(self, page_id):
            if page_id == "root":
                return root
            return ConfluencePage(page_id, basic.title, basic.url,
                                  body="<table><tr><th>FAE QA</th><td>TBD</td></tr></table>", version=3)

        def get_page_children(self, page_id):
            return [status, basic] if page_id == "root" else []

        def get_user_display_name(self, identity):
            raise AssertionError("plain readable fallback needs no identity lookup")

    result = extract_project_detail(CachedRootClient(), catalog)
    assert result["entry_page_id"] == "status"
    assert result["root_page_id"] == "root"
    assert result["detail_source"]["page_id"] == "basic"


@pytest.mark.parametrize(
    "search",
    (
        "alpha-id", "alpha", "x", "customer north", "fae one",
        "alice.account", "user-identity", "custom field", "secret value",
    ),
)


def test_local_query_searches_all_local_project_person_and_field_text(search):
    snapshot = {"projects": [{
        "identity": "X:101", "page_id": "101", "project_id": "Alpha-ID", "name": "Alpha",
        "space_key": "X", "page_url": "https://c/101", "active": True,
        "customer_summary": "Customer North",
        "fields": {"support mode": "A", "current stage": "Stage 1", "Custom Field": "Secret Value"},
        "roles": {"FAE QA": [{"identity": "user-identity", "account": "alice.account", "name": "Fae One"}]},
    }], "stage_domains": {"X": ["Stage 1"]}}
    result = query_project_facts(snapshot, filters={PRODUCT_SPACE_FACET: "X"}, search=search)
    assert [row["project_id"] for row in result["projects"]] == ["Alpha-ID"]
    assert result["facets"]["current stage"] == ["Stage 1"]
    assert len(result["ownerHierarchy"][1]["people"][0]["projects"]) == 1


def test_role_workload_groups_only_empty_assignments_without_colliding_with_named_unknown():
    members = [[], [{"name": "  ", "identity": " "}],
               [{"name": "Unknown", "identity": "Unknown"}],
               [{"name": "NA", "identity": "NA"}],
               [{"name": "N/A", "identity": "N/A"}],
               [{"name": "Alice", "identity": "alice"}, {"name": "", "identity": ""}]]
    projects = [{"identity": str(index), "space_key": "X", "roles": {"Major FAE QA": people}}
                for index, people in enumerate(members)]
    result = query_project_facts({"projects": projects})
    people = next(role["people"] for role in result["ownerHierarchy"] if role["role"] == "Major FAE QA")
    grouped = {(person["identity"], person["name"]): [p["identity"] for p in person["projects"]]
               for person in people}
    assert grouped == {("", ""): ["0", "1"], ("Unknown", "Unknown"): ["2"],
                       ("NA", "NA"): ["3"], ("N/A", "N/A"): ["4"], ("alice", "Alice"): ["5"]}
    assert [project["roles"] for project in result["projects"]] == [project["roles"] for project in projects]


def test_local_query_keeps_authorized_facet_options_when_filters_match_no_projects():
    snapshot = {"projects": [
        {
            "identity": "X:101", "project_id": "P-2025", "name": "Alpha", "space_key": "X", "active": True,
            "fields": {"date of commercial approval": "18 Aug 2025", "project owner": "Alice"}, "roles": {},
        },
        {
            "identity": "Y:102", "project_id": "P-2026", "name": "Beta", "space_key": "Y", "active": True,
            "fields": {"date of commercial approval": "19 Aug 2026", "project owner": "Bob"}, "roles": {},
        },
    ]}

    result = query_project_facts(
        snapshot,
        filters={"date of commercial approval": "2025"},
        search="no project matches",
    )

    assert result["projects"] == []
    assert result["facets"]["date of commercial approval"] == [2025, 2026]
    assert result["facets"]["project owner"] == ["Alice", "Bob"]
    assert result["facets"][PRODUCT_SPACE_FACET] == [
        {"value": "X", "label": "X"}, {"value": "Y", "label": "Y"},
    ]


def test_catalog_uses_injected_task_manager_for_each_product_space():
    from concurrent.futures import Future
    submitted=[]
    class Manager:
        def submit(self, label, runner):
            submitted.append(label); future=Future()
            try: future.set_result(runner(None, None))
            except Exception as error: future.set_exception(error)
            return future
    class Store:
        def load(self): return {"projects": []}
        def save(self, _payload): pass
    lines = (ProductLine("A", "a", "A"),)
    class Client:
        def get_page_by_url(self, _url): return type("Page", (), {"view_body":"", "body":"", "id":"1", "url":"a", "title":"A", "version":0, "updated_at":None})()
    import core.confluence.project_catalog as catalog
    monkeypatch = __import__("pytest").MonkeyPatch(); monkeypatch.setattr(catalog, "_catalog_rows", lambda *_: [])
    try:
        refresh_project_catalogs(Client(), Store(), lines, manager=Manager())
    finally:
        monkeypatch.undo()
    assert submitted == ["confluence-catalog:A"]
