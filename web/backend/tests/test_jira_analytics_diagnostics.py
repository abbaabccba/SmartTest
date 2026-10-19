from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError
from starlette.requests import Request

from smarttest_web import jira_api


def test_request_failure_preserves_database_exception_and_redacts_details(monkeypatch):
    records = []
    monkeypatch.setattr(jira_api, "smart_log", lambda message, **fields: records.append((message, fields)))
    error = OperationalError("private SQL", {"password": "secret"}, RuntimeError("private connection"))
    request = Request({"type": "http"})
    request.state.jira_stage = "read_conditions"

    class Repository:
        def published_conditions(self, *_args):
            raise error

    monkeypatch.setattr(jira_api, "request_session_id", lambda _request: "session")
    router = jira_api.create_router(lambda: None, None, None, None, Repository(), None, None, None, None, None)
    query = next(route.endpoint for route in router.routes if route.path == "/api/jira/cards/{card_key}/query")
    with pytest.raises(OperationalError) as raised:
        query("customer-feedback", request, SimpleNamespace(username="account"))
    assert raised.value is error
    assert len(records) == 2
    fields = records[-1][1]["extra"]
    assert fields["card"] == "customer-feedback"
    assert fields["stage"] == "read_conditions"
    assert fields["failureKind"] == "database"
    assert "stage=read_conditions" in records[-1][0]
    assert "private" not in str(records) and "secret" not in str(records)


def test_conditions_publish_does_not_claim_remote_task_and_task_is_correlated(monkeypatch):
    records = []
    monkeypatch.setattr(jira_api, "smart_log", lambda message, **fields: records.append(fields["extra"]))
    result = {"applied": True, "validation": {"valid": True}, "userJql": "private query"}

    @jira_api._trace_analytics("publish_conditions")
    def publish():
        return result

    assert publish() is result
    assert records[-1]["remoteTaskSubmitted"] is False

    @jira_api._trace_analytics("query_card")
    def query(card_key):
        return {"taskId": "task-1", "snapshotId": "snapshot-1", "validation": {"valid": True}}

    query("customer-feedback")
    assert records[-1]["remoteTaskSubmitted"] is True
    assert records[-1]["taskId"] == "task-1"
    assert records[-1]["snapshotId"] == "snapshot-1"
    assert "private" not in str(records)


def test_successful_statistics_poll_has_no_repeated_record(monkeypatch):
    records = []
    monkeypatch.setattr(jira_api, "smart_log", lambda *args, **kwargs: records.append(args))

    @jira_api._trace_analytics("statistics")
    def statistics():
        return {"state": "loading"}

    statistics()
    statistics()
    assert records == []


def test_translated_http_failure_retains_database_classification(monkeypatch):
    records = []
    monkeypatch.setattr(jira_api, "smart_log", lambda message, **fields: records.append(fields["extra"]))
    database_error = OperationalError("private SQL", {}, RuntimeError("private"))

    def filter_owner(*_args):
        raise database_error

    repository = SimpleNamespace(published_conditions=lambda *_args: {})
    monkeypatch.setattr(jira_api, "request_session_id", lambda _request: "session")
    router = jira_api.create_router(lambda: None, None, None, None, repository, filter_owner, None, None, None, None)
    query = next(route.endpoint for route in router.routes if route.path == "/api/jira/cards/{card_key}/query")
    with pytest.raises(jira_api.HTTPException) as raised:
        query("customer-feedback", Request({"type": "http"}), SimpleNamespace(username="account", password="secret"))
    assert raised.value.__cause__ is database_error
    assert records[-1]["failureKind"] == "database"
    assert "private" not in str(records)


@pytest.mark.parametrize("active_matches", [True, False])
def test_failed_refresh_replays_only_matching_active_database_result(monkeypatch, active_matches):
    from core.jira.services.jql_statistics_cards import card_definition, effective_card_jql_pair
    pair = effective_card_jql_pair("customer-feedback", "", "year")
    fingerprint = card_definition("customer-feedback").fingerprint + ":annual-metrics-v5"
    query = {"activeSnapshotId": "active", "pendingSnapshotId": "", "taskId": "",
             "activeJql": pair["current"]["jql"] if active_matches else "old scope",
             "activeComparisonJql": pair["previous"]["jql"], "userJql": "",
             "rosterFingerprint": fingerprint, "latestState": "failed", "error": "jira_search_failed"}
    reads = []
    def statistics(snapshot_id, period):
        reads.append(snapshot_id)
        return {"productLines": [], "saved": True}
    repository = SimpleNamespace(
        state=lambda *_args, **_kwargs: query,
        published_conditions=lambda *_args: {"userJql": ""},
        statistics_preview=lambda *_args, **_kwargs: {"snapshotId": "failed-partial",
            "jql": pair["current"]["jql"], "comparisonJql": pair["previous"]["jql"],
            "userJql": "", "fingerprint": fingerprint, "statistics": {"partial": True}},
        local_statistics=statistics)
    def remote(*_args):
        pytest.fail("GET must not resolve remote owner")
    monkeypatch.setattr(jira_api, "request_session_id", lambda _request: "session")
    router = jira_api.create_router(lambda: None, None, None, None, repository, remote, None, None, None, None)
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/jira/cards/{card_key}/statistics")
    result = endpoint("customer-feedback", Request({"type": "http", "method": "GET"}), SimpleNamespace(username="account"))
    assert result["state"] == "failed"
    assert result["error"] == "jira_search_failed"
    assert result["dataState"] == ("ready" if active_matches else "none")
    assert bool(result.get("periods")) is active_matches
    assert "failed-partial" not in reads
    if active_matches:
        assert reads == ["active"] * 4
