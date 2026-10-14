from core.jira.services.jql_statistics_cards import (
    QARoster, aggregate_jira_statistics, card_definition, effective_card_jql_pair, load_fae_qa_roster,
)
from core.jira.services.filter_service import jira_period_condition
import pytest
from core.product_lines import DASHBOARD_PRODUCT_LINES, PRODUCT_LINES, WIRELESS_CONNECTION
import core.jira.services.jql_statistics_cards as service


def test_annual_activities_use_last_qa_author_personnel_lines_and_ignore_issue_creator_and_project():
    from datetime import date
    class Gateway:
        def user_groups(self, account):
            assert account == 'fan.xu'
            return ('fae-iptv-qa',)
    basic = {'id':'basic','fields':{'created':'2026-01-01', 'project':{'key':'IPTV'},
        'creator':{'name':'fan.xu'}, 'comment':{'comments':[{'author':{'name':'external'}}]}}}
    comments = {'id':'nonqa','fields':{'created':'2026-01-01','project':{'key':'SH'},'creator':{'name':'outside'},
        'comment':{'comments':[{'author':{'name':'MENG.WANG1'}},{'author':{'name':'yimin.zhou'}},
                              {'author':{'name':'ping.xiong'}},{'author':{'name':'outside'}}]}},
        'changelog':{'histories':[
            {'created':'2026-09-01T00:00:00Z','author':{'name':'YIMIN.ZHOU'},'items':[{'field':'status','fromString':'Resolved','toString':'Verified'}]},
            {'created':'2026-08-01T00:00:00Z','author':{'name':'meng.wang1'},'items':[{'field':'status','fromString':'Resolved','toString':'Verified'}]}]}}
    facts = service.build_annual_facts(Gateway(), [basic], [], current_activities=[comments], previous_activities=[])
    raw = facts.pop('_facts')
    facts['availability']={year:{'basic':True,'activity':True} for year in ('current','previous')}
    result=service.aggregate_annual_facts(raw,facts,'year',date(2026,10,14))
    lines={line['id']:line for line in result['productLines']}
    assert result['teamTotal']==1
    assert result['unassignedCount']==0
    assert result['unassignedCommentCount']==1
    assert sum(p['commentCount'] for p in lines[WIRELESS_CONNECTION.name]['people'])==1
    assert sum(p['commentCount'] for p in lines['TV']['people'])==1
    assert sum(p['verifyCount'] for p in lines['TV']['people'])==1
    assert sum(p['verifyCount'] for p in lines[WIRELESS_CONNECTION.name]['people'])==0
    assert sum(p['commentCount'] for line in result['previous']['productLines'] for p in line['people'])==0


def test_product_line_names_use_the_shared_business_free_labels():
    assert [line.name for line in DASHBOARD_PRODUCT_LINES] == [
        "China Operator", "Smart Device", "TV", "Global Operator & STB", "Wireless Connection",
    ]


def test_registry_defines_all_cards_and_task_uses_the_five_creator_groups():
    assert tuple(service.JIRA_STATISTICS_CARDS) == ("self-test", "task", "customer-feedback")
    task = card_definition("task")
    assert task.first_metric_label == "Tasks"
    assert "(issuetype = Task)" in task.fixed_jql("month")
    assert task.fixed_jql("month").count("creator IN membersOf(") == 5
    assert '"Channel of Reporter"' not in task.fixed_jql("month")


def test_every_jira_card_restricts_creators_to_the_fae_qa_groups():
    for definition in service.JIRA_STATISTICS_CARDS.values():
        assert definition.fixed_jql("month").count("creator IN membersOf(") == 5


@pytest.mark.parametrize("card_key", ("self-test", "task", "customer-feedback"))
def test_activity_scope_keeps_card_business_predicate_without_creator_boundary(card_key):
    from datetime import date
    definition = card_definition(card_key)
    pair = service.activity_card_jql_pair(card_key, "status = Open OR status = Closed ORDER BY key", date(2026, 10, 14))
    assert pair["current"]["jql"].startswith('(status = Open OR status = Closed) AND (')
    for value in pair.values():
        assert definition.custom_jql in value["jql"]
        assert 'creator IN' not in value["jql"]
        assert value["jql"].endswith('ORDER BY key')
    assert 'created >= "2026-01-01"' in pair["current"]["jql"]
    assert 'created >= "2025-01-01"' in pair["previous"]["jql"]


@pytest.mark.parametrize("card_key", ("self-test", "task", "customer-feedback"))
def test_effective_card_jql_intersects_user_scope_and_preserves_order(card_key):
    effective = effective_card_jql_pair(card_key, "project = TV ORDER BY created DESC", "week")["current"]["jql"]
    assert effective.startswith("(project = TV) AND (")
    assert jira_period_condition("week") in effective
    assert effective.endswith("ORDER BY created DESC")


def test_card_period_pair_changes_only_the_created_range():
    from datetime import date
    from core.jira.services.jql_statistics_cards import effective_card_jql_pair

    pair = effective_card_jql_pair("task", "project = SH ORDER BY created DESC", "month", date(2026, 3, 12))

    current_time = 'created >= "2026-03-01" AND created <= now()'
    previous_time = 'created >= "2026-02-01" AND created < "2026-03-01"'
    assert current_time in pair["current"]["jql"]
    assert previous_time in pair["previous"]["jql"]
    assert pair["current"]["jql"].replace(current_time, "TIME") == pair["previous"]["jql"].replace(previous_time, "TIME")
    assert pair["current"]["jql"].endswith("ORDER BY created DESC")


def test_customer_definition_has_wireless_priority_and_mutually_exclusive_standard_projects():
    jql = card_definition("customer-feedback").fixed_jql("year")
    assert '"Channel of Reporter" = "Customer-Feedback"' in jql
    assert 'labels IN ("Customer_W1", "Customer_W1U", "Customer_W2L", "customer_w2", "Customer_w1u", "customer-w2", "customer-w2L", "customer_w1d")' in jql
    assert 'resolution NOT IN ("Invalid Case", "Cannot Reproduce", "HW Fix", "Won\'t Fix", "Won\'t Do")' in jql
    assert 'project NOT IN ("RD SW Platform", "Wireless Project")' in jql
    assert 'labels NOT IN (' in jql
    assert 'created >= "2026-01-01"' in jql


def test_cards_share_qa_group_mapping_even_with_customer_labels():
    wireless = issue(project=TV_LINE.jira_project_keys[0], creator={"name": "wifi"})
    regular = issue(project=TV_LINE.jira_project_keys[0], creator={"name": "qa"})
    regular["fields"]["labels"] = ["Customer_W1"]
    class Gateway:
        def user_groups(self, account):
            return ("fae-wifi-qa",) if account == "wifi" else ("fae-tv-qa",)
    result = service.build_comparison_statistics(Gateway(), [wireless, regular], [], {})
    assert result["teamTotal"] == 2
    assert result["productLines"][2]["people"][0]["identity"] == "qa"
    assert result["productLines"][4]["people"][0]["identity"] == "wifi"


def test_self_test_groups_by_qa_creator_not_non_qa_reporter_or_assignee():
    payload = issue(creator={"name": "qa", "displayName": "QA"}, reporter={"name": "outside"})
    payload["fields"]["assignee"] = {"name": "outside", "displayName": "Outside"}
    result = aggregate_jira_statistics([payload], roster(qa=(TV_LINE.name,)))
    assert result.productLines[2].people[0].identity == "qa"
    assert result.productLines[2].people[0].displayName == "QA"


def test_wireless_creator_owns_unknown_project_while_other_creator_remains_unmapped():
    result = aggregate_jira_statistics([
        issue(project='RK', creator={'name': 'wifi', 'displayName': 'WiFi'}),
        issue(project='RK', creator={'name': 'other', 'displayName': 'Other'}),
    ], roster(wifi=(WIRELESS_CONNECTION.name,), other=(CHINA.name,)))
    assert result.productLines[4].people[0].bugCount == 1
    assert result.productLines[4].people[0].identity == 'wifi'
    assert result.unmappedCount == 1
    assert result.teamTotal == 2
    assert WIRELESS_CONNECTION.jira_project_keys == ()


def test_applied_collection_preserves_page_scope_and_creator_grouping():
    result = aggregate_jira_statistics([
        issue(issue_type="Task", creator={"name": "outside", "displayName": "Outside"}),
        issue(creator={"name": "wifi", "displayName": "Wifi"}),
        issue(),
        issue(project="unknown", creator={"name": "outside"}),
        {},
    ], roster(wifi=(WIRELESS_CONNECTION.name,)))
    assert result.teamTotal == 3
    assert result.unassignedCount == 2
    assert result.unmappedCount == 0
    assert result.teamTotal == sum(person.bugCount for line in result.productLines for person in line.people) + result.unassignedCount + result.unmappedCount
    by_line = {line.id: line.people for line in result.productLines}
    assert by_line[TV_LINE.name] == ()
    assert by_line[WIRELESS_CONNECTION.name][0].identity == "wifi"


def test_group_roster_is_deterministic_and_preserves_multiple_memberships():
    class Gateway:
        def __init__(self):
            self.calls = []

        def user_groups(self, account):
            self.calls.append(account)
            return {
                "amy": ("jira-users", "fae-tv-qa"),
                "multi": ("fae-SH-qa", "fae-wifi-qa", "unrelated"),
            }[account]

    gateway = Gateway()
    first = load_fae_qa_roster(gateway, ("multi", "amy", "MULTI"))
    second = load_fae_qa_roster(Gateway(), ("amy", "multi"))

    assert tuple(account for account, _lines in first.assignments) == ("amy", "multi")
    assert dict(first.assignments)["multi"] == (SMART.name, WIRELESS_CONNECTION.name)
    assert first.fingerprint == second.fingerprint
    assert gateway.calls == ["amy", "multi"]


CHINA, SMART, TV_LINE, GLOBAL = PRODUCT_LINES


def roster(**assignments):
    return QARoster("test", tuple(
        (account, tuple(lines)) for account, lines in sorted(assignments.items())
    ))


def issue(*, project=TV_LINE.jira_project_keys[0], issue_type="Bug", creator=None, reporter=None, priority=None, resolution=None):
    return {"fields": {
        "project": {"key": project},
        "issuetype": {"name": issue_type},
        "reporter": reporter,
        "creator": creator,
        "priority": None if priority is None else {"name": priority},
        "resolution": None if resolution is None else {"name": resolution},
    }}


def test_aggregate_jira_statistics_uses_exact_jira_names_and_defined_denominators():
    rows = aggregate_jira_statistics([
        issue(creator={"name": "alice", "displayName": "Alice"}, priority="P0", resolution="Resolved"),
        issue(creator={"name": "alice", "displayName": "Alice"}, priority="p0", resolution="Invalid"),
        issue(creator={"name": "bob", "displayName": "Bob"}, resolution="Resolved"),
        issue(creator={"name": "ignored", "displayName": "Ignored"}, issue_type="Task", priority="P0"),
    ], roster(alice=(TV_LINE.name,), bob=(TV_LINE.name,)))

    assert rows.teamTotal == 3
    assert rows.to_payload()["productLines"][2]["people"][0] == {
        "identity": "alice", "displayName": "Alice", "bugCount": 2,
        "commentCount": 0, "verifyCount": 0, "invalidCount": 1,
    }


def test_missing_creators_are_counted_and_ties_sort_by_display_name():
    rows = aggregate_jira_statistics([
        issue(), issue(),
        issue(creator={"accountId": "z", "displayName": "Zed"}),
        issue(creator={"accountId": "a", "displayName": "Amy"}),
        issue(creator={"accountId": "outside", "displayName": "Outside"}),
    ], roster(a=(TV_LINE.name,), z=(TV_LINE.name,)))

    assert [row.displayName for row in rows.productLines[2].people] == ["Amy", "Zed"]
    assert rows.teamTotal == 4


def test_project_mapping_keeps_unmapped_issues_in_total_but_not_bars():
    overview = aggregate_jira_statistics([
        issue(project=CHINA.jira_project_keys[0], creator={"name": "amy", "displayName": "Amy"}),
        issue(project=SMART.jira_project_keys[0], creator={"name": "amy", "displayName": "Amy"}, resolution="Resolved"),
        issue(project=GLOBAL.jira_project_keys[0], creator={"name": "zed", "displayName": "Zed"}, priority="P0"),
        issue(project="FQ", creator={"name": "amy", "displayName": "Amy"}),
    ], roster(amy=(CHINA.name, SMART.name), zed=(GLOBAL.name,)))

    payload = overview.to_payload()
    assert [line["id"] for line in payload["productLines"]] == [line.name for line in DASHBOARD_PRODUCT_LINES]
    assert [line["label"] for line in payload["productLines"]] == [line.name for line in DASHBOARD_PRODUCT_LINES]
    assert all("projectKey" not in line for line in payload["productLines"])
    assert payload["teamTotal"] == 4
    assert payload["unmappedCount"] == 1
    assert payload["productLines"][0]["people"][0]["bugCount"] == 1
    assert payload["productLines"][1]["people"][0]["commentCount"] == 0
    assert payload["productLines"][2]["people"] == []
    assert payload["productLines"][3]["people"][0]["verifyCount"] == 0


def test_project_mapping_uses_wireless_creator_assignment_precedence():
    overview = aggregate_jira_statistics([
        issue(project=TV_LINE.jira_project_keys[0], creator={"name": "wifi", "displayName": "WiFi"}, resolution="Resolved"),
        issue(project=CHINA.jira_project_keys[0], creator={"name": "wifi", "displayName": "WiFi"}, priority="P0"),
        issue(project=TV_LINE.jira_project_keys[0], creator={"name": "tv", "displayName": "TV Owner"}),
        issue(project=SMART.jira_project_keys[0], creator={"name": "tv", "displayName": "TV Owner"}),
        issue(project=GLOBAL.jira_project_keys[0], creator={"name": "stb", "displayName": "STB Owner"}, resolution="Invalid"),
        issue(project="FQ", creator={"name": "wifi", "displayName": "WiFi"}),
        issue(project=TV_LINE.jira_project_keys[0], creator={"name": "none", "displayName": "No Assignment"}),
    ], roster(wifi=(WIRELESS_CONNECTION.name, TV_LINE.name), tv=(TV_LINE.name,), stb=(GLOBAL.name,), none=()))

    payload = overview.to_payload()
    assert [line["id"] for line in payload["productLines"]] == [line.name for line in DASHBOARD_PRODUCT_LINES]
    assert payload["teamTotal"] == 6
    assert payload["unmappedCount"] == 0
    assert {person['displayName'] for person in payload["productLines"][2]["people"]} == {'TV Owner'}
    assert payload["productLines"][3]["people"][0]["invalidCount"] == 1
    assert payload["productLines"][4]["people"][0] == {
        "identity": "wifi", "displayName": "WiFi", "bugCount": 3,
        "commentCount": 0, "verifyCount": 0, "invalidCount": 0,
    }


def test_comparison_payload_uses_one_roster_and_keeps_both_complete_overviews():
    current = [issue(creator={"name": "alice", "displayName": "Alice"})]
    previous = [issue(creator={"name": "bob", "displayName": "Bob"}, resolution="Resolved")]
    class Gateway:
        def user_groups(self, _account): return ("fae-tv-qa",)
    ranges = {"current": {"start": "2026-09-01", "end": None},
              "previous": {"start": "2026-08-01", "end": "2026-09-01"}}

    payload = service.build_comparison_statistics(
        Gateway(), current, previous, ranges,
    )

    assert payload["ranges"] == ranges
    assert payload["current"]["teamTotal"] == 1
    assert payload["previous"]["teamTotal"] == 1
    assert payload["current"]["productLines"][2]["people"][0]["displayName"] == "Alice"
    assert payload["previous"]["productLines"][2]["people"][0]["commentCount"] == 0


def test_comments_count_qa_authors_in_issue_project_not_author_product_line():
    current = issue(creator={"name": "creator"})
    current["fields"]["comment"] = {"comments": [
        {"id": "1", "author": {"name": "commenter", "displayName": "Commenter"}, "created": "2020-01-01T00:00:00.000+0000"},
        {"id": "2", "author": {"name": "commenter", "displayName": "Commenter"}, "created": "2026-09-01T00:00:00.000+0000"},
        {"id": "3", "author": {"name": "outside"}, "created": "2026-09-01T00:00:00.000+0000"},
        {"id": "4", "created": "2026-09-01T00:00:00.000+0000"},
    ]}
    previous = issue(creator={"name": "creator"})
    previous["fields"]["comment"] = {"comments": [current["fields"]["comment"]["comments"][0]]}
    calls = []
    class Gateway:
        def user_groups(self, account):
            calls.append(account)
            return {"creator": ("fae-tv-qa",), "commenter": ("fae-wifi-qa",), "outside": ()}[account]
    payload = service.build_comparison_statistics(Gateway(), [current], [previous], {})
    assert set(calls) == {"creator", "commenter", "outside"}
    assert len(calls) == 3
    for period, count in [("current", 2), ("previous", 1)]:
        summary = payload[period]
        people = {person["identity"]: person for person in summary["productLines"][2]["people"]}
        assert people["commenter"]["commentCount"] == count
        assert people["commenter"]["bugCount"] == 0
        assert people["creator"]["bugCount"] == 1
        assert people["creator"]["commentCount"] == 0
        assert "outside" not in people
        assert summary["productLines"][4]["people"] == []
        assert all("resolvedCount" not in person for person in people.values())


def test_annual_facts_match_period_aggregation_across_new_year_without_sensitive_content():
    from datetime import date
    from core.jira.services import jql_statistics_cards as service
    from core.jira.services.filter_service import jira_period_ranges
    rows = []
    for index, created in enumerate(["2026-01-01", "2026-12-21", "2026-12-28", "2027-01-01"]):
        row = issue(creator={"name": "qa"}, resolution={"name": "Invalid"})
        row["id"] = str(index)
        row["fields"]["created"] = created + "T00:00:00+08:00"
        row["fields"]["components"] = [{"name": "Audio"}, {"name": "Video"}]
        row["fields"]["comment"] = {"comments": [{"author": {"name": "qa"},
            "body": "secret-comment", "created": "2020-01-01T00:00:00Z"}]}
        row["changelog"] = {"histories": [{"author": {"name": "qa"}, "created": "2027-01-02T00:00:00Z",
            "items": [{"field": "status", "fromString": "Resolved", "toString": "Verified"}]}]}
        rows.append(row)
    class Gateway:
        def user_groups(self, _account): return ("fae-tv-qa",)
    metadata = service.build_annual_facts(Gateway(), rows[-1:], rows[:-1])
    facts = metadata.pop("_facts")
    assert "secret-comment" not in str(facts) and "histories" not in str(facts)
    metadata["availability"] = {key: {"basic": True, "verify": True} for key in ("current", "previous")}
    today = date(2027, 1, 2)
    for period in ("week", "month", "quarter", "year"):
        windows = jira_period_ranges(period, today)
        selected = {key: [row for row in rows if row["fields"]["created"][:10] >= window["start"]
                    and (window["end"] is None or row["fields"]["created"][:10] < window["end"])]
                    for key, window in windows.items()}
        expected = service.build_comparison_statistics(Gateway(), selected["current"], selected["previous"], windows)
        actual = service.aggregate_annual_facts(facts, metadata, period, today)
        assert {key: value for key, value in actual.items() if key != "availability"} == expected
    metadata["availability"]["previous"]["verify"] = False
    partial = service.aggregate_annual_facts(facts, metadata, "week", today)
    assert partial["availability"]["current"] == {"basic": True, "verify": False}
    assert all("verifyCount" not in person for line in partial["current"]["productLines"] for person in line["people"])


def test_components_count_each_module_once_and_totals_count_unique_issues():
    first = issue(creator={"name": "qa"}, resolution="Invalid")
    first["id"] = "1"
    first["fields"]["components"] = [{"name": "Audio"}, {"name": "Video"}, {"name": "Audio"}]
    second = issue(creator={"name": "qa"})
    second["id"] = "2"
    second["fields"]["components"] = [{"name": "Audio"}]
    third = issue(creator={"name": "qa"})
    third["id"] = "3"
    payload = aggregate_jira_statistics([first, second, third, first], roster(qa=(TV_LINE.name,))).to_payload()
    line = payload["productLines"][2]
    assert line["issueCount"] == 3
    assert line["invalidCount"] == 1
    assert line["components"] == [{"name": "Audio", "count": 2}, {"name": "Video", "count": 1}]
