from __future__ import annotations
from sqlalchemy import text

from datetime import date

from smarttest_web.release_query import ProjectReleaseQueryService


def _service(tmp_path, *, database_factory):
    database = database_factory()
    service = ProjectReleaseQueryService(database, today=lambda: date(2026, 9, 4))
    with database.transaction() as connection:
        connection.execute(
            text(
                """INSERT INTO confluence_projects
            (confluence_id,project_id,name,product_space_key,status_name,stage_name,
             catalog_page_url,source_revision,cached_at)
            VALUES(:confluence_id,:project_id,:name,:product_space_key,:status_name,:stage_name,:catalog_page_url,:source_revision,:cached_at)"""
            ),
            [
                {
                    "confluence_id": "c1",
                    "project_id": "AN16",
                    "name": "Orion",
                    "product_space_key": "DOPL",
                    "status_name": "NORMAL",
                    "stage_name": "EVT",
                    "catalog_page_url": "https://wiki/orion",
                    "source_revision": "c-r1",
                    "cached_at": "2026-09-01",
                },
                {
                    "confluence_id": "c2",
                    "project_id": "P200",
                    "name": "Nebula AN16",
                    "product_space_key": "TV",
                    "status_name": "NORMAL",
                    "stage_name": "DVT",
                    "catalog_page_url": "https://wiki/nebula",
                    "source_revision": "c-r1",
                    "cached_at": "2026-09-01",
                },
                {
                    "confluence_id": "c3",
                    "project_id": "P300",
                    "name": "Atlas",
                    "product_space_key": "SDPL",
                    "status_name": "BLOCK",
                    "stage_name": "MP",
                    "catalog_page_url": "https://wiki/atlas",
                    "source_revision": "c-r1",
                    "cached_at": "2026-09-01",
                },
            ],
        )
        connection.execute(
            text(
                "INSERT INTO confluence_project_owners(confluence_id,identity,account,display_name) VALUES(:confluence_id,:identity,:account,:display_name)"
            ),
            [
                {
                    "confluence_id": "c1",
                    "identity": "u1",
                    "account": "alice",
                    "display_name": "Alice",
                },
                {
                    "confluence_id": "c2",
                    "identity": "u2",
                    "account": "eve",
                    "display_name": "Eve",
                },
            ],
        )
        connection.execute(
            text(
                "INSERT INTO confluence_project_roles(confluence_id,role_id,role_name) VALUES(:confluence_id,:role_id,:role_name)"
            ),
            [
                {
                    "confluence_id": "c1",
                    "role_id": "role.major_fae_qa",
                    "role_name": "Major FAE QA",
                },
                {
                    "confluence_id": "c2",
                    "role_id": "role.major_fae_qa",
                    "role_name": "Major FAE QA",
                },
            ],
        )
        connection.execute(
            text(
                "INSERT INTO confluence_project_role_people(confluence_id,role_id,identity,account,display_name) VALUES(:confluence_id,:role_id,:identity,:account,:display_name)"
            ),
            [
                {
                    "confluence_id": "c1",
                    "role_id": "role.major_fae_qa",
                    "identity": "q1",
                    "account": "bob",
                    "display_name": "Bob",
                },
                {
                    "confluence_id": "c2",
                    "role_id": "role.major_fae_qa",
                    "identity": "q2",
                    "account": "mallory",
                    "display_name": "Mallory",
                },
            ],
        )
        connection.execute(
            text(
                """INSERT INTO project_current_releases
            (confluence_id,project_id,release_name,launch_time,next_target,next_target_date,
             current_hw_stage,status_summary,source_revision,cached_at)
            VALUES(:confluence_id,:project_id,:release_name,:launch_time,:next_target,:next_target_date,:current_hw_stage,:status_summary,:source_revision,:cached_at)"""
            ),
            [
                {
                    "confluence_id": "c1",
                    "project_id": "AN16",
                    "release_name": "Android 16",
                    "launch_time": "2026-09-30",
                    "next_target": "Close P1",
                    "next_target_date": "2026-09-20",
                    "current_hw_stage": "EVT2",
                    "status_summary": "On track",
                    "source_revision": "c-r1",
                    "cached_at": "2026-09-01",
                },
                {
                    "confluence_id": "c2",
                    "project_id": "P200",
                    "release_name": "Android 15",
                    "launch_time": "2026-10-20",
                    "next_target": "DVT exit",
                    "next_target_date": "2026-09-25",
                    "current_hw_stage": "DVT1",
                    "status_summary": "On track",
                    "source_revision": "c-r1",
                    "cached_at": "2026-09-01",
                },
                {
                    "confluence_id": "c3",
                    "project_id": "P300",
                    "release_name": "Android 16",
                    "launch_time": "2026-08-31",
                    "next_target": "MP",
                    "next_target_date": "2026-08-30",
                    "current_hw_stage": "MP",
                    "status_summary": "Blocked",
                    "source_revision": "c-r1",
                    "cached_at": "2026-09-01",
                },
            ],
        )
        connection.execute(
            text(
                """INSERT INTO jira_issues
            (issue_id,issue_key,summary,status_id,status_name,issue_type_id,issue_type_name,
             priority_id,priority_name,updated_at,source_revision,cached_at,resolution_id,resolution_name)
            VALUES(:issue_id,:issue_key,:summary,:status_id,:status_name,:issue_type_id,:issue_type_name,:priority_id,:priority_name,:updated_at,:source_revision,:cached_at,:resolution_id,:resolution_name)"""
            ),
            [
                {
                    "issue_id": "i1",
                    "issue_key": "SH-1",
                    "summary": "Exact release",
                    "status_id": "1",
                    "status_name": "Open",
                    "issue_type_id": "1",
                    "issue_type_name": "Bug",
                    "priority_id": "1",
                    "priority_name": "P1",
                    "updated_at": "2026-09-03",
                    "source_revision": "j-r1",
                    "cached_at": "2026-09-03",
                    "resolution_id": None,
                    "resolution_name": None,
                },
                {
                    "issue_id": "i2",
                    "issue_key": "SH-2",
                    "summary": "Missing release",
                    "status_id": "1",
                    "status_name": "Open",
                    "issue_type_id": "1",
                    "issue_type_name": "Bug",
                    "priority_id": "0",
                    "priority_name": "P0",
                    "updated_at": "2026-09-04",
                    "source_revision": "j-r1",
                    "cached_at": "2026-09-04",
                    "resolution_id": None,
                    "resolution_name": None,
                },
                {
                    "issue_id": "i3",
                    "issue_key": "SH-3",
                    "summary": "AN16 appears only in title",
                    "status_id": "1",
                    "status_name": "Open",
                    "issue_type_id": "1",
                    "issue_type_name": "Bug",
                    "priority_id": "1",
                    "priority_name": "P1",
                    "updated_at": "2026-09-04",
                    "source_revision": "j-r1",
                    "cached_at": "2026-09-04",
                    "resolution_id": None,
                    "resolution_name": None,
                },
                {
                    "issue_id": "i4",
                    "issue_key": "SH-4",
                    "summary": "Resolved blocker",
                    "status_id": "5",
                    "status_name": "Done",
                    "issue_type_id": "1",
                    "issue_type_name": "Bug",
                    "priority_id": "0",
                    "priority_name": "P0",
                    "updated_at": "2026-09-02",
                    "source_revision": "j-r1",
                    "cached_at": "2026-09-02",
                    "resolution_id": "1",
                    "resolution_name": "Fixed",
                },
            ],
        )
        connection.execute(
            text(
                """INSERT INTO jira_issue_release_facts
            (issue_id,project_business_id,software_release,severity,compare_status,qa_assignee_identity,manager_identity)
            VALUES(:issue_id,:project_business_id,:software_release,:severity,:compare_status,:qa_assignee_identity,:manager_identity)"""
            ),
            [
                {
                    "issue_id": "i1",
                    "project_business_id": " an16 ",
                    "software_release": "ANDROID   16",
                    "severity": "Major",
                    "compare_status": "",
                    "qa_assignee_identity": "qa1",
                    "manager_identity": "m1",
                },
                {
                    "issue_id": "i2",
                    "project_business_id": "AN16",
                    "software_release": "",
                    "severity": "Critical",
                    "compare_status": "",
                    "qa_assignee_identity": "qa1",
                    "manager_identity": "m1",
                },
                {
                    "issue_id": "i3",
                    "project_business_id": "P200",
                    "software_release": "",
                    "severity": "Major",
                    "compare_status": "",
                    "qa_assignee_identity": "qa2",
                    "manager_identity": "m2",
                },
                {
                    "issue_id": "i4",
                    "project_business_id": "P300",
                    "software_release": "Android 16",
                    "severity": "Critical",
                    "compare_status": "",
                    "qa_assignee_identity": "qa3",
                    "manager_identity": "m3",
                },
            ],
        )
        connection.execute(
            text(
                "INSERT INTO jira_release_field_metadata(field_name,field_key,cached_at) VALUES(:name,:key,:cached_at)"
            ),
            [
                {"name": name, "key": f"field-{index}", "cached_at": "2026-09-04"}
                for index, name in enumerate(
                    (
                        "Project ID",
                        "Software Release",
                        "Severity",
                        "Compare Status",
                        "QA Assignee",
                        "Manager",
                    )
                )
            ],
        )

    return service


def test_dashboard_uses_only_exact_project_id_and_keeps_version_pending_issues(
    tmp_path, *, database_factory
):
    result = _service(tmp_path, database_factory=database_factory).dashboard(
        visible_ids=("c1", "c2")
    )

    rows = {row["projectId"]: row for row in result["releases"]}
    assert rows["AN16"]["issueCounts"] == {
        "open": 2, "p0": 1, "p1": 1, "exact": 1, "versionPending": 1,
    }
    assert rows["P200"]["issueCounts"]["versionPending"] == 1
    assert rows["P200"]["issueCounts"]["open"] == 1
    assert rows["P200"]["health"]["state"] == "WARNING"
    assert any("版本待确认" in reason for reason in rows["P200"]["health"]["reasons"])


def test_health_priority_is_block_then_warning_then_incomplete_then_normal(
    tmp_path, *, database_factory
):
    result = _service(tmp_path, database_factory=database_factory).dashboard(
        visible_ids=("c1", "c2", "c3")
    )
    rows = {row["projectId"]: row for row in result["releases"]}

    assert rows["AN16"]["health"]["state"] == "BLOCK"
    assert any("P0" in reason for reason in rows["AN16"]["health"]["reasons"])
    assert rows["P300"]["health"]["state"] == "BLOCK"
    assert rows["P300"]["issueCounts"]["p0"] == 0


def test_issue_workbench_drilldown_matches_dashboard_scope_and_sorts_priority_then_updated(
    tmp_path, *, database_factory
):
    service = _service(tmp_path, database_factory=database_factory)
    dashboard = service.dashboard(visible_ids=("c1",), project_ids=("AN16",))
    issues = service.issues(visible_ids=("c1",), project_ids=("AN16",), page=0, page_size=20)

    assert dashboard["releases"][0]["issueCounts"]["open"] == issues["pagination"]["total"]
    assert [row["key"] for row in issues["issues"]] == ["SH-2", "SH-1"]
    assert issues["counts"] == {"exact": 1, "versionPending": 1}
    assert issues["sourceFreshness"]["jira"] == "2026-09-04"


def test_missing_required_release_data_is_explicit_not_substituted(
    tmp_path, *, database_factory
):
    service = _service(tmp_path, database_factory=database_factory)
    with service.database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE project_current_releases SET release_name='',launch_time='' WHERE confluence_id='c2'"
            )
        )

    row = service.dashboard(visible_ids=("c2",))["releases"][0]

    assert row["releaseName"] == "版本未填写"
    assert row["launchTime"] == ""
    assert row["health"]["state"] == "WARNING"
    assert any("版本名缺失" in reason for reason in row["health"]["reasons"])


def test_dashboard_and_jira_project_filters_apply_inside_authorized_database_scope(
    tmp_path, *, database_factory
):
    service = _service(tmp_path, database_factory=database_factory)
    with service.database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE jira_issues SET project_key=CASE WHEN issue_id IN ('i1','i2') THEN 'SH' ELSE 'OTHER' END"
            )
        )

    dashboard = service.dashboard(visible_ids=("c1", "c2"), filters={"owner": ["Alice"], "qa": ["Bob"]})
    issues = service.issues(visible_ids=("c1", "c2"), filters={"project": ["SH"]})

    assert [row["projectId"] for row in dashboard["releases"]] == ["AN16"]
    assert {row["projectId"] for row in issues["issues"]} == {"AN16"}


def test_component_filter_matches_each_structured_component_member(
    tmp_path, *, database_factory
):
    service = _service(tmp_path, database_factory=database_factory)
    with service.database.transaction() as connection:
        connection.execute(
            text(
                "INSERT INTO jira_issue_components(issue_id,component_id,component_name) VALUES(:issue_id,:component_id,:component_name)"
            ),
            [
                {"issue_id": "i1", "component_id": "c1", "component_name": "Video"},
                {"issue_id": "i1", "component_id": "c2", "component_name": "Audio"},
                {"issue_id": "i2", "component_id": "c3", "component_name": "Camera"},
            ],
        )

    result = service.issues(
        visible_ids=("c1",), project_ids=("AN16",), filters={"component": ["Audio"]},
    )

    assert [row["key"] for row in result["issues"]] == ["SH-1"]
    assert next(facet for facet in result["facets"] if facet["key"] == "component")["options"] == [
        "Audio", "Video",
    ]


def test_fix_version_with_comma_matches_as_one_structured_version_name(
    tmp_path, *, database_factory
):
    service = _service(tmp_path, database_factory=database_factory)
    with service.database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE project_current_releases SET release_name=:release_name WHERE confluence_id='c1'"
            ),
            {"release_name": "Android 16, EVT"},
        )
        connection.execute(
            text(
                "UPDATE jira_issue_release_facts SET software_release=:software_release WHERE issue_id='i1'"
            ),
            {"software_release": "Other release"},
        )
        connection.execute(
            text(
                """INSERT INTO jira_issue_fix_versions
            (issue_id,version_id,version_name,released,release_date) VALUES(:issue_id,:version_id,:version_name,:released,:release_date)"""
            ),
            [
                {
                    "issue_id": "i1",
                    "version_id": "v1",
                    "version_name": "Android 16, EVT",
                    "released": 0,
                    "release_date": "",
                },
                {
                    "issue_id": "i1",
                    "version_id": "v2",
                    "version_name": "Android 15",
                    "released": 0,
                    "release_date": "",
                },
            ],
        )

    result = service.issues(
        visible_ids=("c1",), project_ids=("AN16",), filters={"fixVersion": ["Android 16, EVT"]},
    )

    assert [row["key"] for row in result["issues"]] == ["SH-1"]
    assert result["issues"][0]["releaseAssociation"] == "exact"


def test_data_incomplete_summary_is_independent_of_higher_priority_health_state(
    tmp_path, *, database_factory
):
    service = _service(tmp_path, database_factory=database_factory)
    with service.database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE project_current_releases SET launch_time='' WHERE confluence_id='c3'"
            )
        )

    result = service.dashboard(visible_ids=("c1", "c2", "c3"))
    blocked = next(row for row in result["releases"] if row["projectId"] == "P300")

    assert blocked["health"]["state"] == "BLOCK"
    assert result["summary"]["dataIncomplete"] == 1


def test_dashboard_drilldown_scope_keeps_open_count_and_snapshotted_current_release(
    tmp_path, *, database_factory
):
    service = _service(tmp_path, database_factory=database_factory)
    with service.database.transaction() as connection:
        connection.execute(
            text(
                """INSERT INTO jira_issues
            (issue_id,issue_key,summary,status_id,status_name,issue_type_id,issue_type_name,
             priority_id,priority_name,updated_at,source_revision,cached_at,resolution_id,resolution_name)
            VALUES(:issue_id,:issue_key,:summary,:status_id,:status_name,:issue_type_id,:issue_type_name,:priority_id,:priority_name,:updated_at,:source_revision,:cached_at,:resolution_id,:resolution_name)"""
            ),
            {
                "issue_id": "i5",
                "issue_key": "SH-5",
                "summary": "Resolved same release",
                "status_id": "5",
                "status_name": "Done",
                "issue_type_id": "1",
                "issue_type_name": "Bug",
                "priority_id": "1",
                "priority_name": "P1",
                "updated_at": "2026-09-04",
                "source_revision": "j-r1",
                "cached_at": "2026-09-04",
                "resolution_id": "1",
                "resolution_name": "Fixed",
            },
        )
        connection.execute(
            text(
                """INSERT INTO jira_issue_release_facts
            (issue_id,project_business_id,software_release,severity,compare_status,
             qa_assignee_identity,manager_identity) VALUES(:issue_id,:project_business_id,:software_release,:severity,:compare_status,:qa_assignee_identity,:manager_identity)"""
            ),
            {
                "issue_id": "i5",
                "project_business_id": "AN16",
                "software_release": "Android 16",
                "severity": "Major",
                "compare_status": "",
                "qa_assignee_identity": "qa1",
                "manager_identity": "m1",
            },
        )

    dashboard = service.dashboard(visible_ids=("c1",), project_ids=("AN16",))
    issues = service.issues(
        visible_ids=("c1",), project_ids=("AN16",),
        filters={"_scopeRelease": ["Android 16"], "_openOnly": True},
    )

    assert dashboard["releases"][0]["issueCounts"]["open"] == issues["pagination"]["total"] == 2
    assert service.issue_detail(
        "SH-5", visible_ids=("c1",), project_ids=("AN16",),
        filters={"_scopeRelease": ["Android 16"], "_openOnly": True},
    ) is None

    with service.database.transaction() as connection:
        connection.execute(
            text(
                "UPDATE project_current_releases SET release_name='Android 17' WHERE confluence_id='c1'"
            )
        )

    stale = service.issues(
        visible_ids=("c1",), project_ids=("AN16",),
        filters={"_scopeRelease": ["Android 16"], "_openOnly": True},
    )
    assert stale["state"] == "no_snapshot"
    assert stale["pagination"]["total"] == 0


def test_release_details_are_the_same_issue_rows_used_by_counts(monkeypatch):
    service = ProjectReleaseQueryService(None, today=lambda: date(2026, 10, 19))
    monkeypatch.setattr(service, '_metadata_ready', lambda: True)
    project = ('c1', 'P1', 'Project', 'R1', '2027-01-01', '', '', '', '', '',
               'TV', 'NORMAL', 'DVT', 'https://wiki/P1', 'r1', '2026-10-19', 'r1', '2026-10-19', 'Owner', 'QA')
    def issue(identity, priority, resolution, release):
        return (identity, f'TV-{identity}', f'https://jira/TV-{identity}', 'Summary', 'Open', resolution,
                priority, 'qa', 'QA', '2026-10-01', '2026-10-19', release, '', '', '', '', '',
                '2026-10-19', 'r1', 'P1', 'TV', 'Bug', ('Audio',), ())
    result = service._release_payload(project, [issue('1', 'P0', '', 'R1'), issue('2', 'P1', 'Fixed', 'R0')])
    assert len(result['issues']) == 2
    assert result['issueCounts']['open'] == sum(not row['resolution'] for row in result['issues'])
    assert result['issueCounts']['p0'] == sum(not row['resolution'] and row['priority'] == 'P0' for row in result['issues'])
    assert result['issueCounts']['versionPending'] == sum(row['releaseAssociation'] == 'version_pending' for row in result['issues'])
    assert all(not any(key.startswith('_') for key in row) for row in result['issues'])
