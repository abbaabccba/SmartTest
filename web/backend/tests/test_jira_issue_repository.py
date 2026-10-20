from __future__ import annotations
from sqlalchemy import text

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from core.domain.detail import DetailSection, DetailState
from core.domain.values import FieldBag, NamedValue, PersonRef, SourceRevision
from core.jira.domain import (
    Issue,
    IssueAttachment,
    IssueComment,
    IssueDetails,
    IssueIdentity,
    IssueLink,
    IssueRef,
    JiraProjectRef,
    RichText,
)
from smarttest_web.jira.issue_repository import JiraIssueRepository
from smarttest_web.schema import verify_schema_revision

def _issue(revision: str = "r1") -> Issue:
    return Issue(
        identity=IssueIdentity("100", "SH-100", "https://jira/browse/SH-100"),
        summary="Cache contract",
        project=JiraProjectRef("SH", "10", "Smart Home"),
        status=NamedValue("1", "Open"),
        issue_type=NamedValue("2", "Bug"),
        priority=NamedValue("3", "Major"),
        assignee=PersonRef("u1", "alice", "Alice"),
        reporter=PersonRef("u2", "bob", "Bob"),
        created_at=datetime(2026, 8, 1, 8, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 2, 9, tzinfo=timezone.utc),
        labels=("cache", "jira"),
        revision=SourceRevision(revision),
        creator=PersonRef("u3", "carol", "Carol"),
        components=(NamedValue("10", "Video"), NamedValue("11", "Audio")),
    )


def _repository(tmp_path, *, database_factory) -> JiraIssueRepository:
    database = database_factory()
    verify_schema_revision(database)
    return JiraIssueRepository(database)


@pytest.mark.parametrize('workers', [2, 16])
def test_parallel_first_custom_field_publications_are_atomic_and_isolated(database_factory, workers):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import event
    repository = JiraIssueRepository(database_factory())
    issues = [replace(_issue(), identity=IssueIdentity(f'parallel-{index}', f'SH-PARALLEL-{index}', ''))
              for index in range(workers)]
    repository.save_core(issues)
    started = Barrier(workers)
    deleted = Barrier(2)
    def synchronize(connection, cursor, statement, parameters, context, executemany):
        if workers == 2 and statement.startswith('DELETE FROM jira_issue_custom_fields'):
            deleted.wait(timeout=10)
    event.listen(repository.database._engine, 'after_cursor_execute', synchronize)
    def publish(issue):
        started.wait(timeout=10)
        fields = FieldBag.from_mapping({'customfield_1': issue.identity.key, 'Test Case ID': 'CASE-1'})
        repository.replace_custom_fields(issue.identity.key, DetailSection.loaded(fields, source_revision='r1'))
        return fields
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            expected = list(executor.map(publish, issues))
    finally:
        event.remove(repository.database._engine, 'after_cursor_execute', synchronize)
    for issue, fields in zip(issues, expected):
        actual = repository.get(issue.identity.key, IssueDetails(custom_fields=True)).custom_fields
        assert actual.state is DetailState.LOADED
        assert dict(actual.value.values) == dict(fields.values)
        assert actual.source_revision == 'r1'


def test_custom_field_values_and_state_roll_back_together(database_factory):
    from sqlalchemy import event
    repository = JiraIssueRepository(database_factory())
    issue = _issue()
    repository.save_core([issue])
    original = DetailSection.loaded(FieldBag.from_mapping({'old': 'kept'}), source_revision='r1')
    repository.replace_custom_fields(issue.identity.key, original)
    def fail_state(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO jira_issue_detail_states'):
            raise RuntimeError('state publication failed')
    event.listen(repository.database._engine, 'before_cursor_execute', fail_state)
    try:
        with pytest.raises(RuntimeError, match='state publication failed'):
            repository.replace_custom_fields(issue.identity.key, DetailSection.loaded(
                FieldBag.from_mapping({'new': 'must roll back'}), source_revision='r2'))
    finally:
        event.remove(repository.database._engine, 'before_cursor_execute', fail_state)
    assert repository.get(issue.identity.key, IssueDetails(custom_fields=True)).custom_fields == original


def test_standard_resolution_round_trips_and_can_be_cleared(
    tmp_path, *, database_factory
) -> None:
    repository = _repository(tmp_path, database_factory=database_factory)
    issue = replace(_issue(), resolution=NamedValue("1", "Fixed"))
    repository.save_core((issue,))
    reopened = JiraIssueRepository(repository.database)
    assert reopened.get(issue.identity.key, IssueDetails()).resolution == issue.resolution
    reopened.save_core((replace(issue, resolution=None),))
    assert reopened.get(issue.identity.key, IssueDetails()).resolution is None


def test_issue_core_and_all_detail_states_round_trip_without_field_loss(
    tmp_path, *, database_factory
) -> None:
    repository = _repository(tmp_path, database_factory=database_factory)
    issue = _issue()
    comment = IssueComment(
        "c1", {"type": "doc", "text": "body"}, PersonRef("u3", "carol", "Carol"),
        datetime(2026, 8, 3, tzinfo=timezone.utc), None,
    )
    attachment = IssueAttachment("a1", "log.txt", "https://jira/a1", 42, None)
    link = IssueLink("l1", "Blocks", "outward", IssueRef("200", "SH-200", "https://jira/browse/SH-200", "Target"))

    repository.save_core((issue,))
    repository.replace_description("SH-100", DetailSection.loaded(RichText({"text": "description"}), source_revision="r1"))
    repository.replace_comments("SH-100", DetailSection.stale((comment,), source_revision="r0"))
    repository.replace_attachments("SH-100", DetailSection.failed("remote_unavailable", value=(attachment,), source_revision="r0"))
    repository.replace_links("SH-100", DetailSection())
    repository.replace_custom_fields("SH-100", DetailSection.loaded(FieldBag.from_mapping({"customfield_1": ["x", 2]}), source_revision="r1"))

    loaded = repository.get("SH-100", IssueDetails(True, True, True, True, True))

    assert replace(loaded, description=DetailSection(), comments=DetailSection(), attachments=DetailSection(), links=DetailSection(), custom_fields=DetailSection()) == issue
    assert loaded.description == DetailSection.loaded(RichText({"text": "description"}), source_revision="r1")
    assert loaded.comments == DetailSection.stale((comment,), source_revision="r0")
    assert loaded.attachments == DetailSection.failed("remote_unavailable", value=(attachment,), source_revision="r0")
    assert loaded.links.state is DetailState.UNLOADED
    assert loaded.custom_fields == DetailSection.loaded(FieldBag.from_mapping({"customfield_1": ["x", 2]}), source_revision="r1")


def test_detail_replace_failure_rolls_back_value_and_state(
    tmp_path, *, database_factory
) -> None:
    repository = _repository(tmp_path, database_factory=database_factory)
    repository.save_core((_issue(),))
    original = DetailSection.loaded(RichText({"text": "kept"}), source_revision="r1")
    repository.replace_description("SH-100", original)

    with pytest.raises(TypeError):
        repository.replace_description(
            "SH-100", DetailSection.loaded(RichText({"not_json": {1}}), source_revision="r2")
        )

    assert repository.get("SH-100", IssueDetails(description=True)).description == original


def test_release_projection_uses_field_metadata_names_and_standard_fix_versions(
    tmp_path, *, database_factory
) -> None:
    repository = _repository(tmp_path, database_factory=database_factory)
    repository.save_core((_issue(),))

    repository.replace_release_fields(
        "SH-100",
        {
            "customfield_101": "P100",
            "customfield_102": {"value": "Android 16"},
            "customfield_103": [{"value": "Critical"}],
            "customfield_104": {"name": "qa.user", "displayName": "QA User"},
            "resolutiondate": "2026-09-03T08:30:00+00:00",
            "fixVersions": [{"id": "v1", "name": "Android 16", "released": False, "releaseDate": "2026-11-02"}],
        },
        {
            "customfield_101": "Project ID", "customfield_102": "Software Release",
            "customfield_103": "Severity", "customfield_104": "QA Assignee",
            "customfield_105": "Compare Status", "customfield_106": "Manager",
        },
    )

    with repository.database.connect() as connection:
        fact = connection.execute(
            text("SELECT * FROM jira_issue_release_facts WHERE issue_id='100'")
        ).fetchone()
        versions = connection.execute(
            text(
                "SELECT version_id,version_name,released,release_date FROM jira_issue_fix_versions"
            )
        ).fetchall()
        metadata = dict(
            connection.execute(
                text("SELECT field_name,field_key FROM jira_release_field_metadata")
            ).fetchall()
        )
    assert fact == ("100", "P100", "Android 16", "Critical", "", "qa.user", "", "2026-09-03T08:30:00+00:00")
    assert versions == [("v1", "Android 16", 0, "2026-11-02")]
    assert metadata["Project ID"] == "customfield_101"


def test_failed_sections_round_trip_explicit_value_presence_without_revision(
    tmp_path, *, database_factory
) -> None:
    repository = _repository(tmp_path, database_factory=database_factory)
    repository.save_core((_issue(),))
    comment = IssueComment("c1", {"text": "old"})
    repository.replace_comments(
        "SH-100", DetailSection.failed("remote_unavailable", value=(comment,)),
    )
    repository.replace_links(
        "SH-100", DetailSection.failed("remote_unavailable", value=()),
    )
    repository.replace_custom_fields(
        "SH-100", DetailSection.failed("remote_unavailable", value=FieldBag()),
    )

    loaded = repository.get(
        "SH-100", IssueDetails(comments=True, links=True, custom_fields=True),
    )

    assert loaded.comments.value == (comment,)
    assert loaded.comments.source_revision == ""
    assert loaded.links.value == ()
    assert loaded.custom_fields.value == FieldBag()


def test_delete_cascades_details_and_clear_is_isolated_from_confluence(
    tmp_path, *, database_factory
) -> None:
    repository = _repository(tmp_path, database_factory=database_factory)
    repository.save_core((_issue(),))
    repository.replace_description("SH-100", DetailSection.loaded(RichText("value")))
    with repository.database.transaction() as connection:
        connection.execute(
            text(
                "INSERT INTO confluence_projects(confluence_id,project_id,name,cached_at) VALUES('c1','P1','kept','now')"
            )
        )

    repository.delete("SH-100")
    with repository.database.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM jira_issue_descriptions")
            ).fetchone()[0]
            == 0
        )
    repository.save_core((_issue(),))
    repository.clear()

    with repository.database.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM jira_issues")).fetchone()[0]
            == 0
        )
        assert (
            connection.execute(text("SELECT name FROM confluence_projects")).fetchone()[
                0
            ]
            == "kept"
        )


def test_core_fills_missing_sections_without_replacing_loaded_details(database_factory):
    database = database_factory()
    repository = JiraIssueRepository(database)
    issue = _issue()
    repository.save_core([issue])
    repository.replace_description(
        issue.identity.key, DetailSection.loaded(RichText("kept"), source_revision="r1")
    )
    with database.transaction() as connection:
        connection.execute(text("DELETE FROM jira_issue_detail_states WHERE section_name IN ('comments','links')"))
    repository.save_core([issue])
    with database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM jira_issue_detail_states")).scalar_one() == 5
    restored = repository.get(issue.identity.key, IssueDetails(description=True))
    assert restored.description.state is DetailState.LOADED
    assert restored.description.value == RichText("kept")


@pytest.mark.parametrize("publish_details", [False, True])
def test_core_section_initialization_preserves_concurrent_writers(database_factory, publish_details):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    database = database_factory()
    repository = JiraIssueRepository(database)
    issue = _issue()
    repository.save_core([issue])
    with database.transaction() as connection:
        connection.execute(text("DELETE FROM jira_issue_detail_states WHERE section_name IN ('comments','links')"))
    barrier = Barrier(2)

    def write(publish):
        barrier.wait(timeout=5)
        if publish:
            repository.replace_description(
                issue.identity.key,
                DetailSection.loaded(RichText("concurrent"), source_revision="r1"),
            )
        else:
            repository.save_core([issue])

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(write, False)
        second = pool.submit(write, publish_details)
        first.result(timeout=10)
        second.result(timeout=10)
    with database.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM jira_issue_detail_states")).scalar_one() == 5
    if publish_details:
        restored = repository.get(issue.identity.key, IssueDetails(description=True))
        assert restored.description.state is DetailState.LOADED
        assert restored.description.value == RichText("concurrent")


def test_batch_repeated_identity_keeps_last_values_and_revision_cycle_invalidation(
    database_factory,
):
    repository = JiraIssueRepository(database_factory())
    original = _issue()
    repository.save_core([original])
    repository.replace_description(
        original.identity.key,
        DetailSection.loaded(RichText("kept"), source_revision="r1"),
    )
    repository.save_core(
        [
            replace(original, revision=SourceRevision("r2"), labels=("middle",)),
            replace(original, summary="last", labels=("last",)),
        ]
    )
    restored = repository.get(original.identity.key, IssueDetails(description=True))
    assert restored.summary == "last" and restored.labels == ("last",)
    assert restored.revision.value == "r1"
    assert restored.description.state is DetailState.STALE
    assert restored.description.value == RichText("kept")


def test_batch_child_failure_rolls_back_all_rows_and_keeps_intermediate_duplicate_validation(
    database_factory,
):
    from sqlalchemy.exc import IntegrityError

    repository = JiraIssueRepository(database_factory())
    original = _issue()
    repository.save_core([original])
    repository.replace_description(
        original.identity.key,
        DetailSection.loaded(RichText("kept"), source_revision="r1"),
    )
    fresh = replace(
        original, identity=IssueIdentity("new", "SH-NEW", ""), summary="new"
    )
    invalid = replace(
        original, labels=("duplicate", "duplicate"), revision=SourceRevision("r2")
    )
    with pytest.raises(IntegrityError):
        repository.save_core([fresh, invalid, replace(original, labels=("fixed",))])
    assert repository.get("SH-NEW", IssueDetails()) is None
    restored = repository.get(original.identity.key, IssueDetails(description=True))
    assert restored.labels == original.labels and restored.revision == original.revision
    assert restored.description.state is DetailState.LOADED
