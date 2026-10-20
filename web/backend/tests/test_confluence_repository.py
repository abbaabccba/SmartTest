from __future__ import annotations
from sqlalchemy import text

from dataclasses import replace

import pytest

from core.confluence.project import (
    ConfluencePageRef,
    ProductSpaceRef,
    Project,
    ProjectDetails,
    ProjectIdentity,
    ProjectMilestones,
    ProjectQuery,
    ProjectRole,
    SourceEvidence,
)
from core.domain.detail import DetailSection, DetailState
from core.domain.values import FieldBag, NamedValue, PersonRef, SourceRevision
from core.confluence.models import ConfluencePage
from core.confluence.project_catalog import refresh_project_catalogs
from core.confluence.project_mapper import ConfluenceProjectMapper
from core.product_lines import PRODUCT_LINES
from smarttest_web.release_query import ProjectReleaseQueryService
from smarttest_web.confluence.project_repository import ConfluenceProjectRepository
from smarttest_web.schema import verify_schema_revision

def _project(revision: str = "4") -> Project:
    return Project(
        ProjectIdentity("900", "P100"), "Project One",
        ProductSpaceRef("DOPL", "DOPL", "https://confluence/spaces/DOPL"),
        ConfluencePageRef("10", "Catalog", "https://confluence/pages/10", 4),
        NamedValue("normal", "Normal"), NamedValue("evt", "EVT"),
        NamedValue("onsite", "Onsite"), "Customer A",
        (PersonRef("u1", "alice", "Alice"),), SourceRevision(revision),
    )


def _repository(tmp_path, *, database_factory) -> ConfluenceProjectRepository:
    database = database_factory()
    verify_schema_revision(database)
    return ConfluenceProjectRepository(database)


def test_project_core_and_all_detail_states_round_trip_without_field_loss(
    tmp_path, *, database_factory
) -> None:
    repository = _repository(tmp_path, database_factory=database_factory)
    project = _project()
    roles = (ProjectRole(NamedValue("fae", "FAE QA"), (PersonRef("u2", "bob", "Bob"),)),)
    evidence = (SourceEvidence("basic", ConfluencePageRef("11", "Basic", "https://confluence/pages/11", 7)),)

    repository.save_core((project,))
    repository.replace_roles("P100", DetailSection.loaded(roles, source_revision="4"))
    repository.replace_milestones("P100", DetailSection.stale(ProjectMilestones((("SOP", "2026-10"),)), source_revision="3"))
    repository.replace_hardware("P100", DetailSection.failed("remote_unavailable", value=FieldBag.from_mapping({"board": "A"}), source_revision="3"))
    repository.replace_software("P100", DetailSection())
    repository.replace_facts("P100", DetailSection.loaded(FieldBag.from_mapping({"region": ["US", "EU"]}), source_revision="4"))
    repository.replace_evidence("P100", DetailSection.loaded(evidence, source_revision="4"))

    loaded = repository.get("P100", ProjectDetails(True, True, True, True, True, True))

    assert replace(loaded, roles=DetailSection(), milestones=DetailSection(), hardware=DetailSection(), software=DetailSection(), facts=DetailSection(), evidence=DetailSection()) == project
    assert loaded.roles == DetailSection.loaded(roles, source_revision="4")
    assert loaded.milestones == DetailSection.stale(ProjectMilestones((("SOP", "2026-10"),)), source_revision="3")
    assert loaded.hardware == DetailSection.failed("remote_unavailable", value=FieldBag.from_mapping({"board": "A"}), source_revision="3")
    assert loaded.software.state is DetailState.UNLOADED
    assert loaded.facts == DetailSection.loaded(FieldBag.from_mapping({"region": ["US", "EU"]}), source_revision="4")
    assert loaded.evidence == DetailSection.loaded(evidence, source_revision="4")


def test_catalog_transaction_preserves_duplicate_owner_and_cross_space_project_id(
    tmp_path, *, database_factory
) -> None:
    repository = _repository(tmp_path, database_factory=database_factory)
    duplicate_owner = PersonRef("u1", "alice", "Alice")
    first = replace(
        _project(),
        identity=ProjectIdentity("DOPL:P100", "P100"),
        owner_summary=(duplicate_owner, duplicate_owner),
    )
    second = replace(
        _project(),
        identity=ProjectIdentity("TV:P100", "P100"),
        product_space=ProductSpaceRef("TV", "TV"),
        owner_summary=(),
    )

    repository.save_core((first, second))

    stored = repository.list(ProjectQuery(), 0, 10)
    assert [(project.identity.confluence_id, project.identity.project_id) for project in stored.projects] == [
        ("DOPL:P100", "P100"), ("TV:P100", "P100"),
    ]
    assert repository.get("DOPL:P100", ProjectDetails()).owner_summary == (duplicate_owner,)
    assert repository.get("TV:P100", ProjectDetails()).product_space.key == "TV"


def test_project_detail_replace_failure_rolls_back_value_and_state(
    tmp_path, *, database_factory
) -> None:
    repository = _repository(tmp_path, database_factory=database_factory)
    repository.save_core((_project(),))
    original = DetailSection.loaded(FieldBag.from_mapping({"kept": "yes"}), source_revision="4")
    repository.replace_facts("P100", original)

    with pytest.raises(TypeError):
        repository.replace_facts(
            "P100", DetailSection.loaded(FieldBag.from_mapping({"bad": {1}}), source_revision="5")
        )

    assert repository.get("P100", ProjectDetails(facts=True)).facts == original


def test_current_release_projection_tracks_only_named_confluence_fields(
    tmp_path, *, database_factory
) -> None:
    repository = _repository(tmp_path, database_factory=database_factory)
    repository.save_core((_project(),))

    repository.replace_facts("P100", DetailSection.loaded(FieldBag.from_mapping({
        "Launch OS": "Android 16", "Launch Time": "2026-11-02", "MP Time": "2026-10-18",
        "Next Target": "DVT exit", "Next Target Date": "2026-09-20",
        "Current HW Stage": "DVT2", "Status Summary": "On track",
        "planned closure": "2099-01-01",
    }), source_revision="detail-r2"))

    with repository.database.connect() as connection:
        row = connection.execute(
            text("SELECT * FROM project_current_releases WHERE confluence_id='900'")
        ).fetchone()
    assert row[1:] == (
        "P100", "Android 16", "2026-11-02", "2026-10-18", "DVT exit", "2026-09-20",
        "DVT2", "On track", "detail-r2", row[-1],
    )


def test_failed_sections_round_trip_explicit_value_presence_without_revision(
    tmp_path, *, database_factory
) -> None:
    repository = _repository(tmp_path, database_factory=database_factory)
    repository.save_core((_project(),))
    repository.replace_hardware(
        "P100",
        DetailSection.failed(
            "remote_unavailable", value=FieldBag.from_mapping({"board": "old"}),
        ),
    )
    repository.replace_facts(
        "P100", DetailSection.failed("remote_unavailable", value=FieldBag()),
    )
    repository.replace_evidence(
        "P100", DetailSection.failed("remote_unavailable", value=()),
    )

    loaded = repository.get(
        "P100", ProjectDetails(hardware=True, facts=True, evidence=True),
    )

    assert loaded.hardware.value == FieldBag.from_mapping({"board": "old"})
    assert loaded.hardware.source_revision == ""
    assert loaded.facts.value == FieldBag()
    assert loaded.evidence.value == ()


def test_project_delete_cascades_details_and_clear_is_isolated_from_jira(
    tmp_path, *, database_factory
) -> None:
    repository = _repository(tmp_path, database_factory=database_factory)
    repository.save_core((_project(),))
    repository.replace_facts("P100", DetailSection.loaded(FieldBag.from_mapping({"x": 1})))
    with repository.database.transaction() as connection:
        connection.execute(
            text(
                "INSERT INTO jira_issues(issue_id,issue_key,summary,cached_at) VALUES('1','J-1','kept','now')"
            )
        )

    repository.delete("P100")
    with repository.database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM confluence_project_fields")
            ).fetchone()[0]
            == 0
        )
    repository.save_core((_project(),))
    repository.clear()

    with repository.database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM confluence_projects")
            ).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(text("SELECT summary FROM jira_issues")).fetchone()[0]
            == "kept"
        )


def test_canonical_catalog_name_is_unchanged_through_mysql_and_dashboard_query(
    tmp_path, *, database_factory
) -> None:
    page = ConfluencePage(
        "catalog", "Project Space", "https://c/display/DOPL/Project+Space",
        view_body=(
            "<table><tr><th>Page</th><th>Project ID</th><th>Launch OS</th><th>Launch Time</th></tr>"
            '<tr><td><a href="/pages/viewpage.action?pageId=900">★ 1. Apollo - Project Status Report</a></td>'
            "<td>P100</td><td>Android 16</td><td>2026-10-01</td></tr></table>"
        ),
    )
    store = type("Store", (), {"load": lambda self: None, "save": lambda self, value: setattr(self, "value", value)})()
    client = type("Client", (), {"get_page_by_url": lambda self, _url: page})()
    row = refresh_project_catalogs(
        client, store, (PRODUCT_LINES[0],),
    )["projects"][0]
    repository = _repository(tmp_path, database_factory=database_factory)
    project = ConfluenceProjectMapper().from_catalog(row)

    repository.save_core((project,))
    repository.replace_facts(project.identity.confluence_id, project.facts)
    stored = repository.get("P100", ProjectDetails())
    dashboard = ProjectReleaseQueryService(repository.database).dashboard(visible_ids=("DOPL:P100",))

    assert stored.name == "Apollo"
    assert dashboard["releases"][0]["projectName"] == "Apollo"


def test_stale_project_id_boundary_and_long_release_people(
    tmp_path, *, database_factory
):
    from core.confluence.project_rules import MAJOR_QA_ROLE_ID

    repository = _repository(tmp_path, database_factory=database_factory)
    people = tuple(
        PersonRef(f"u{i:03}", f"user{i}", f"Person {i:03} " + "x" * 40)
        for i in range(40)
    )
    project = replace(_project(), owner_summary=people)
    repository.save_core((project,))
    repository.replace_roles(
        "P100",
        DetailSection.loaded(
            (ProjectRole(NamedValue(MAJOR_QA_ROLE_ID, "QA"), people),)
        ),
    )
    repository.mark_details_stale("missing", ("roles",))
    repository.mark_details_stale("900", ("roles",))
    assert (
        repository.get("P100", ProjectDetails(roles=True)).roles.state
        is DetailState.LOADED
    )
    rows = ProjectReleaseQueryService(repository.database)._projects(["900"], (), {})
    expected = ", ".join(person.display_name for person in people)
    assert len(expected) > 1024
    assert rows[0][18] == expected
    assert rows[0][19] == expected
    repository.mark_details_stale("P100", ("roles",))
    assert (
        repository.get("P100", ProjectDetails(roles=True)).roles.state
        is DetailState.STALE
    )
