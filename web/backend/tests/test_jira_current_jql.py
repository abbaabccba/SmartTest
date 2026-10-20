from datetime import date

import pytest

from core.jira.services import jql_statistics_cards as cards
from core.product_lines import DASHBOARD_PRODUCT_LINES

GROUPS = {"China Operator": "fae-iptv-qa", "Smart Device": "fae-SH-qa", "TV": "fae-tv-qa",
          "Global Operator & STB": "fae-stb-qa", "Wireless Connection": "fae-wifi-qa"}


@pytest.mark.parametrize("card_key", ("self-test", "task", "customer-feedback"))
@pytest.mark.parametrize("period,start", (
    ("week", "2026-10-12"),
    ("month", "2026-10-01"),
    ("quarter", "2026-10-01"),
    ("year", "2026-01-01"),
))
def test_current_card_jql_maps_product_lines_and_metrics_without_changing_user_order(card_key, period, start):
    result = cards.current_card_jql_by_product_line_and_metric(
        card_key, "status = Open OR priority = High ORDER BY created DESC", period,
        today=date(2026, 10, 17),
    )

    assert tuple(result) == tuple(line.name for line in DASHBOARD_PRODUCT_LINES)
    for line_name, metrics in result.items():
        assert tuple(metrics) == ("bugCount", "commentCount", "verifyCount", "invalidCount")
        actual_activity = cards.activity_card_jql_pair(card_key,
            "status = Open OR priority = High ORDER BY created DESC", date(2026, 10, 17))["current"]["jql"]
        assert metrics["commentCount"] == metrics["verifyCount"] == actual_activity
        assert "creator IN" not in actual_activity
        assert 'created >= "2026-01-01"' in actual_activity
        assert metrics["invalidCount"] != metrics["bugCount"]
        assert 'resolution = "Invalid"' in metrics["invalidCount"]
        query = metrics["bugCount"]
        assert query.startswith("(status = Open OR priority = High) AND (")
        if card_key == "customer-feedback":
            assert 'issuetype = Bug AND "Channel of Reporter" = "Customer-Feedback"' in query
            if line_name == "Wireless Connection":
                assert 'labels IN (' in query
                assert 'resolution NOT IN (' in query
                assert 'project NOT IN (' in query
            else:
                assert 'labels' not in query.lower()
        else:
            assert cards.card_definition(card_key).custom_jql in query
        assert f'created >= "{start}" AND created <= now()' in query
        if card_key == "customer-feedback":
            assert "creator IN" not in query
        else:
            assert f'creator IN membersOf("{GROUPS[line_name]}")' in query
            assert query.count('creator IN membersOf(') == 1
        assert 'creator NOT IN' not in query
        assert query.endswith("ORDER BY created DESC")
    assert 'project IN ("IPTV")' in result["China Operator"]["bugCount"]
    assert 'project IN ("SH")' in result["Smart Device"]["bugCount"]
    assert 'project IN ("TV")' in result["TV"]["bugCount"]
    assert 'project IN ("OTT")' in result["Global Operator & STB"]["bugCount"]
    assert all('creator NOT IN' not in result[line.name]["bugCount"] for line in DASHBOARD_PRODUCT_LINES)
    assert ('creator IN membersOf("fae-wifi-qa")' in result["Wireless Connection"]["bugCount"]) == (card_key != "customer-feedback")

    assert 'project IN ("IPTV", "TV", "SH", "OTT")' in result["Wireless Connection"]["bugCount"]
    assert len({metrics["commentCount"] for metrics in result.values()}) == 1


@pytest.mark.parametrize('card_key', ('self-test', 'task', 'customer-feedback'))
def test_activity_metric_sources_keep_their_previous_creator_free_scope(monkeypatch, card_key):
    from smarttest_web.jira.analytics_refresh import metric_source_key
    from core.jira.jql import CARD_BUSINESS_JQL
    from core.jira import jql
    from core.jira.services.filter_service import compose_jql, jira_period_ranges
    windows = jira_period_ranges('year', date(2026, 10, 19))
    actual = cards.activity_card_jql_pair(card_key, 'status = Open', date(2026, 10, 19))
    for period, window in windows.items():
        previous = compose_jql('status = Open', compose_jql(window['condition'], CARD_BUSINESS_JQL[card_key]))
        assert actual[period]['jql'] == previous
        assert 'creator IN' not in previous
    if card_key == 'customer-feedback':
        assert ' OR (project IN ("IPTV", "SH", "TV", "OTT"))' in actual['current']['jql']
        assert 'labels IN (' in actual['current']['jql']
        assert 'resolution NOT IN (' in actual['current']['jql']
        assert 'project NOT IN (' in actual['current']['jql']

    keys = {metric: metric_source_key(card_key, actual['current']['jql'], 'roster', metric) for metric in ('comment', 'verify')}
    scopes = list(jql.JIRA_PRODUCT_LINE_SCOPES)
    group, line, _projects = scopes[0]
    scopes[0] = (group, line, ('TV',))
    monkeypatch.setattr(jql, 'JIRA_PRODUCT_LINE_SCOPES', tuple(scopes))
    after = cards.activity_card_jql_pair(card_key, 'status = Open', date(2026, 10, 19))['current']['jql']
    assert all(metric_source_key(card_key, after, 'roster', metric) == key for metric, key in keys.items())
