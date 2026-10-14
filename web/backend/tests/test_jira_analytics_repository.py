from core.domain.values import NamedValue
from core.jira.domain import Issue, IssueIdentity, JiraProjectRef
from smarttest_web.database import WebDatabase
from smarttest_web.jira.analytics_repository import JiraAnalyticsRepository


def test_failed_refresh_preserves_active_and_superseded_task_cannot_activate(tmp_path):
    repo = JiraAnalyticsRepository(WebDatabase(tmp_path / 'web.db'))
    cached = repo.begin('first', 'alice', 'annual', {}, '', expires_at=100, user_jql='', card_key='self-test')
    repo.write_batch(cached, [issue('A-1')]); repo.activate(cached)
    failed = repo.begin('first', 'alice', 'annual', {}, '', expires_at=100, user_jql='', card_key='self-test')
    repo.finish(failed, 'failed', 'query_failed')
    assert repo.issue_keys('second', 'alice', card_key='self-test') == ['A-1']
    old = repo.begin('first', 'alice', 'annual', {}, '', expires_at=100, card_key='self-test')
    new = repo.begin('second', 'alice', 'annual', {}, '', expires_at=100, card_key='self-test')
    assert not repo.activate(old)
    state = repo.state('second', 'alice', card_key='self-test')
    assert state['activeSnapshotId'] == cached and state['pendingSnapshotId'] == new



def issue(key):
    return Issue(identity=IssueIdentity(key, key, ""), summary=key,
                 project=JiraProjectRef("P", "p", "Project"),
                 status=NamedValue("1", "Open"), issue_type=NamedValue("2", "Bug"))


def test_snapshots_are_isolated_by_session_and_account(tmp_path):
    repo = JiraAnalyticsRepository(WebDatabase(tmp_path / "web.db"), now=lambda: 10)
    first = repo.begin("session-a", "alice", "project = A", {}, "", expires_at=100)
    second = repo.begin("session-b", "bob", "project = B", {}, "", expires_at=100)
    repo.write_batch(first, [issue("A-1")]); repo.activate(first)
    repo.write_batch(second, [issue("B-1")]); repo.activate(second)

    assert repo.state("session-a", "alice")["activeJql"] == "project = A"
    assert repo.issue_keys("session-a", "alice") == ["A-1"]
    assert repo.issue_keys("session-b", "bob") == ["B-1"]
    assert repo.state("session-a", "alice")["userJql"] is None


def test_stale_generation_cannot_replace_active_snapshot(tmp_path):
    repo = JiraAnalyticsRepository(WebDatabase(tmp_path / "web.db"), now=lambda: 10)
    old = repo.begin("s", "alice", "old", {}, "", expires_at=100)
    new = repo.begin("s", "alice", "new", {}, "", expires_at=100)
    repo.write_batch(old, [issue("OLD-1")]); repo.write_batch(new, [issue("NEW-1")])

    assert repo.activate(old) is False
    assert repo.activate(new) is True
    assert repo.issue_keys("s", "alice") == ["NEW-1"]


def test_failed_or_cancelled_pending_query_preserves_active_snapshot(tmp_path):
    repo = JiraAnalyticsRepository(WebDatabase(tmp_path / "web.db"), now=lambda: 10)
    active = repo.begin("s", "alice", "good", {}, "", expires_at=100)
    repo.write_batch(active, [issue("GOOD-1")]); repo.activate(active)

    failed = repo.begin("s", "alice", "bad", {}, "", expires_at=100)
    repo.finish(failed, "failed", "offline")
    cancelled = repo.begin("s", "alice", "cancel", {}, "", expires_at=100)
    repo.finish(cancelled, "cancelled")

    assert repo.state("s", "alice")["activeJql"] == "good"
    assert repo.issue_keys("s", "alice") == ["GOOD-1"]


def test_delete_session_and_expiry_remove_analytics_snapshots(tmp_path):
    clock = [10]
    repo = JiraAnalyticsRepository(WebDatabase(tmp_path / "web.db"), now=lambda: clock[0])
    repo.begin("expired", "alice", "x", {}, "", expires_at=11)
    repo.begin("deleted", "alice", "y", {}, "", expires_at=100)
    clock[0] = 12
    repo.cleanup()
    repo.delete_session("deleted")

    assert repo.state("expired", "alice")["pendingSnapshotId"] == ""
    assert repo.state("deleted", "alice")["pendingSnapshotId"] == ""


def test_effective_jql_and_empty_user_draft_remain_distinct_after_reopening_database(tmp_path):
    database = WebDatabase(tmp_path / "web.db")
    repo = JiraAnalyticsRepository(database, now=lambda: 10)
    snapshot = repo.begin("s", "alice", 'channel = "Self-Test"', {}, "", expires_at=100, user_jql="")
    repo.activate(snapshot)
    restored = JiraAnalyticsRepository(database, now=lambda: 10).state("s", "alice")
    assert restored["activeJql"] == 'channel = "Self-Test"'
    assert restored["userJql"] == ""
    failed = repo.begin("s", "alice", "bad", {}, "", expires_at=100, user_jql="bad")
    repo.finish(failed, "failed", "offline")
    state = repo.state("s", "alice")
    assert state["latestState"] == "failed"
    assert state["error"] == "offline"
    assert state["activeJql"] == 'channel = "Self-Test"'


def test_card_scopes_do_not_replace_each_others_sqlite_results_or_published_conditions(tmp_path):
    repo = JiraAnalyticsRepository(WebDatabase(tmp_path / "web.db"), now=lambda: 10)
    conditions = {"mode": "basic", "basic": {"project": ["A"]}}
    repo.publish_conditions("s", "alice", conditions, expires_at=100)
    first = repo.begin("s", "alice", "scope A", {}, "", expires_at=100, card_key="first")
    second = repo.begin("s", "alice", "scope B", {}, "", expires_at=100, card_key="second")
    repo.write_batch(first, [issue("A-1")]); repo.activate(first)
    repo.write_batch(second, [issue("B-1")]); repo.activate(second)
    assert repo.issue_keys("s", "alice", card_key="first") == ["A-1"]
    assert repo.issue_keys("s", "alice", card_key="second") == ["B-1"]
    assert repo.issue_keys("s", "bob", card_key="first") == []
    published = repo.published_conditions("s", "alice")
    assert {key: value for key, value in published.items() if key != "queryRevision"} == conditions
    assert published["queryRevision"]
    old = repo.begin("s", "alice", "old A", {}, "", expires_at=100, card_key="first")
    new = repo.begin("s", "alice", "new A", {}, "", expires_at=100, card_key="first")
    assert repo.activate(old) is False
    assert repo.state("s", "alice", card_key="first")["pendingSnapshotId"] == new
    assert repo.issue_keys("s", "alice", card_key="second") == ["B-1"]
    repo.delete_session("s")
    assert repo.state("relogin", "alice", card_key="first")["activeSnapshotId"] == first
    assert repo.published_conditions("relogin", "alice") == published


def test_account_card_replay_survives_session_expiry_but_does_not_expose_other_session_task(tmp_path):
    clock = [10]
    repo = JiraAnalyticsRepository(WebDatabase(tmp_path / "web.db"), now=lambda: clock[0])
    snapshot = repo.begin("old-session", "alice", "scope", {}, "", expires_at=11,
                          user_jql="user", card_key="self-test")
    repo.write_batch(snapshot, [issue("A-1")]); repo.activate(snapshot)
    pending = repo.begin("old-session", "alice", "new", {}, "", expires_at=11, card_key="self-test")
    repo.set_task(pending, "secret-task", session_hash="old-session")
    clock[0] = 12; repo.cleanup()
    replay = repo.state("new-session", "alice", card_key="self-test")
    assert replay["activeJql"] == "scope"
    assert replay["pendingSnapshotId"] == pending
    assert replay["taskId"] == ""
    assert repo.state("old-session", "alice", card_key="self-test")["taskId"] == "secret-task"
    assert repo.issue_keys("new-session", "alice", card_key="self-test") == ["A-1"]
    assert repo.issue_keys("new-session", "bob", card_key="self-test") == []


def test_comparison_query_and_memberships_are_persisted_and_reused_atomically(tmp_path):
    repo = JiraAnalyticsRepository(WebDatabase(tmp_path / "web.db"), now=lambda: 10)
    snapshot = repo.begin("s", "alice", "current", {}, "", expires_at=100, user_jql="user",
                          card_key="self-test", roster_fingerprint="definition", comparison_jql="previous")
    repo.write_batch(snapshot, [issue("CUR-1")], period_kind="current")
    repo.write_batch(snapshot, [issue("PREV-1")], period_kind="previous")
    repo.activate(snapshot, {"current": {"teamTotal": 1}, "previous": {"teamTotal": 1}})

    state = repo.state("new", "alice", card_key="self-test")
    assert state["activeComparisonJql"] == "previous"
    assert repo.issue_keys("new", "alice", card_key="self-test", period_kind="current") == ["CUR-1"]
    assert repo.issue_keys("new", "alice", card_key="self-test", period_kind="previous") == ["PREV-1"]


def test_partial_statistics_persist_without_replacing_valid_result_and_reject_stale_writes(tmp_path):
    repo = JiraAnalyticsRepository(WebDatabase(tmp_path / "web.db"))
    old = repo.begin("s", "alice", "scope", {}, "", expires_at=100, card_key="task")
    repo.activate(old, {"value": "valid"})
    partial = repo.begin("s", "alice", "scope", {}, "", expires_at=100, card_key="task")
    assert repo.update_statistics(partial, {"value": "basic"})
    reopened = JiraAnalyticsRepository(WebDatabase(tmp_path / "web.db"))
    assert reopened.statistics_preview("s", "alice", card_key="task")["statistics"] == {"value": "basic"}
    assert reopened.state("s", "alice", card_key="task")["activeSnapshotId"] == old
    replacement = repo.begin("s", "alice", "scope", {}, "", expires_at=100, card_key="task")
    assert not repo.update_statistics(partial, {"value": "stale"}, facts=[{"issueId": "stale"}])
    with repo.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM jira_analytics_facts WHERE snapshot_id=?", (partial,)).fetchone()[0] == 0
    assert repo.update_statistics(replacement, {"value": "new"})
    repo.finish(partial, "cancelled")
    assert repo.statistics_preview("s", "alice", card_key="task")["statistics"] == {"value": "new"}
    repo.finish(replacement, "failed")
    assert repo.state("s", "alice", card_key="task")["activeSnapshotId"] == old
