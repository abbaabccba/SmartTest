from datetime import date

import pytest

from core.jira.services import jql_statistics_cards as cards
from core.product_lines import DASHBOARD_PRODUCT_LINES


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
    for metrics in result.values():
        assert tuple(metrics) == ("bugCount", "commentCount", "verifyCount", "invalidCount")
        assert metrics["commentCount"] == metrics["bugCount"]
        assert metrics["verifyCount"] == metrics["bugCount"]
        assert metrics["invalidCount"] != metrics["bugCount"]
        assert 'resolution = "Invalid"' in metrics["invalidCount"]
        query = metrics["bugCount"]
        assert query.startswith("(status = Open OR priority = High) AND (")
        assert cards.card_definition(card_key).custom_jql in query
        assert f'created >= "{start}" AND created <= now()' in query
        assert all(f'creator IN membersOf("{group}")' in query for group in cards.FAE_QA_GROUPS)
        assert query.endswith("ORDER BY created DESC")
    assert 'project IN ("IPTV")' in result["China Operator"]["bugCount"]
    assert 'project IN ("SH")' in result["Smart Device"]["bugCount"]
    assert 'project IN ("TV")' in result["TV"]["bugCount"]
    assert 'project IN ("OTT")' in result["Global Operator & STB"]["bugCount"]
    assert all('creator NOT IN membersOf("fae-wifi-qa")' in result[line.name]["bugCount"]
               for line in DASHBOARD_PRODUCT_LINES[:-1])
    assert 'creator IN membersOf("fae-wifi-qa")' in result["Wireless Connection"]["bugCount"]
