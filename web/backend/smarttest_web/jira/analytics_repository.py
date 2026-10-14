from __future__ import annotations

import json
import time
from uuid import uuid4

from core.logging import smart_log

from .issue_repository import JiraIssueRepository


class JiraAnalyticsRepository:
    def __init__(self, database, *, now=time.time):
        self.database, self._now = database, now
        self.issues = JiraIssueRepository(database)

    @staticmethod
    def _scope(session_hash, card_key, account):
        return f"account:{str(account).casefold()}:card:{card_key}" if card_key else str(session_hash)

    def publish_conditions(self, session_hash, account, conditions, *, expires_at):
        conditions = {**conditions, "queryRevision": uuid4().hex}
        session_hash = self._scope(session_hash, "conditions", account)
        with self.database.transaction() as connection:
            connection.execute("""INSERT INTO jira_analytics_queries
                (session_hash,account,expires_at,user_conditions_json) VALUES(?,?,?,?)
                ON CONFLICT(session_hash) DO UPDATE SET account=excluded.account,
                expires_at=excluded.expires_at,user_conditions_json=excluded.user_conditions_json""",
                (str(session_hash), str(account).casefold(), float(expires_at),
                 json.dumps(conditions, ensure_ascii=False, sort_keys=True)))

    def published_conditions(self, session_hash, account):
        session_hash = self._scope(session_hash, "conditions", account)
        with self.database.connect() as connection:
            row = connection.execute("""SELECT user_conditions_json FROM jira_analytics_queries
                WHERE session_hash=? AND account=?""",
                (str(session_hash), str(account).casefold())).fetchone()
        return json.loads(row[0]) if row and row[0] is not None else None

    def begin(self, session_hash, account, jql, basic, source_filter_id, *, expires_at, user_jql=None, card_key="", roster_fingerprint="", comparison_jql="", query_revision="", published_revision=""):
        snapshot_id = uuid4().hex
        now = self._now()
        task_session_hash = str(session_hash)
        session_hash = self._scope(session_hash, card_key, account)
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM jira_analytics_queries WHERE expires_at<=? AND card_key='' AND user_conditions_json IS NULL", (now,))
            connection.execute("""INSERT INTO jira_analytics_queries
                (session_hash,account,pending_snapshot_id,expires_at,card_key,task_session_hash,last_snapshot_id) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(session_hash) DO UPDATE SET account=excluded.account,
                pending_snapshot_id=excluded.pending_snapshot_id,task_id='',expires_at=excluded.expires_at,
                task_session_hash=excluded.task_session_hash,last_snapshot_id=excluded.last_snapshot_id""",
                (str(session_hash), str(account).casefold(), snapshot_id, float(expires_at), str(card_key), task_session_hash, snapshot_id))
            connection.execute("""INSERT INTO jira_analytics_snapshots
                (snapshot_id,session_hash,account,jql,basic_json,source_filter_id,state,created_at,user_jql,card_key,roster_fingerprint,comparison_jql,statistics_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (snapshot_id, str(session_hash), str(account).casefold(),
                str(jql), json.dumps(basic or {}, ensure_ascii=False, sort_keys=True),
                str(source_filter_id or ""), "pending", now, user_jql, str(card_key), str(roster_fingerprint), str(comparison_jql),
                json.dumps({"queryRevision": query_revision, "publishedRevision": published_revision}) if query_revision else None))
        return snapshot_id

    def set_task(self, snapshot_id, task_id, *, session_hash=None):
        with self.database.transaction() as connection:
            connection.execute("""UPDATE jira_analytics_queries SET task_id=?,task_session_hash=COALESCE(?,task_session_hash)
                WHERE pending_snapshot_id=?""", (str(task_id), session_hash, str(snapshot_id)))

    def write_batch(self, snapshot_id, issues, period_kind="current"):
        rows = list(issues)
        self.issues.save_core(rows)
        with self.database.transaction() as connection:
            offset = int(connection.execute(
                "SELECT count(*) FROM jira_analytics_snapshot_issues WHERE snapshot_id=? AND period_kind=?", (snapshot_id, period_kind)
            ).fetchone()[0])
            for index, issue in enumerate(rows, offset):
                connection.execute("""INSERT OR IGNORE INTO jira_analytics_snapshot_issues
                    (snapshot_id,issue_id,ordinal,period_kind) VALUES(?,?,?,?)""",
                    (snapshot_id, issue.identity.id, index, str(period_kind)))

    def update_statistics(self, snapshot_id, statistics, *, facts=None):
        with self.database.transaction() as connection:
            cursor = connection.execute("""UPDATE jira_analytics_snapshots SET statistics_json=?
                WHERE snapshot_id=? AND state='pending' AND snapshot_id IN
                (SELECT pending_snapshot_id FROM jira_analytics_queries)""",
                (json.dumps(statistics, ensure_ascii=False, sort_keys=True), str(snapshot_id)))
            if cursor.rowcount and facts is not None:
                connection.executemany("""INSERT INTO jira_analytics_facts(snapshot_id,issue_id,fact_json) VALUES(?,?,?)
                    ON CONFLICT(snapshot_id,issue_id) DO UPDATE SET fact_json=excluded.fact_json""",
                    [(snapshot_id, fact["issueId"], json.dumps(fact, ensure_ascii=False)) for fact in facts])
            return bool(cursor.rowcount)

    def local_statistics(self, snapshot_id, period):
        started = time.monotonic()
        outcome = "failed"
        stage = "sqlite_read"
        smart_log("Jira local statistics", platform="web", domain="jira", extra={
            "snapshot_id": snapshot_id, "period": period, "stage": stage, "outcome": "started"})
        try:
            from core.jira.services.jql_statistics_cards import aggregate_annual_facts, merge_annual_activity_facts
            with self.database.connect() as connection:
                if not connection.in_transaction:
                    connection.execute("BEGIN")
                row = connection.execute("SELECT statistics_json,account FROM jira_analytics_snapshots WHERE snapshot_id=?",
                                         (snapshot_id,)).fetchone()
                metadata = json.loads(row[0]) if row and row[0] else {}
                if not metadata.get("annual"):
                    outcome = "not_annual"
                    return None
                facts = [json.loads(item[0]) for item in connection.execute(
                    "SELECT fact_json FROM jira_analytics_facts WHERE snapshot_id=? ORDER BY rowid", (snapshot_id,))]
                for source_period, source in metadata.get("activitySources", {}).items():
                    if source.get("snapshotId"):
                        source_row = connection.execute("SELECT snapshot_id,statistics_json FROM jira_analytics_snapshots WHERE snapshot_id=? AND account=?",
                                                        (source["snapshotId"], row[1])).fetchone()
                    else:
                        source_row = connection.execute("""SELECT s.snapshot_id,s.statistics_json FROM jira_analytics_queries q
                            JOIN jira_analytics_snapshots s ON s.snapshot_id=q.active_snapshot_id
                            WHERE q.session_hash=?""", (self._scope("", source["scope"], row[1]),)).fetchone()
                    if source_row and json.loads(source_row[1]).get("queryRevision") == source["revision"]:
                        merge_annual_activity_facts(facts, [json.loads(item[0]) for item in connection.execute(
                            "SELECT fact_json FROM jira_analytics_facts WHERE snapshot_id=? ORDER BY rowid", (source_row[0],))])
                        metadata["availability"][source_period]["activity"] = True
            smart_log("Jira local statistics", platform="web", domain="jira", extra={
                "snapshot_id": snapshot_id, "period": period, "stage": stage, "outcome": "completed",
                "processed": len(facts), "duration_ms": round((time.monotonic() - started) * 1000, 3)})
            stage = "aggregate"
            started = time.monotonic()
            result = aggregate_annual_facts(facts, metadata, period)
            outcome = "completed"
            return result
        except Exception as error:
            smart_log("Jira local statistics failed", platform="web", domain="jira", level="ERROR",
                extra={"snapshot_id": snapshot_id, "period": period, "stage": stage,
                       "cause_type": type(error).__name__})
            raise
        finally:
            smart_log("Jira local statistics", platform="web", domain="jira", extra={
                "snapshot_id": snapshot_id, "period": period, "stage": stage, "outcome": outcome,
                "duration_ms": round((time.monotonic() - started) * 1000, 3)})

    def annual_source(self, account, source_key, revision):
        state = self.state("", account, card_key=source_key)
        snapshot_id = state["activeSnapshotId"]
        if not snapshot_id:
            return ""
        with self.database.connect() as connection:
            row = connection.execute("SELECT statistics_json FROM jira_analytics_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        metadata = json.loads(row[0]) if row and row[0] else {}
        return snapshot_id if metadata.get("queryRevision") == revision else ""

    def statistics_preview(self, session_hash, account, *, card_key):
        with self.database.connect() as connection:
            row = connection.execute("""SELECT s.jql,s.comparison_jql,s.user_jql,s.roster_fingerprint,s.statistics_json,s.snapshot_id
                FROM jira_analytics_queries q JOIN jira_analytics_snapshots s
                ON s.snapshot_id=COALESCE(NULLIF(q.pending_snapshot_id,''),q.last_snapshot_id)
                WHERE q.session_hash=? AND q.account=? AND s.state IN ('pending','failed','cancelled','active')
                """,
                (self._scope(session_hash, card_key, account), str(account).casefold())).fetchone()
        return {"jql": row[0], "comparisonJql": row[1], "userJql": row[2],
                "fingerprint": row[3], "statistics": json.loads(row[4]) if row[4] else None, "snapshotId": row[5]} if row else None

    def activate(self, snapshot_id, statistics=None):
        with self.database.transaction() as connection:
            cursor = connection.execute("""UPDATE jira_analytics_queries
                SET active_snapshot_id=?,pending_snapshot_id='',task_id=''
                WHERE pending_snapshot_id=?""", (snapshot_id, snapshot_id))
            if not cursor.rowcount:
                connection.execute("UPDATE jira_analytics_snapshots SET state='stale' WHERE snapshot_id=?", (snapshot_id,))
                return False
            connection.execute("""UPDATE jira_analytics_snapshots SET state='active',statistics_json=?
                WHERE snapshot_id=?""", (
                None if statistics is None else json.dumps(statistics, ensure_ascii=False, sort_keys=True),
                snapshot_id,
            ))
            return True

    def finish(self, snapshot_id, state, error=""):
        with self.database.transaction() as connection:
            connection.execute("UPDATE jira_analytics_snapshots SET state=?,error=? WHERE snapshot_id=?",
                               (str(state), str(error), str(snapshot_id)))
            connection.execute("""UPDATE jira_analytics_queries SET pending_snapshot_id='',task_id=''
                WHERE pending_snapshot_id=?""", (str(snapshot_id),))

    def state(self, session_hash, account, *, card_key=""):
        task_session_hash = str(session_hash)
        session_hash = self._scope(session_hash, card_key, account)
        with self.database.connect() as connection:
            row = connection.execute("""SELECT active_snapshot_id,pending_snapshot_id,task_id,task_session_hash,last_snapshot_id
                FROM jira_analytics_queries WHERE session_hash=? AND account=? AND (card_key!='' OR expires_at>?)""",
                (str(session_hash), str(account).casefold(), self._now())).fetchone()
            if row is None:
                return {"activeSnapshotId": "", "pendingSnapshotId": "", "taskId": "", "activeJql": "", "activeComparisonJql": "",
                        "userJql": None, "latestState": "", "error": "", "rosterFingerprint": ""}
            active_jql = active_comparison_jql = ""
            user_jql = None
            fingerprint = ""
            if row[0]:
                value = connection.execute("SELECT jql,user_jql,roster_fingerprint,comparison_jql FROM jira_analytics_snapshots WHERE snapshot_id=?", (row[0],)).fetchone()
                active_jql = value[0] if value else ""
                user_jql = value[1] if value else None
                fingerprint = value[2] if value else ""
                active_comparison_jql = value[3] if value else ""
            latest = connection.execute("""SELECT state,error FROM jira_analytics_snapshots
                WHERE session_hash=? AND account=? AND (?='' OR snapshot_id=?)
                ORDER BY created_at DESC,rowid DESC LIMIT 1""",
                (str(session_hash), str(account).casefold(), row[4], row[4])).fetchone()
            return {"activeSnapshotId": row[0], "pendingSnapshotId": row[1],
                    "taskId": row[2] if not card_key or row[3] == task_session_hash else "", "activeJql": active_jql,
                    "activeComparisonJql": active_comparison_jql,
                    "userJql": user_jql, "latestState": latest[0] if latest else "", "error": latest[1] if latest else "",
                    "rosterFingerprint": fingerprint}

    def issue_keys(self, session_hash, account, *, card_key="", period_kind="current"):
        session_hash = self._scope(session_hash, card_key, account)
        with self.database.connect() as connection:
            return [row[0] for row in connection.execute("""SELECT i.issue_key
                FROM jira_analytics_queries q
                JOIN jira_analytics_snapshot_issues m ON m.snapshot_id=q.active_snapshot_id
                JOIN jira_issues i ON i.issue_id=m.issue_id
                WHERE q.session_hash=? AND q.account=? AND (q.card_key!='' OR q.expires_at>?) AND m.period_kind=?
                ORDER BY m.ordinal""", (str(session_hash), str(account).casefold(), self._now(), period_kind))]

    def delete_session(self, session_hash):
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM jira_analytics_queries WHERE session_hash=? OR session_hash LIKE ?",
                               (str(session_hash), f"{session_hash}:card:%"))

    def interrupt_pending(self):
        """Called before serving requests: runtime tasks do not survive restart."""
        with self.database.transaction() as connection:
            connection.execute("""UPDATE jira_analytics_snapshots SET state='failed',error='query_interrupted'
                WHERE snapshot_id IN (SELECT pending_snapshot_id FROM jira_analytics_queries
                WHERE pending_snapshot_id!='')""")
            connection.execute("""UPDATE jira_analytics_queries SET pending_snapshot_id='',task_id=''
                WHERE pending_snapshot_id!=''""")

    def delete_account(self, account):
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM jira_analytics_queries WHERE account=?", (str(account).casefold(),))

    def cleanup(self):
        with self.database.transaction() as connection:
            return connection.execute("DELETE FROM jira_analytics_queries WHERE expires_at<=? AND card_key='' AND user_conditions_json IS NULL", (self._now(),)).rowcount
