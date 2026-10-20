from __future__ import annotations

from datetime import date
from time import perf_counter

from fastapi import APIRouter
from fastapi import Body, Depends, HTTPException, Request

from core.confluence.audit import manual_audit_period

from .audit.registry import AuditConflictError
from .audit_http import (
    audit_task_payload as _audit_task_payload,
    cancel_audit as _cancel_audit,
    confluence_access,
    get_audit_payload as _get_audit_payload,
    request_session_id,
)
from .query_snapshot_repository import normalize_project_filters
from .resource_access import remote_credentials_rejected


def create_router(authenticated_session, sessions, cache_database, facts, refresh, snapshots, audits,
                  downloads, confluence_audit_owner, smart_log) -> APIRouter:
    router = APIRouter()

    def access_context(request):
        return confluence_access(sessions, cache_database, request)

    def resolve_project_facts_owner():
        return facts

    def audit_session(request):
        return request_session_id(request)

    @router.delete("/api/confluence/projects/{project_id}")
    def invalidate_confluence_project(project_id: str, request: Request, value=Depends(authenticated_session)):
        facts.invalidate_project(project_id, access_context(request))
        return {"invalidated": project_id}

    def record_query_snapshot(access, value, filters, search, result):
        if result.get("state") in {"ready", "partial_success"}:
            snapshots.record(
                access.session_hash, filters, search,
                tuple(dict.fromkeys(
                    row.get("source_identity") or row.get("identity") or row.get("project_id")
                    for row in result.get("projects", ())
                )),
                getattr(facts, "facts_version", lambda: "")(), expires_at=value.expires_at,
            )

    def downstream_refresh_error(value, error):
        if not remote_credentials_rejected(error):
            return "failed"
        sessions.invalidate_credentials(value.username)
        return "invalid_credentials"

    @router.get("/api/confluence/project-facts/status")
    def confluence_project_facts_status(request: Request, value=Depends(authenticated_session)):
        del value
        return refresh.status_for(access_context(request).session_hash)

    @router.get("/api/confluence/project-facts")
    def confluence_project_facts(request: Request, owner=Depends(resolve_project_facts_owner),
                                 value=Depends(authenticated_session)):
        api_started = perf_counter()
        access = access_context(request)
        filters = normalize_project_filters({
            key.removeprefix("field."): tuple(value for value in request.query_params.getlist(key) if str(value).strip())
            for key, _value in request.query_params.multi_items()
            if key.startswith("field.")
        })
        search = request.query_params.get("search", "")
        replay_snapshot = request.query_params.get("snapshot") == "1"
        reset_snapshot = request.query_params.get("reset") == "1"
        if replay_snapshot:
            selection = snapshots.get(access.session_hash)
            if selection is not None:
                filters, search = selection.filters, selection.search
        elif reset_snapshot:
            filters, search = {}, ""
        pagination = {}
        if "page" in request.query_params or "pageSize" in request.query_params:
            try:
                pagination = {
                    "page": int(request.query_params.get("page", "0")),
                    "page_size": int(request.query_params.get("pageSize", "100")),
                }
            except ValueError as error:
                raise HTTPException(status_code=422, detail={"state": "invalid_pagination"}) from error

        def query_current():
            return owner.query(
                access, filters=filters, search=search, **pagination,
            )
        result = query_current()
        refresh_state = refresh.state_for(access.session_hash)
        result = _with_sync_state(result, refresh_state)
        if result.get("state") == "no_snapshot" and not value.password:
            result = {**result, "state": "reauthentication_required"}
        selection = snapshots.get(access.session_hash)
        result = {**result, "sync": refresh.status_for(access.session_hash),
                  "querySnapshot": None if selection is None else {
                      "filters": selection.filters, "search": selection.search,
                      "revision": selection.updated_at,
                  }}
        smart_log("Confluence filter API timing", platform="web", domain="framework", source="project_facts", emit_runtime_event=False,
                  extra={"stage": "filter.api_total", "duration_ms": round((perf_counter() - api_started) * 1000, 3),
                         "request_state": str(result.get("state") or ""), "refresh_state": refresh_state,
                         "credential_present": bool(value.password),
                         "project_count": len(result.get("projects") or ())})
        return result

    @router.put("/api/confluence/filter-snapshot")
    def apply_confluence_filter_snapshot(request: Request, payload: dict = Body(...),
                                         owner=Depends(resolve_project_facts_owner),
                                         value=Depends(authenticated_session)):
        access = access_context(request)
        requested = payload.get("filters") or {}
        search = payload.get("search") or ""
        if (not isinstance(requested, dict) or not isinstance(search, str)
                or any(not isinstance(values, list) or any(not isinstance(item, str) for item in values)
                       for values in requested.values())):
            raise HTTPException(status_code=422, detail={"state": "invalid_filters"})
        filters = normalize_project_filters({
            str(key): tuple(item for item in values if item.strip()) for key, values in requested.items()
        })
        result = owner.query(access, filters=filters, search=search)
        acquisition_required = (
            result.get("state") != "ready" or result.get("detailState", "ready") != "ready"
        )
        if acquisition_required and value.password:
            def publish_result():
                completed = owner.query(access, filters=filters, search=search)
                record_query_snapshot(access, value, filters, search, completed)

            refresh.start_details(
                owner, access, value.password, filters=filters, search=search,
                on_error=lambda error: downstream_refresh_error(value, error),
                on_success=publish_result,
            )
        else:
            record_query_snapshot(access, value, filters, search, result)
        selection = snapshots.get(access.session_hash)
        sync = refresh.status_for(access.session_hash)
        result = _with_sync_state(result, sync["state"])
        return {**result, "querySnapshot": None if selection is None else {
            "filters": selection.filters, "search": selection.search, "revision": selection.updated_at,
            "projectIds": list(selection.project_ids),
        }, "sync": sync}

    @router.delete("/api/confluence/filter-snapshot")
    def reset_confluence_filter_snapshot(request: Request, owner=Depends(resolve_project_facts_owner),
                                         value=Depends(authenticated_session)):
        access = access_context(request)
        result = owner.query(access, filters={}, search="")
        record_query_snapshot(access, value, {}, "", result)
        selection = snapshots.get(access.session_hash)
        return {**result, "querySnapshot": {
            "filters": selection.filters, "search": selection.search, "revision": selection.updated_at,
            "projectIds": list(selection.project_ids),
        }, "sync": refresh.status_for(access.session_hash)}

    @router.post("/api/confluence/project-facts/cancel")
    def cancel_confluence_project_sync(request: Request, value=Depends(authenticated_session)):
        key = audit_session(request)
        return {"cancelled": refresh.cancel(key), "sync": refresh.status_for(key)}

    @router.post("/api/audits/confluence")
    def create_confluence_audit(
        request: Request, payload: dict = Body(...),
        value=Depends(authenticated_session),
    ):
        access = access_context(request)
        selection = snapshots.get(access.session_hash)
        if selection is None:
            raise HTTPException(status_code=409, detail={"state": "no_snapshot"})
        filters, search = selection.filters, selection.search
        owner = confluence_audit_owner(access, value.password)
        try:
            period = manual_audit_period(
                date.fromisoformat(str(payload.get("startDate") or "")),
                date.fromisoformat(str(payload.get("endDate") or "")),
            )

            def run_review(token, progress):
                resolved = owner.resolve({
                    "projectIds": list(selection.project_ids),
                    "startDate": payload.get("startDate"),
                    "endDate": payload.get("endDate"),
                })
                return owner.run(resolved, token, progress)
            task = audits.create(
                "confluence", audit_session(request),
                run_review,
                context=period,
                validate=access.require_active,
            )
        except ValueError as error:
            raise HTTPException(status_code=422, detail={"state": "invalid_input"}) from error
        except AuditConflictError as error:
            raise HTTPException(status_code=409, detail={"state": "audit_running"}) from error
        return _audit_task_payload(task)

    @router.get("/api/audits/confluence/{audit_id}")
    def get_confluence_audit(
        audit_id: str, request: Request, value=Depends(authenticated_session),
    ):
        del value
        return _get_audit_payload(audits, audit_id, audit_session(request))

    @router.post("/api/audits/confluence/{audit_id}/cancel")
    def cancel_confluence_audit(
        audit_id: str, request: Request, value=Depends(authenticated_session),
    ):
        del value
        return _cancel_audit(audits, audit_id, audit_session(request))

    return router


def _with_sync_state(result, sync_state):
    result = {**result, "dataState": "ready" if result.get("state") in {"ready", "partial_success"} else "none"}
    if sync_state in {"failed", "invalid_credentials", "loading"}:
        return {**result, "state": sync_state}
    return result
