from __future__ import annotations

from time import monotonic
from uuid import uuid4

from .analytics_refresh import JiraRefreshProgress

from core.logging import smart_log
from core.async_tasks import TaskCancelled
from core.jira.gateway import JiraGateway
from core.jira.services.filter_service import build_basic_jql, compose_jql


class JiraAnalyticsService:
    def __init__(self, filters, gateway, mapper, repository, tasks, *, fixed_conditions="", card_key="",
                 roster_fingerprint="", statistics_builder=None, comparison_conditions="",
                 activity_conditions=None, activity_roster=None,
                 business_card_key=None,
                 reuse_basic_facts=False,
                 on_error=lambda _error: None):
        self.filters, self.gateway, self.mapper = filters, gateway, mapper
        self.repository, self.tasks = repository, tasks
        self.on_error = on_error
        self.fixed_conditions = fixed_conditions
        self.card_key = card_key
        self.business_card_key = business_card_key or card_key
        self.roster_fingerprint = roster_fingerprint
        self.statistics_builder = statistics_builder
        self.reuse_basic_facts = reuse_basic_facts
        self.comparison_conditions = comparison_conditions
        self.activity_conditions = activity_conditions or {}
        self.activity_roster = activity_roster

    def schema(self):
        fields = self.filters.fields()
        by_id = {item["id"].casefold(): item for item in fields}
        fixed = [by_id[field_id] for field_id in ("project", "issuetype", "status", "assignee", "resolution") if field_id in by_id]
        return {"fixed": fixed, "more": [
            item for item in fields
            if item["id"].casefold() not in {"project", "issuetype", "status", "resolution", "text", "assignee"}
        ]}

    def suggestions(self, field_name, query=""):
        return self.filters.suggestions(field_name, query)

    def preview(self, payload):
        mode = payload.get("mode", "advanced")
        if mode == "basic":
            fields = {item["id"]: item for item in self.filters.fields()}
            jql = build_basic_jql(payload.get("basic") or {}, fields)
        else:
            jql = str(payload.get("jql") or "")
        effective = compose_jql(jql, self.fixed_conditions)
        validation = self.filters.validate(effective) if effective else {"valid": True, "errors": []}
        return {**validation, "jql": effective, "userJql": jql}

    def search(self, session_hash, account, expires_at, payload, *, scheduled=False,
               cancellation=None, report_progress=None, periods=('current', 'previous')):
        if scheduled:
            previous = self.repository.statistics_preview(session_hash, account, card_key=self.card_key)
            payload = {**payload, 'queryRevision': ((previous or {}).get('statistics') or {}).get('publishedRevision', '')}
        mode = payload.get("mode", "basic")
        basic = payload.get("basic") or {}
        preview = self.preview({**payload, "mode": mode})
        jql, user_jql = preview["jql"], preview["userJql"]
        comparison_jql = compose_jql(user_jql, self.comparison_conditions) if self.comparison_conditions else ""
        validation = {key: value for key, value in preview.items() if key not in {"jql", "userJql"}}
        activity_queries = {period: compose_jql(user_jql, fixed) for period, fixed in self.activity_conditions.items() if period in periods}
        for query in ([comparison_jql] if comparison_jql else []) + list(activity_queries.values()):
            comparison_validation = self.filters.validate(query)
            if not comparison_validation.get("valid"):
                validation = comparison_validation
        current = self.repository.state(session_hash, account, card_key=self.card_key)
        if not validation.get("valid"):
            return {"validation": validation, "taskId": "", "state": current}
        revision = payload.get("queryRevision") or uuid4().hex
        if self.activity_conditions:
            previous = self.repository.statistics_preview(session_hash, account, card_key=self.card_key)
            if previous and (previous.get("statistics") or {}).get("publishedRevision") == revision:
                revision = uuid4().hex
        snapshot_id = self.repository.begin(
            session_hash, account, jql, basic if mode == "basic" else {},
            payload.get("sourceFilterId", ""), expires_at=expires_at, user_jql=user_jql, card_key=self.card_key,
            roster_fingerprint=self.roster_fingerprint,
            comparison_jql=comparison_jql,
            query_revision=revision if activity_queries else "",
            published_revision=payload.get("queryRevision", ""),
            expected_user_jql=user_jql if scheduled and self.card_key == self.business_card_key else None,
        )
        activity_sources = activity_queries
        source_scopes = {}
        if activity_queries:
            from .analytics_refresh import metric_source_key
            if not scheduled:
                self.repository.register_history_scope(account, self.card_key, user_jql)
            source_scopes = {period: {metric: {"scope": metric_source_key(self.business_card_key, query,
                self.activity_roster.fingerprint, metric), "query": query, "card": self.business_card_key} for metric in ("comment", "verify")}
                for period, query in activity_queries.items()}

        card_queued_at = monotonic()
        def run(token, progress):
            started = monotonic()
            outcome = "failed"
            processed = 0
            page_report = None
            def log_stage(stage, since, *, period="both", layer="basic", page_count=0, processed=0, outcome="completed"):
                smart_log("Jira card load stage", platform="web", domain="jira", extra={
                    "card_key": self.card_key, "snapshot_id": snapshot_id, "stage": stage, "period": period, "layer": layer,
                    "duration_ms": round((monotonic() - since) * 1000, 3),
                    "page_count": page_count, "processed": processed, "outcome": outcome,
                })
            stage, period, layer = "queue", "both", "basic"
            log_stage(stage, card_queued_at, outcome="started")
            try:
                token.raise_if_cancelled()
                layered = bool(self.statistics_builder and comparison_jql)
                rows_by_period = {"current": [], "previous": []}
                availability = {period: {"basic": False,
                    **({"comment": False, "verify": False} if activity_sources else {})} for period in rows_by_period}
                queries = {"current": jql, **({"previous": comparison_jql} if comparison_jql else {})}
                queries = {period: query for period, query in queries.items() if period in periods}
                statistics = None
                current_facts = None
                basic_statistics = None
                from ..daily_refresh import manual_metric_allowed
                layers = ['basic', *(['comment'] if activity_sources else [])] if layered else ['basic']
                for layer in layers:
                    if not scheduled and not manual_metric_allowed(self.business_card_key, layer):
                        continue
                    for period, query in queries.items():
                        token.raise_if_cancelled()
                        since = monotonic()
                        stage = "search"
                        log_stage(stage, since, period=period, layer=layer, outcome="started")
                        observed_total = 0
                        def report(count, total):
                            nonlocal observed_total
                            observed_total = max(observed_total, total)
                            token.raise_if_cancelled()
                            progress(processed + count, processed + total)
                        if layer == "comment":
                            from .analytics_refresh import refresh_metric
                            page_report = JiraRefreshProgress(self.business_card_key, layer, report)
                            refresh_metric(self.repository, self.tasks.pages, self.gateway, account, self.business_card_key,
                                activity_queries[period], self.activity_roster, "comment", token, report, log_progress=page_report)
                            page_report = None
                            rows = []
                        else:
                            page_report = JiraRefreshProgress(self.business_card_key, layer, report)
                            rows = self.gateway.search_all_payloads(
                                query, fields=list(JiraGateway.CORE_FIELDS),
                                page_size=100, progress=page_report,
                            )
                            page_report.finish('completed')
                            page_report = None
                        processed += observed_total if layer == "comment" else len(rows)
                        log_stage("reuse" if layer == "comment" else "search", since, period=period, layer=layer,
                                  page_count=max(1, (observed_total + 99) // 100), processed=observed_total)
                        token.raise_if_cancelled()
                        since = monotonic()
                        stage = "persist"
                        log_stage(stage, since, period=period, layer=layer, outcome="started")
                        if layer == "basic":
                            rows_by_period[period] = rows
                            for start in range(0, len(rows), 500):
                                token.raise_if_cancelled()
                                self.repository.write_batch(snapshot_id, [self.mapper.from_search(item)
                                    for item in rows[start:start + 500]], period)
                        availability[period][layer] = True
                        log_stage("persist", since, period=period, layer=layer, processed=len(rows))
                        token.raise_if_cancelled()
                        if layered:
                            since = monotonic()
                            stage = "aggregate"
                            log_stage(stage, since, period=period, layer=layer, outcome="started")
                            if self.reuse_basic_facts and layer == "comment":
                                statistics = dict(basic_statistics)
                            else:
                                statistics = self.statistics_builder(rows_by_period["current"], rows_by_period["previous"],
                                    **({"activity_facts": []} if activity_sources else {}),
                                    **({"current_facts": current_facts} if self.reuse_basic_facts else {}))
                            statistics["availability"] = {key: dict(value) for key, value in availability.items()}
                            if activity_sources:
                                statistics["queryRevision"] = revision
                                statistics["publishedRevision"] = payload.get("queryRevision", "")
                                statistics["metricSources"] = source_scopes
                            token.raise_if_cancelled()
                            facts = statistics.pop("_facts", None)
                            written_facts = facts
                            if self.reuse_basic_facts and layer == "basic":
                                if period == "previous" and current_facts is not None:
                                    written_facts = facts[len(current_facts):]
                                if period == "current":
                                    current_facts = facts
                                basic_statistics = statistics
                            # Comment layers publish independent sources; their basic issue facts did not change.
                            if not self.repository.update_statistics(
                                snapshot_id,
                                statistics,
                                **(
                                    {"facts": written_facts}
                                    if layer == "basic" and facts is not None
                                    else {}
                                ),
                            ):
                                raise TaskCancelled()
                            log_stage("aggregate", since, period=period, layer=layer, processed=len(rows))
                if not layered:
                    statistics = self.statistics_builder(rows_by_period["current"]) if self.statistics_builder else None
                token.raise_if_cancelled()
                stage = "activate"
                if not self.repository.activate(snapshot_id, statistics):
                    raise TaskCancelled('snapshot_superseded')
                outcome = "completed"
            except TaskCancelled:
                outcome = "cancelled"
                if page_report is not None and not page_report.finished:
                    page_report.finish(outcome)
                self.repository.finish(snapshot_id, "cancelled")
                raise
            except Exception as error:
                cause = error.__cause__ or error
                failure = {
                    "card_key": self.card_key, "snapshot_id": snapshot_id, "stage": stage,
                    "period": period, "layer": layer, "outcome": "failed",
                    "cause_type": type(cause).__name__, "error_code": getattr(error, "code", type(error).__name__),
                    "http_status": getattr(getattr(cause, "response", None), "status_code", None),
                    "duration_ms": round((monotonic() - started) * 1000, 3)}
                if page_report is not None:
                    if not page_report.finished:
                        page_report.finish(**failure)
                else:
                    smart_log("Jira card failed", platform="web", domain="jira", level="ERROR", extra=failure)
                self.repository.finish(snapshot_id, "failed", getattr(error, "code", type(error).__name__))
                self.on_error(error)
                raise
            finally:
                log_stage("complete", started, processed=processed, outcome=outcome)

        if scheduled:
            run(cancellation, report_progress)
            return {"validation": validation, "snapshotId": snapshot_id}
        task_id = self.tasks.submit(session_hash, run, card_key=self.card_key)
        self.repository.set_task(snapshot_id, task_id, session_hash=session_hash)
        return {"validation": validation, "taskId": task_id, "snapshotId": snapshot_id,
                "state": self.repository.state(session_hash, account, card_key=self.card_key)}


def card_analytics_service(filters, gateway, repository, tasks, card_key, *, storage_key=None,
                           on_error=lambda _error: None):
    from core.jira.mapper import JiraIssueMapper
    from core.jira.services.jql_statistics_cards import (
        card_definition, effective_card_jql_pair, activity_card_jql_pair,
        load_activity_qa_roster, build_annual_facts,
    )
    definition = card_definition(card_key) if card_key else None
    pair = effective_card_jql_pair(card_key, '', 'year') if definition else {}
    roster = load_activity_qa_roster() if definition else None
    groups = {}
    return JiraAnalyticsService(filters, gateway,
        JiraIssueMapper(gateway.config.base_url if hasattr(gateway, 'config') else ''),
        repository, tasks, card_key=storage_key or card_key, business_card_key=card_key,
        fixed_conditions=pair.get('current', {}).get('jql', ''),
        comparison_conditions=pair.get('previous', {}).get('jql', ''),
        activity_conditions={key: item['jql'] for key, item in activity_card_jql_pair(card_key, '').items()} if definition else None,
        activity_roster=roster,
        roster_fingerprint=f'{definition.fingerprint}:annual-metrics-v5' if definition else '',
        reuse_basic_facts=bool(definition),
        statistics_builder=(lambda current, previous, **kwargs: build_annual_facts(
            gateway, current, previous, groups_cache=groups, activity_roster=roster, **kwargs)) if definition else None,
        on_error=on_error)
