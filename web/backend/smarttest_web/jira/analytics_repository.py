from __future__ import annotations

from sqlalchemy import select, or_, text, update, delete, func
from ..database import upsert

from .schema import jira_analytics_qa_events, jira_analytics_facts, jira_analytics_queries, jira_analytics_snapshot_issues, jira_analytics_snapshots, jira_history_scopes, jira_issues

import json
import time
from datetime import date, datetime, timezone
from hashlib import sha256

from ..cache import CacheIdentity
from uuid import uuid4

from core.logging import smart_log

from .issue_repository import JiraIssueRepository


class JiraAnalyticsRepository:
    def __init__(self, database, *, now=time.time, statistics_cache=None, statistics_cache_ttl_seconds=None, today=date.today):
        self.database, self._now = database, now
        self.issues = JiraIssueRepository(database)
        self._statistics_cache = statistics_cache
        self._statistics_cache_ttl_seconds = statistics_cache_ttl_seconds
        self._today = today
        if statistics_cache is not None and (type(statistics_cache_ttl_seconds) is not int or statistics_cache_ttl_seconds <= 0):
            raise ValueError('An explicitly injected statistics cache requires a positive TTL')

    def register_history_scope(self, account, card_key, user_jql):
        account = str(account).casefold()
        with self.database.transaction() as connection:
            upsert(
                connection,
                jira_history_scopes,
                {"account": account, "card_key": card_key, "user_jql": user_jql},
                conflict_columns=("account", "card_key"),
                updates=("user_jql",),
            )

    def history_scopes(self, account):
        with self.database.connect() as connection:
            return dict(
                connection.execute(
                    text(
                        "SELECT card_key,user_jql FROM jira_history_scopes WHERE account=:account ORDER BY card_key"
                    ),
                    {"account": str(account).casefold()},
                ).fetchall()
            )

    @staticmethod
    def _scope(session_hash, card_key, account):
        return f"account:{str(account).casefold()}:card:{card_key}" if card_key else str(session_hash)

    def publish_conditions(self, session_hash, account, conditions, *, expires_at):
        conditions = {**conditions, "queryRevision": uuid4().hex}
        session_hash = self._scope(session_hash, "conditions", account)
        with self.database.transaction() as connection:
            upsert(
                connection,
                jira_analytics_queries,
                {
                    "session_hash": str(session_hash),
                    "account": str(account).casefold(),
                    "expires_at": float(expires_at),
                    "user_conditions_json": json.dumps(conditions, ensure_ascii=False, sort_keys=True),
                },
                conflict_columns=("session_hash",),
                updates=("account", "expires_at", "user_conditions_json"),
            )

    def published_conditions(self, session_hash, account):
        session_hash = self._scope(session_hash, "conditions", account)
        with self.database.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT user_conditions_json FROM jira_analytics_queries\n                WHERE session_hash=:session_hash AND account=:account"
                ),
                {"session_hash": str(session_hash), "account": str(account).casefold()},
            ).fetchone()
        return json.loads(row[0]) if row and row[0] is not None else None

    def begin(self, session_hash, account, jql, basic, source_filter_id, *, expires_at, user_jql=None, card_key="", roster_fingerprint="", comparison_jql="", query_revision="", published_revision="", expected_user_jql=None):
        snapshot_id = uuid4().hex
        now = self._now()
        task_session_hash = str(session_hash)
        session_hash = self._scope(session_hash, card_key, account)
        with self.database.transaction() as connection:
            if expected_user_jql is not None:
                submitted = connection.execute(
                    text("SELECT user_jql FROM jira_history_scopes WHERE account=:account AND card_key=:card_key"),
                    {"account": str(account).casefold(), "card_key": card_key},
                ).fetchone()
                if not submitted or submitted[0] != expected_user_jql:
                    from core.async_tasks import TaskCancelled
                    raise TaskCancelled('scope_changed')
                existing = connection.execute(
                    text(
                        "SELECT q.pending_snapshot_id,s.basic_json,s.source_filter_id\n                    FROM jira_analytics_queries q LEFT JOIN jira_analytics_snapshots s\n                    ON s.snapshot_id=q.active_snapshot_id WHERE q.session_hash=:session_hash"
                    ),
                    {"session_hash": session_hash},
                ).fetchone()
                if existing:
                    if existing[0]:
                        from core.async_tasks import TaskCancelled
                        raise TaskCancelled('query_in_progress')
                    basic = json.loads(existing[1] or '{}')
                    source_filter_id = existing[2] or ''
            self._delete_expired(connection, now)
            upsert(
                connection,
                jira_analytics_queries,
                {
                    "session_hash": str(session_hash),
                    "account": str(account).casefold(),
                    "pending_snapshot_id": snapshot_id,
                    "expires_at": float(expires_at),
                    "card_key": str(card_key),
                    "task_session_hash": task_session_hash,
                    "last_snapshot_id": snapshot_id,
                },
                conflict_columns=("session_hash",),
                updates=lambda incoming: {
                    "account": incoming.account,
                    "pending_snapshot_id": incoming.pending_snapshot_id,
                    "task_id": "",
                    "expires_at": incoming.expires_at,
                    "task_session_hash": incoming.task_session_hash,
                    "last_snapshot_id": incoming.last_snapshot_id,
                },
            )
            connection.execute(
                jira_analytics_snapshots.insert().values(
                    {
                        "snapshot_id": snapshot_id,
                        "session_hash": str(session_hash),
                        "account": str(account).casefold(),
                        "jql": str(jql),
                        "basic_json": json.dumps(
                            basic or {}, ensure_ascii=False, sort_keys=True
                        ),
                        "source_filter_id": str(source_filter_id or ""),
                        "state": "pending",
                        "created_at": now,
                        "user_jql": user_jql,
                        "card_key": str(card_key),
                        "roster_fingerprint": str(roster_fingerprint),
                        "comparison_jql": str(comparison_jql),
                        "statistics_json": (
                            json.dumps(
                                {
                                    "queryRevision": query_revision,
                                    "publishedRevision": published_revision,
                                }
                            )
                            if query_revision
                            else None
                        ),
                    }
                )
            )
        return snapshot_id

    @staticmethod
    def _lock_query(connection, snapshot_id):
        scope = connection.execute(
            select(jira_analytics_snapshots.c.session_hash).where(
                jira_analytics_snapshots.c.snapshot_id == str(snapshot_id)
            )
        ).scalar_one_or_none()
        if scope is not None:
            connection.execute(
                select(jira_analytics_queries.c.session_hash)
                .where(jira_analytics_queries.c.session_hash == scope)
                .with_for_update()
            ).first()
        return scope

    @staticmethod
    def _delete_expired(connection, now):
        table = jira_analytics_queries
        predicate = (
            table.c.expires_at <= now,
            table.c.card_key == "",
            table.c.user_conditions_json.is_(None),
        )
        scopes = (
            connection.execute(
                select(table.c.session_hash)
                .where(*predicate)
                .order_by(table.c.session_hash)
            )
            .scalars()
            .all()
        )
        deleted = 0
        for scope in scopes:
            deleted += connection.execute(
                delete(table).where(table.c.session_hash == scope, *predicate)
            ).rowcount
        return deleted

    def set_task(self, snapshot_id, task_id, *, session_hash=None):
        with self.database.transaction() as connection:
            scope = self._lock_query(connection, snapshot_id)
            connection.execute(
                update(jira_analytics_queries)
                .where(
                    jira_analytics_queries.c.session_hash == scope,
                    jira_analytics_queries.c.pending_snapshot_id == str(snapshot_id),
                )
                .values(
                    task_id=str(task_id),
                    task_session_hash=func.coalesce(
                        session_hash, jira_analytics_queries.c.task_session_hash
                    ),
                )
            )

    def write_batch(self, snapshot_id, issues, period_kind="current"):
        rows = list(issues)
        self.issues.save_core(rows)
        with self.database.transaction() as connection:
            offset = int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM jira_analytics_snapshot_issues WHERE snapshot_id=:snapshot_id AND period_kind=:period_kind"
                    ),
                    {"snapshot_id": snapshot_id, "period_kind": period_kind},
                ).fetchone()[0]
            )
            if rows:
                upsert(
                    connection,
                    jira_analytics_snapshot_issues,
                    [
                        {
                            "snapshot_id": snapshot_id,
                            "issue_id": issue.identity.id,
                            "ordinal": index,
                            "period_kind": str(period_kind),
                        }
                        for index, issue in enumerate(rows, offset)
                    ],
                    conflict_columns=("snapshot_id", "issue_id", "period_kind"),
                    updates=(),
                )

    def update_statistics(self, snapshot_id, statistics, *, facts=None, qa_events=None):
        with self.database.transaction() as connection:
            scope = self._lock_query(connection, snapshot_id)
            pending = connection.execute(
                select(jira_analytics_queries.c.pending_snapshot_id)
                .where(jira_analytics_queries.c.session_hash == scope)
                .with_for_update()
            ).scalar_one_or_none()
            if pending != str(snapshot_id):
                return False
            cursor = connection.execute(
                update(jira_analytics_snapshots)
                .where(
                    jira_analytics_snapshots.c.snapshot_id == str(snapshot_id),
                    jira_analytics_snapshots.c.state == "pending",
                )
                .values(
                    statistics_json=json.dumps(
                        statistics, ensure_ascii=False, sort_keys=True
                    )
                )
            )
            if cursor.rowcount and facts is not None:
                fact_rows = [
                    {
                        "snapshot_id": snapshot_id,
                        "issue_id": fact["issueId"],
                        "fact_json": json.dumps(fact, ensure_ascii=False),
                    }
                    for fact in facts
                ]
                for start in range(0, len(fact_rows), 500):
                    upsert(
                        connection,
                        jira_analytics_facts,
                        fact_rows[start : start + 500],
                        conflict_columns=("snapshot_id", "issue_id"),
                        updates=("fact_json",),
                    )
            if cursor.rowcount and qa_events is not None:
                event_rows = [
                    {"snapshot_id": snapshot_id,
                     **{key: event[key] for key in ("issue_id", "event_kind", "source_id",
                        "author_identity", "author_account", "author_display_name", "created_at", "updated_at")},
                     "payload_json": json.dumps(event["payload"], ensure_ascii=False)}
                    for event in qa_events
                ]
                for start in range(0, len(event_rows), 500):
                    upsert(connection, jira_analytics_qa_events, event_rows[start:start + 500],
                           conflict_columns=("snapshot_id", "issue_id", "event_kind", "source_id"),
                           updates=("author_identity", "author_account", "author_display_name",
                                    "created_at", "updated_at", "payload_json"))
            return bool(cursor.rowcount)

    def local_statistics(self, snapshot_id, period):
        started = time.monotonic()
        outcome = "failed"
        fields = {
            "snapshot_id": snapshot_id,
            "period": period,
            "stage": "database_read",
        }
        smart_log(
            "Jira local statistics",
            platform="web",
            domain="jira",
            extra={**fields, "outcome": "started"},
        )
        try:
            result = self._local_statistics(snapshot_id, period)
            outcome = 'completed' if result is not None else 'not_annual'
            return result
        except Exception as error:
            smart_log('Jira local statistics failed', platform='web', domain='jira', level='ERROR',
                extra={**fields, 'cause_type': type(error).__name__})
            raise
        finally:
            smart_log('Jira local statistics', platform='web', domain='jira', extra={**fields,
                'outcome': outcome, 'duration_ms': round((time.monotonic() - started) * 1000, 3)})

    def _local_statistics(self, snapshot_id, period):
        from core.jira.services.jql_statistics_cards import (
            aggregate_annual_facts, merge_annual_activity_facts, load_activity_qa_roster,
        )
        today = self._today()
        with self.database.connect() as connection:
            if not connection.in_transaction():
                connection.begin()
            row = connection.execute(select(
                jira_analytics_snapshots.c.statistics_json, jira_analytics_snapshots.c.account,
                jira_analytics_snapshots.c.card_key, jira_analytics_snapshots.c.roster_fingerprint,
                jira_analytics_snapshots.c.state,
            ).where(jira_analytics_snapshots.c.snapshot_id == snapshot_id)).fetchone()
            metadata = json.loads(row[0]) if row and row[0] else {}
            if not metadata.get('annual'):
                return None
            current = connection.execute(select(jira_analytics_queries.c.active_snapshot_id).where(
                jira_analytics_queries.c.active_snapshot_id == snapshot_id,
                jira_analytics_queries.c.account == row[1],
            )).first() is not None and row[4] == 'active'
            roster = load_activity_qa_roster().fingerprint if metadata.get('metricSources') else ''
            sources = []
            immutable_sources = True
            metric_sources = self._metric_sources(connection, row[1], metadata, roster)
            for source in metric_sources:
                metadata['availability'][source['year']][source['metric']] = bool(source['snapshotId'])
                sources.append((source['snapshotId'], (source['metric'] + 'Count',)))
            evidence_refs = self._qa_references(snapshot_id, period, today, metric_sources)
            for source_period, source in metadata.get('activitySources', {}).items():
                if source.get('snapshotId'):
                    source_row = connection.execute(select(jira_analytics_snapshots.c.snapshot_id,
                        jira_analytics_snapshots.c.statistics_json, jira_analytics_snapshots.c.state).where(
                        jira_analytics_snapshots.c.snapshot_id == source['snapshotId'],
                        jira_analytics_snapshots.c.account == row[1])).first()
                else:
                    source_row = connection.execute(text(
                        "SELECT s.snapshot_id,s.statistics_json,s.state FROM jira_analytics_queries q "
                        "JOIN jira_analytics_snapshots s ON s.snapshot_id=q.active_snapshot_id WHERE q.session_hash=:scope"
                    ), {'scope': self._scope('', source['scope'], row[1])}).first()
                valid = source_row and json.loads(source_row[1]).get('queryRevision') == source['revision']
                if valid and source_row[2] != 'active':
                    immutable_sources = False
                if valid:
                    metadata['availability'][source_period]['activity'] = True
                sources.append((source_row[0] if valid else '', None))

            def read_raw_facts(connection):
                # Resolve identities in the metadata transaction; active facts are immutable.
                return [[item[0] for item in connection.execute(
                    select(jira_analytics_facts.c.fact_json)
                    .where(jira_analytics_facts.c.snapshot_id == identity)
                    .order_by(jira_analytics_facts.c.sequence))] if identity else []
                    for identity in [snapshot_id, *(identity for identity, _metrics in sources)]]

            complete = all(all(values.values()) for values in metadata.get('availability', {}).values())
            cacheable = (self._statistics_cache is not None and current and complete and immutable_sources)
            if not cacheable:
                # Pending/mutable metadata and facts must come from the same consistent read.
                raw_facts = read_raw_facts(connection)

        def aggregate(raw):
            facts = [json.loads(value) for value in raw[0]]
            for (_identity, metrics), source_rows in zip(sources, raw[1:]):
                contributions = [json.loads(value) for value in source_rows]
                if metrics is None:
                    merge_annual_activity_facts(facts, contributions)
                else:
                    merge_annual_activity_facts(facts, contributions, metrics=metrics)
            return aggregate_annual_facts(facts, metadata, period, today=today, include_details=True)

        def presentation_fields(result):
            from copy import deepcopy
            result = deepcopy(result)
            result["qaEvidence"] = evidence_refs
            issue_ids = tuple({item["issueId"] for kind in ("current", "previous") for item in result[kind]["details"]})
            with self.database.connect() as connection:
                fields = {row[0]: {"key": row[1], "summary": row[2], "status": row[3], "webUrl": row[4],
                                   "cachedAt": str(row[5] or "")}
                          for row in connection.execute(select(jira_issues.c.issue_id, jira_issues.c.issue_key,
                              jira_issues.c.summary, jira_issues.c.status_name, jira_issues.c.web_url, jira_issues.c.cached_at)
                              .where(jira_issues.c.issue_id.in_(issue_ids)))} if issue_ids else {}
            for kind in ("current", "previous"):
                for item in result[kind]["details"]:
                    item.update(fields.get(item["issueId"], {}))
            return result

        if not cacheable:
            return presentation_fields(aggregate(raw_facts))

        def load():
            identities = {snapshot_id, *(identity for identity, _metrics in sources if identity)}
            with self.database.connect() as connection:
                if not connection.in_transaction():
                    connection.begin()
                existing = {item[0] for item in connection.execute(
                    select(jira_analytics_snapshots.c.snapshot_id)
                    .where(jira_analytics_snapshots.c.snapshot_id.in_(identities)))}
                if existing != identities:
                    raise LookupError('query_snapshot_unavailable')
                raw = read_raw_facts(connection)
            return aggregate(raw)

        dependencies = json.dumps([snapshot_id, sources, row[3], roster], sort_keys=True, separators=(',', ':'))
        identity = CacheIdentity(format_version=2, namespace='jira-annual-statistics',
            scope=json.dumps([row[1], row[2]], separators=(',', ':')),
            dependency_revision=sha256(dependencies.encode()).hexdigest(),
            dimensions=(('period', str(period)),), time_boundary=today.isoformat())
        return presentation_fields(self._statistics_cache.get_or_load(identity, load,
            ttl_seconds=self._statistics_cache_ttl_seconds))

    def annual_source(self, account, source_key, revision):
        state = self.state("", account, card_key=source_key)
        snapshot_id = state["activeSnapshotId"]
        if not snapshot_id:
            return ""
        with self.database.connect() as connection:
            row = connection.execute(
                text("SELECT statistics_json FROM jira_analytics_snapshots WHERE snapshot_id=:snapshot_id"),
                {"snapshot_id": snapshot_id},
            ).fetchone()
        metadata = json.loads(row[0]) if row and row[0] else {}
        return snapshot_id if metadata.get("queryRevision") == revision else ""

    def statistics_preview(self, session_hash, account, *, card_key):
        with self.database.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT s.jql,s.comparison_jql,s.user_jql,s.roster_fingerprint,s.statistics_json,s.snapshot_id\n                FROM jira_analytics_queries q JOIN jira_analytics_snapshots s\n                ON s.snapshot_id=COALESCE(NULLIF(q.pending_snapshot_id,''),q.last_snapshot_id)\n                WHERE q.session_hash=:session_hash AND q.account=:account AND s.state IN ('pending','failed','cancelled','active')"
                ),
                {
                    "session_hash": self._scope(session_hash, card_key, account),
                    "account": str(account).casefold(),
                },
            ).fetchone()
        return (
            {
                "jql": row[0],
                "comparisonJql": row[1],
                "userJql": row[2],
                "fingerprint": row[3],
                "statistics": json.loads(row[4]) if row[4] else None,
                "snapshotId": row[5],
            }
            if row
            else None
        )

    def activate(self, snapshot_id, statistics=None):
        with self.database.transaction() as connection:
            scope = self._lock_query(connection, snapshot_id)
            cursor = connection.execute(
                text(
                    "UPDATE jira_analytics_queries\n                SET active_snapshot_id=:active_snapshot_id,pending_snapshot_id='',task_id=''\n                WHERE session_hash=:scope AND pending_snapshot_id=:pending_snapshot_id"
                ),
                {
                    "active_snapshot_id": snapshot_id,
                    "pending_snapshot_id": snapshot_id,
                    "scope": scope,
                },
            )
            if not cursor.rowcount:
                connection.execute(
                    text(
                        "UPDATE jira_analytics_snapshots SET state='stale' WHERE snapshot_id=:snapshot_id"
                    ),
                    {"snapshot_id": snapshot_id},
                )
                return False
            connection.execute(
                text(
                    "UPDATE jira_analytics_snapshots SET state='active',statistics_json=:statistics_json\n                WHERE snapshot_id=:snapshot_id"
                ),
                {
                    "statistics_json": (
                        None
                        if statistics is None
                        else json.dumps(statistics, ensure_ascii=False, sort_keys=True)
                    ),
                    "snapshot_id": snapshot_id,
                },
            )
            return True

    def finish(self, snapshot_id, state, error=""):
        with self.database.transaction() as connection:
            scope = self._lock_query(connection, snapshot_id)
            connection.execute(
                text(
                    "UPDATE jira_analytics_snapshots SET state=:state,error=:error WHERE snapshot_id=:snapshot_id"
                ),
                {
                    "state": str(state),
                    "error": str(error),
                    "snapshot_id": str(snapshot_id),
                },
            )
            connection.execute(
                text(
                    "UPDATE jira_analytics_queries SET pending_snapshot_id='',task_id=''\n                WHERE session_hash=:scope AND pending_snapshot_id=:pending_snapshot_id"
                ),
                {"pending_snapshot_id": str(snapshot_id), "scope": scope},
            )

    def state(self, session_hash, account, *, card_key=""):
        task_session_hash = str(session_hash)
        session_hash = self._scope(session_hash, card_key, account)
        with self.database.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT active_snapshot_id,pending_snapshot_id,task_id,task_session_hash,last_snapshot_id\n                FROM jira_analytics_queries WHERE session_hash=:session_hash AND account=:account AND (card_key!='' OR expires_at>:expires_at)"
                ),
                {
                    "session_hash": str(session_hash),
                    "account": str(account).casefold(),
                    "expires_at": self._now(),
                },
            ).fetchone()
            if row is None:
                return {"activeSnapshotId": "", "pendingSnapshotId": "", "taskId": "", "activeJql": "", "activeComparisonJql": "",
                        "userJql": None, "latestState": "", "error": "", "rosterFingerprint": ""}
            active_jql = active_comparison_jql = ""
            user_jql = None
            fingerprint = ""
            if row[0]:
                value = connection.execute(
                    text(
                        "SELECT jql,user_jql,roster_fingerprint,comparison_jql FROM jira_analytics_snapshots WHERE snapshot_id=:snapshot_id"
                    ),
                    {"snapshot_id": row[0]},
                ).fetchone()
                active_jql = value[0] if value else ""
                user_jql = value[1] if value else None
                fingerprint = value[2] if value else ""
                active_comparison_jql = value[3] if value else ""
            latest = connection.execute(select(jira_analytics_snapshots.c.state, jira_analytics_snapshots.c.error).where(jira_analytics_snapshots.c.session_hash == str(session_hash), jira_analytics_snapshots.c.account == str(account).casefold(), or_(row[4] == '', jira_analytics_snapshots.c.snapshot_id == row[4])).order_by(jira_analytics_snapshots.c.created_at.desc(), jira_analytics_snapshots.c.sequence.desc()).limit(1)).fetchone()
            return {"activeSnapshotId": row[0], "pendingSnapshotId": row[1],
                    "taskId": row[2] if not card_key or row[3] == task_session_hash else "", "activeJql": active_jql,
                    "activeComparisonJql": active_comparison_jql,
                    "userJql": user_jql, "latestState": latest[0] if latest else "", "error": latest[1] if latest else "",
                    "rosterFingerprint": fingerprint}

    def issue_keys(self, session_hash, account, *, card_key="", period_kind="current"):
        session_hash = self._scope(session_hash, card_key, account)
        with self.database.connect() as connection:
            return [
                row[0]
                for row in connection.execute(
                    text(
                        "SELECT i.issue_key\n                FROM jira_analytics_queries q\n                JOIN jira_analytics_snapshot_issues m ON m.snapshot_id=q.active_snapshot_id\n                JOIN jira_issues i ON i.issue_id=m.issue_id\n                WHERE q.session_hash=:session_hash AND q.account=:account AND (q.card_key!='' OR q.expires_at>:expires_at) AND m.period_kind=:period_kind\n                ORDER BY m.ordinal"
                    ),
                    {
                        "session_hash": str(session_hash),
                        "account": str(account).casefold(),
                        "expires_at": self._now(),
                        "period_kind": period_kind,
                    },
                )
            ]

    def delete_session(self, session_hash):
        with self.database.transaction() as connection:
            connection.execute(
                text(
                    "DELETE FROM jira_analytics_queries WHERE session_hash=:session_hash OR session_hash LIKE :session_hash_p1"
                ),
                {
                    "session_hash": str(session_hash),
                    "session_hash_p1": f"{session_hash}:card:%",
                },
            )

    def interrupt_pending(self):
        """Called before serving requests: runtime tasks do not survive restart."""
        table = jira_analytics_queries
        with self.database.transaction() as connection:
            scopes = (
                connection.execute(
                    select(table.c.session_hash)
                    .where(table.c.pending_snapshot_id != "")
                    .order_by(table.c.session_hash)
                )
                .scalars()
                .all()
            )
            for scope in scopes:
                pending = connection.execute(
                    select(table.c.pending_snapshot_id)
                    .where(table.c.session_hash == scope)
                    .with_for_update()
                ).scalar_one_or_none()
                if not pending:
                    continue
                connection.execute(
                    update(jira_analytics_snapshots)
                    .where(jira_analytics_snapshots.c.snapshot_id == pending)
                    .values(state="failed", error="query_interrupted")
                )
                connection.execute(
                    update(table)
                    .where(
                        table.c.session_hash == scope,
                        table.c.pending_snapshot_id == pending,
                    )
                    .values(pending_snapshot_id="", task_id="")
                )

    def delete_account(self, account):
        with self.database.transaction() as connection:
            connection.execute(
                text("DELETE FROM jira_analytics_queries WHERE account=:account"),
                {"account": str(account).casefold()},
            )

    def cleanup(self):
        with self.database.transaction() as connection:
            return self._delete_expired(connection, self._now())

    def _metric_sources(self, connection, account, metadata, roster):
        from .analytics_refresh import metric_source_key
        resolved = []
        for year, metrics in metadata.get('metricSources', {}).items():
            for metric, source in metrics.items():
                key = metric_source_key(source['card'], source['query'], roster, metric)
                row = connection.execute(select(jira_analytics_snapshots.c.snapshot_id,
                    jira_analytics_snapshots.c.statistics_json, jira_analytics_snapshots.c.roster_fingerprint, jira_analytics_snapshots.c.created_at)
                    .join(jira_analytics_queries, jira_analytics_queries.c.active_snapshot_id == jira_analytics_snapshots.c.snapshot_id)
                    .where(jira_analytics_queries.c.session_hash == self._scope('', key, account),
                           jira_analytics_snapshots.c.account == account, jira_analytics_snapshots.c.card_key == key,
                           jira_analytics_snapshots.c.state == 'active')).first()
                evidence = json.loads(row[1] or '{}').get('qaEvidence', {}) if row else {}
                resolved.append({'year': year, 'metric': metric, 'snapshotId': row[0] if row else '',
                    'rosterFingerprint': row[2] if row else '', 'version': evidence.get('version'),
                    'complete': evidence.get('complete', False), 'roster': evidence.get('roster'), 'createdAt': row[3] if row else 0})
        return resolved

    @staticmethod
    def _qa_references(base, period, today, sources):
        from core.jira.services.filter_service import jira_period_ranges
        result = {}
        for kind, window in jira_period_ranges(period, today).items():
            years = (['previous'] if window['start'] < f'{today.year}-01-01' else [])
            if window['end'] is None or window['end'] > f'{today.year}-01-01':
                years.append('current')
            result[kind] = {}
            for metric in ('comment', 'verify'):
                selected = []
                for year in years:
                    source = next((item for item in sources if item['year'] == year and item['metric'] == metric),
                        {'year': year, 'snapshotId': '', 'rosterFingerprint': '', 'version': None, 'complete': False})
                    selected.append({key: source[key] for key in ('year', 'snapshotId', 'rosterFingerprint', 'version', 'complete')})
                result[kind][metric] = {'baseSnapshotId': base, 'period': period, 'periodKind': kind,
                    'metric': metric, 'range': {key: window[key] for key in ('start', 'end')}, 'sources': selected}
        return result

    def qa_details(self, account, card, reference, *, product_line='', person='', all_qa=False,
                   issue_id='', actions=False, page=0, download=False):
        from core.jira.mapper import _datetime
        from core.jira.services.jql_statistics_cards import load_activity_qa_roster, effective_card_jql_pair, card_definition
        account = str(account).casefold()
        if type(page) is not int or page < 0 or not isinstance(reference, dict) or (download and not actions):
            raise ValueError('invalid_details')
        metric, period, kind = (reference.get(key) for key in ('metric', 'period', 'periodKind'))
        if metric not in ('comment', 'verify') or period not in ('week', 'month', 'quarter', 'year') or kind not in ('current', 'previous'):
            raise ValueError('invalid_details')
        with self.database.connect() as connection:
            connection.begin()
            base = connection.execute(select(jira_analytics_snapshots).join(jira_analytics_queries,
                jira_analytics_queries.c.active_snapshot_id == jira_analytics_snapshots.c.snapshot_id).where(
                    jira_analytics_queries.c.session_hash == self._scope('', card, account),
                    jira_analytics_snapshots.c.account == account, jira_analytics_snapshots.c.card_key == card,
                    jira_analytics_snapshots.c.state == 'active')).mappings().first()
            conditions = connection.execute(select(jira_analytics_queries.c.user_conditions_json).where(
                jira_analytics_queries.c.session_hash == self._scope('', 'conditions', account))).scalar_one_or_none()
            applied = json.loads(conditions or '{}').get('userJql')
            expected = effective_card_jql_pair(card, applied or '', 'year')
            if not base or base['snapshot_id'] != reference.get('baseSnapshotId') or applied is None or base['user_jql'] != applied or base['jql'] != expected['current']['jql'] or base['comparison_jql'] != expected['previous']['jql']:
                raise ValueError('details_version_changed')
            fingerprint = card_definition(card).fingerprint
            legacy = f'{fingerprint}:annual-activity-v4:' + load_activity_qa_roster().fingerprint
            if base['roster_fingerprint'] not in (f'{fingerprint}:annual-metrics-v5', legacy):
                raise ValueError('details_version_changed')
            metadata = json.loads(base['statistics_json'] or '{}')
            sources = self._metric_sources(connection, account, metadata, load_activity_qa_roster().fingerprint)
            expected_ref = self._qa_references(base['snapshot_id'], period, self._today(), sources)[kind][metric]
            if reference != expected_ref:
                raise ValueError('details_version_changed')
            wanted = [source for source in sources if source['metric'] == metric and
                      source['year'] in {item['year'] for item in reference['sources']}]
            if not wanted or len(wanted) != len(reference['sources']) or any(not item['snapshotId'] or item['version'] != 1 or not item['complete'] or item['roster'] is None for item in wanted):
                raise ValueError('details_not_collected')
            rosters = {source['snapshotId']: dict(source['roster']) for source in wanted}
            events, facts = jira_analytics_qa_events, jira_analytics_facts
            created = func.left(func.json_unquote(func.json_extract(facts.c.fact_json, '$.createdAt')), 10)
            query = select(events.c.sequence, events.c.snapshot_id, events.c.issue_id, events.c.event_kind,
                events.c.source_id, events.c.author_account, events.c.author_display_name, events.c.created_at).join(
                    facts, (facts.c.snapshot_id == events.c.snapshot_id) & (facts.c.issue_id == events.c.issue_id)).where(
                        events.c.snapshot_id.in_(rosters), created >= reference['range']['start'])
            if reference['range']['end'] is not None:
                query = query.where(created < reference['range']['end'])
            if issue_id:
                query = query.where(events.c.issue_id == str(issue_id))
            entries = []
            for row in connection.execute(query).mappings():
                author = row['author_account'].strip().casefold()
                assignments = rosters[row['snapshot_id']].get(author)
                if assignments is None or (person and author != person) or (not all_qa and product_line not in assignments):
                    continue
                stamp = _datetime(row['created_at'])
                entry = dict(row)
                entry.update(person=author, assignments=assignments,
                    order=(stamp.timestamp() if stamp else float('-inf'), row['event_kind'], row['source_id'], row['snapshot_id']))
                entries.append(entry)
            entries.sort(key=lambda item: item['order'], reverse=True)
            page_size = 1000 if download else 25
            if actions:
                selected = entries[page * page_size:(page + 1) * page_size]
                payloads = {row[0]: json.loads(row[1]) for row in connection.execute(select(events.c.sequence, events.c.payload_json)
                    .where(events.c.sequence.in_([entry['sequence'] for entry in selected])))} if selected else {}
                rows = [{'issueId': entry['issue_id'], 'person': entry['person'], 'personName': entry['author_display_name'],
                    'eventKind': entry['event_kind'], 'sourceId': entry['source_id'], 'createdAt': entry['created_at'],
                    'assignments': entry['assignments'], 'payload': payloads[entry['sequence']]} for entry in selected]
                total = len(entries)
            else:
                grouped = {}
                for entry in entries:
                    key = (entry['issue_id'], entry['person'])
                    group = grouped.setdefault(key, {'issueId': key[0], 'person': key[1], 'personName': entry['author_display_name'],
                        'assignments': entry['assignments'], 'commentCount': 0, 'attachmentCount': 0, 'historyCount': 0,
                        'createdAt': entry['created_at']})
                    group[{'comment': 'commentCount', 'attachment': 'attachmentCount', 'verify': 'historyCount'}[entry['event_kind']]] += 1
                total = len(grouped); rows = list(grouped.values())[page * page_size:(page + 1) * page_size]
            ids = {row['issueId'] for row in rows}
            fields = {row[0]: {'key': row[1], 'summary': row[2], 'webUrl': row[3]} for row in connection.execute(
                select(jira_issues.c.issue_id, jira_issues.c.issue_key, jira_issues.c.summary, jira_issues.c.web_url)
                .where(jira_issues.c.issue_id.in_(ids)))} if ids else {}
            for row in rows:
                row.update(fields.get(row['issueId'], {}))
            totals = {kind: sum(entry['event_kind'] == kind for entry in entries) for kind in ('comment', 'attachment', 'verify')}
            return {'rows': rows, 'total': total, 'page': page, 'pageSize': page_size, 'reference': expected_ref,
                    'updatedAt': datetime.fromtimestamp(max(source['createdAt'] for source in wanted), timezone.utc).isoformat(),
                    'metric': metric, 'totals': totals}
