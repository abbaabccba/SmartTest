from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi import Body, Depends, HTTPException, Request

from core.product_lines import DASHBOARD_PRODUCT_LINES
from core.jira.domain import IssueDetails
from core.jira.mapper import JiraIssueMapper
from core.jira.services.jql_statistics_cards import (
    JIRA_STATISTICS_CARDS,
    build_annual_facts,
    card_definition,
    effective_card_jql_pair,
    activity_card_jql_pair,
    load_activity_qa_roster,
)

from .audit.registry import AuditConflictError
from .audit_http import (
    audit_task_payload as _audit_task_payload,
    cancel_audit as _cancel_audit,
    download_payload as _download_payload,
    get_audit_payload as _get_audit_payload,
    owned_task as _owned_task,
    request_session_id,
)
from .downloads import DownloadNotFoundError
from .jira.analytics_service import JiraAnalyticsService
from .jira.analytics_tasks import JIRA_ANALYTICS_TASKS
from .jira_filter_snapshot_repository import JIRA_FILTER_FIELDS
from .jira_http import issue_payload as _issue_payload
from .resource_access import remote_credentials_rejected


def create_router(authenticated_session, sessions, cache_database, jira_cache_owner, jira_analytics,
                  jira_filter_owner, jira_filters, audits, downloads, jira_audit_owner) -> APIRouter:
    router = APIRouter()

    def audit_session(request):
        return request_session_id(request)

    def resolve_jira_cache(value):
        try:
            return jira_cache_owner(value.username, value.password)
        except Exception as error:
            raise HTTPException(status_code=503, detail={"state": "cache_unavailable"}) from error

    def raise_downstream_error(value, error):
        if remote_credentials_rejected(error):
            jira_analytics.delete_account(value.username)
            sessions.invalidate_credentials(value.username)
            raise HTTPException(
                status_code=401, detail={"state": "invalid_credentials"},
            ) from error
        raise error

    @router.get("/api/jira/issues")
    def jira_issues(
        query: str = "", page: int = 0, pageSize: int = 100,
        value=Depends(authenticated_session),
    ):
        owner = resolve_jira_cache(value)
        try:
            result = owner.list_issues(query, page, pageSize)
        except Exception as error:
            raise_downstream_error(value, error)
        return {
            "issues": [_issue_payload(issue, ()) for issue in result.issues],
            "pagination": {"page": result.page, "pageSize": result.page_size, "total": result.total},
        }

    @router.get("/api/jira/issues/{issue_key}")
    def jira_issue(issue_key: str, request: Request, value=Depends(authenticated_session)):
        names = tuple(dict.fromkeys(request.query_params.getlist("details")))
        allowed = {"description", "comments", "attachments", "links", "custom_fields"}
        if any(name not in allowed for name in names):
            raise HTTPException(status_code=422, detail={"state": "invalid_details"})
        details = IssueDetails(**{name: True for name in names})
        try:
            issue = resolve_jira_cache(value).get_issue(issue_key, details)
        except Exception as error:
            raise_downstream_error(value, error)
        if issue is None:
            raise HTTPException(status_code=404, detail={"state": "not_found"})
        return _issue_payload(issue, names)

    @router.delete("/api/jira/issues/{issue_key}")
    def invalidate_jira_issue(issue_key: str, value=Depends(authenticated_session)):
        resolve_jira_cache(value).invalidate_issue(issue_key)
        return {"invalidated": issue_key}

    def resolve_jira_analytics(value, *, card_key=""):
        try:
            filters, gateway = jira_filter_owner(value.username, value.password)
        except Exception as error:
            raise HTTPException(status_code=503, detail={"state": "analytics_unavailable"}) from error
        definition = card_definition(card_key) if card_key else None
        period = "year" if definition else None
        query_pair = effective_card_jql_pair(card_key, "", period) if definition else None
        current_fixed = query_pair["current"]["jql"] if query_pair else ""
        previous_fixed = query_pair["previous"]["jql"] if query_pair else ""
        groups_cache = {}
        activity_roster = load_activity_qa_roster() if definition else None
        return JiraAnalyticsService(
            filters, gateway, JiraIssueMapper(gateway.config.base_url if hasattr(gateway, "config") else ""),
            jira_analytics, JIRA_ANALYTICS_TASKS,
            fixed_conditions=current_fixed,
            comparison_conditions=previous_fixed,
            activity_conditions={key: item["jql"] for key, item in activity_card_jql_pair(card_key, "").items()} if definition else None,
            activity_roster=activity_roster,
            card_key=card_key,
            roster_fingerprint=f"{definition.fingerprint}:annual-activity-v4:{activity_roster.fingerprint}" if definition else "",
            statistics_builder=(lambda current, previous, **kwargs: build_annual_facts(
                gateway, current, previous, groups_cache=groups_cache, activity_roster=activity_roster, **kwargs,
            )) if definition else None,
            on_error=lambda error: invalidate_analytics_credentials(value, error),
        )

    def invalidate_analytics_credentials(value, error):
        if remote_credentials_rejected(error):
            jira_analytics.delete_account(value.username)
            sessions.invalidate_credentials(value.username)

    def require_jira_card(card_key):
        if card_key not in JIRA_STATISTICS_CARDS:
            raise HTTPException(status_code=404, detail={"state": "card_not_found"})

    def jira_card_period(value, card_key):
        from core.jira.services.filter_service import JIRA_PERIODS
        period = sessions.get_preferences(value.username, f"jira/cards/{card_key}")["items"].get("period", "month")
        if not isinstance(period, str) or period not in JIRA_PERIODS:
            raise HTTPException(status_code=422, detail={"state": "invalid_jira_period"})
        return period

    def analytics_query_state(session_hash, account, card_key):
        query = jira_analytics.state(session_hash, account, card_key=card_key)
        task = JIRA_ANALYTICS_TASKS.status(session_hash, query["taskId"], card_key=card_key) if query["taskId"] else None
        if query["pendingSnapshotId"] and query["taskId"]:
            terminal = "failed" if task is None else task["state"]
            if terminal in {"failed", "cancelled"}:
                error = "query_interrupted" if task is None else ("query_cancelled" if terminal == "cancelled" else "query_failed")
                jira_analytics.finish(query["pendingSnapshotId"], terminal, error)
                query = jira_analytics.state(session_hash, account, card_key=card_key)
        return query, task

    def card_query_payload(query):
        return {key: value for key, value in query.items()
                if key not in {"activeJql", "activeComparisonJql", "userJql"}}

    @router.get("/api/jira/analytics/state")
    def jira_analytics_state(request: Request, value=Depends(authenticated_session)):
        conditions = jira_analytics.published_conditions(audit_session(request), value.username)
        return {"conditions": conditions, "userJql": conditions.get("userJql", "") if conditions else ""}

    @router.get("/api/jira/cards/{card_key}/statistics")
    def jira_analytics_statistics(card_key: str, request: Request, value=Depends(authenticated_session)):
        require_jira_card(card_key)
        session_hash = audit_session(request)
        query, task = analytics_query_state(session_hash, value.username, card_key)
        period = jira_card_period(value, card_key)
        conditions = jira_analytics.published_conditions(session_hash, value.username)
        applied_jql = (conditions or {}).get("userJql")
        expected = effective_card_jql_pair(card_key, applied_jql or "", "year")
        fingerprint = f"{card_definition(card_key).fingerprint}:annual-activity-v4:{load_activity_qa_roster().fingerprint}"
        def matches(jql, comparison_jql, user_jql, version):
            return (user_jql is not None and user_jql == applied_jql and version == fingerprint
                    and jql == expected["current"]["jql"] and comparison_jql == expected["previous"]["jql"])
        active_matches = matches(query["activeJql"], query["activeComparisonJql"],
                                 query["userJql"], query["rosterFingerprint"])
        summary = jira_analytics.local_statistics(query["activeSnapshotId"], period) if active_matches else None
        active_matches = active_matches and summary is not None
        preview = jira_analytics.statistics_preview(session_hash, value.username, card_key=card_key)
        preview_matches = preview is not None and matches(preview["jql"], preview["comparisonJql"],
                                                         preview["userJql"], preview["fingerprint"])
        state = ("loading" if query["pendingSnapshotId"] and preview_matches else query["latestState"]
                 if preview_matches and query["latestState"] in {"failed", "cancelled"} else "ready"
                 if active_matches else "no_snapshot")
        payload = {"state": state, "period": period}
        if query["activeSnapshotId"] or query["pendingSnapshotId"] or query["latestState"]:
            payload["query"] = card_query_payload(query)
        if state == "loading":
            payload["task"] = task
        if state in {"failed", "cancelled"}:
            payload["error"] = query["error"] or ("query_cancelled" if state == "cancelled" else "query_failed")
        if preview_matches and preview["statistics"] and (state == "loading" or not active_matches):
            partial = jira_analytics.local_statistics(preview["snapshotId"], period)
            if partial is not None:
                payload.update(partial)
        elif active_matches:
            payload.update(summary)
        if state == "loading" and "productLines" not in payload:
            payload["productLines"] = [{"id": line.name, "label": line.name, "people": []}
                                       for line in DASHBOARD_PRODUCT_LINES]
            payload["availability"] = {key: {"basic": False, "activity": False} for key in ("current", "previous")}
        return payload

    @router.get("/api/jira/analytics/fields")
    def jira_analytics_fields(value=Depends(authenticated_session)):
        try:
            return resolve_jira_analytics(value).schema()
        except Exception as error:
            raise_downstream_error(value, error)

    @router.get("/api/jira/analytics/suggestions")
    def jira_analytics_suggestions(fieldName: str, query: str = "", value=Depends(authenticated_session)):
        try:
            return resolve_jira_analytics(value).suggestions(fieldName, query)
        except Exception as error:
            raise_downstream_error(value, error)

    @router.get("/api/jira/analytics/saved-filters")
    def jira_analytics_saved_filters(value=Depends(authenticated_session)):
        try:
            return resolve_jira_analytics(value).filters.saved_filters()
        except Exception as error:
            raise_downstream_error(value, error)

    @router.get("/api/jira/analytics/saved-filters/{filter_id}")
    def jira_analytics_saved_filter(filter_id: str, value=Depends(authenticated_session)):
        try:
            return resolve_jira_analytics(value).filters.saved_filter(filter_id)
        except Exception as error:
            raise_downstream_error(value, error)

    @router.post("/api/jira/analytics/validate")
    def jira_analytics_validate(payload: dict = Body(...), value=Depends(authenticated_session)):
        try:
            return resolve_jira_analytics(value).preview(payload)
        except Exception as error:
            raise_downstream_error(value, error)

    @router.post("/api/jira/analytics/search")
    def jira_analytics_search(request: Request, payload: dict = Body(...), value=Depends(authenticated_session)):
        try:
            preview = resolve_jira_analytics(value).preview(payload)
            validation = {key: item for key, item in preview.items() if key not in {"jql", "userJql"}}
            if validation.get("valid"):
                jira_analytics.publish_conditions(audit_session(request), value.username,
                                                  {**payload, "userJql": preview["userJql"]},
                                                  expires_at=value.expires_at)
            return {"applied": bool(validation.get("valid")), "validation": validation, "userJql": preview["userJql"]}
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail={"state": str(error)}) from error
        except Exception as error:
            raise_downstream_error(value, error)

    @router.post("/api/jira/cards/{card_key}/query")
    def query_jira_card(card_key: str, request: Request, payload: dict = Body(default={}), value=Depends(authenticated_session)):
        require_jira_card(card_key)
        intent = payload.get("intent", "refresh")
        if not isinstance(intent, str) or intent not in {"reuse", "refresh"}:
            raise HTTPException(status_code=422, detail={"state": "invalid_jira_query_intent"})
        from core.jira.services.filter_service import JIRA_PERIODS
        if "period" in payload:
            if not isinstance(payload["period"], str) or payload["period"] not in JIRA_PERIODS:
                raise HTTPException(status_code=422, detail={"state": "invalid_jira_period"})
            sessions.upsert_preferences(value.username, f"jira/cards/{card_key}", {"period": payload["period"]}, 1)
        period = jira_card_period(value, card_key)
        conditions = jira_analytics.published_conditions(audit_session(request), value.username)
        if conditions is None:
            return {"state": "no_snapshot", "period": period}
        if intent == "reuse":
            return jira_analytics_statistics(card_key, request, value)
        service = resolve_jira_analytics(value, card_key=card_key)
        try:
            started = service.search(
                audit_session(request), value.username, value.expires_at, conditions)
        except (TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail={"state": str(error)}) from error
        except Exception as error:
            raise_downstream_error(value, error)
        if not started["validation"].get("valid"):
            return {**jira_analytics_statistics(card_key, request, value), **started,
                    "state": "failed", "error": " · ".join(started["validation"].get("errors") or ["invalid_jql"])}
        return {**started, **jira_analytics_statistics(card_key, request, value)}

    @router.get("/api/jira/cards/{card_key}/tasks/{task_id}")
    def jira_analytics_task(card_key: str, task_id: str, request: Request, value=Depends(authenticated_session)):
        require_jira_card(card_key)
        task = JIRA_ANALYTICS_TASKS.status(audit_session(request), task_id, card_key=card_key)
        if task is None:
            raise HTTPException(status_code=404, detail={"state": "not_found"})
        return {**task, "query": card_query_payload(analytics_query_state(audit_session(request), value.username, card_key)[0])}

    @router.delete("/api/jira/cards/{card_key}/tasks/{task_id}")
    def cancel_jira_analytics_task(card_key: str, task_id: str, request: Request, value=Depends(authenticated_session)):
        require_jira_card(card_key)
        del value
        if not JIRA_ANALYTICS_TASKS.cancel(audit_session(request), task_id, card_key=card_key):
            raise HTTPException(status_code=404, detail={"state": "not_found"})
        return {"cancelled": True}

    def jira_filter_payload(session_id):
        selection = jira_filters.get(session_id)
        return {
            "facets": jira_filters.facets(),
            "snapshot": None if selection is None else {
                "filters": selection.filters, "jql": selection.jql,
                "revision": selection.revision,
            },
        }

    @router.get("/api/jira/filter-snapshot")
    def get_jira_filter_snapshot(request: Request, value=Depends(authenticated_session)):
        del value
        return jira_filter_payload(audit_session(request))

    @router.put("/api/jira/filter-snapshot")
    def put_jira_filter_snapshot(request: Request, payload: dict = Body(...),
                                 value=Depends(authenticated_session)):
        requested = payload.get("filters") or {}
        if (not isinstance(requested, dict) or not isinstance(payload.get("jql", ""), str)
                or any(key not in JIRA_FILTER_FIELDS or not isinstance(values, list)
                       or any(not isinstance(value, str) for value in values)
                       for key, values in requested.items())):
            raise HTTPException(status_code=422, detail={"state": "invalid_filters"})
        jira_filters.record(audit_session(request), requested, payload.get("jql", ""),
                            expires_at=value.expires_at)
        return jira_filter_payload(audit_session(request))

    @router.delete("/api/jira/filter-snapshot")
    def delete_jira_filter_snapshot(request: Request, value=Depends(authenticated_session)):
        jira_filters.record(audit_session(request), {}, "", expires_at=value.expires_at)
        return jira_filter_payload(audit_session(request))

    @router.post("/api/audits/jira")
    def create_jira_audit(
        request: Request, payload: dict = Body(...),
        value=Depends(authenticated_session),
    ):
        owner = jira_audit_owner(value.username, value.password)
        session_id = audit_session(request)
        try:
            selection = jira_filters.get(session_id)
            if selection is None:
                raise HTTPException(status_code=409, detail={"state": "no_snapshot"})
            scope = owner.resolve_filter(selection)

            def finalize(task, report):
                directory = downloads.task_dir(task.id)
                try:
                    path = owner.export(
                        report, directory / f"Jira_Weekly_Review_{task.id}.xlsx",
                    )
                    artifact = downloads.register(
                        session_id, path, Path(path).name,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                except Exception as error:
                    raise RuntimeError("export_failed") from error
                return artifact.id
            task = audits.create(
                "jira", session_id,
                lambda token, progress: owner.run(scope, token, progress),
                context=scope,
                finalizer=finalize,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail={"state": "invalid_input"}) from error
        except AuditConflictError as error:
            raise HTTPException(status_code=409, detail={"state": "audit_running"}) from error
        return _audit_task_payload(task)

    @router.get("/api/audits/jira/{audit_id}")
    def get_jira_audit(
        audit_id: str, request: Request, value=Depends(authenticated_session),
    ):
        del value
        return _get_audit_payload(audits, audit_id, audit_session(request))

    @router.post("/api/audits/jira/{audit_id}/cancel")
    def cancel_jira_audit(
        audit_id: str, request: Request, value=Depends(authenticated_session),
    ):
        del value
        return _cancel_audit(audits, audit_id, audit_session(request))

    @router.post("/api/audits/jira/{audit_id}/export")
    def export_jira_audit(
        audit_id: str, request: Request, value=Depends(authenticated_session),
    ):
        del value
        session_id = audit_session(request)
        task = _owned_task(audits, audit_id, session_id)
        if task.status != "completed" or not task.download_id:
            raise HTTPException(status_code=409, detail={"state": "invalid_state"})
        try:
            artifact = downloads.get(task.download_id, session_id)
        except DownloadNotFoundError as error:
            raise HTTPException(status_code=404, detail={"state": "download_expired"}) from error
        return {"status": task.status, "download": _download_payload(artifact)}

    return router
