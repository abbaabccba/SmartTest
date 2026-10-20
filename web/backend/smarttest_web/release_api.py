from __future__ import annotations

from core.async_tasks import TaskCancelled, check_cancellation

from fastapi import APIRouter
from fastapi import Depends, HTTPException, Request

from core.jira.domain import IssueDetails

from .audit_http import confluence_access, request_session_id
from .jira_http import issue_payload as _issue_payload
from .resource_access import remote_credentials_rejected
from .task_manager import WEB_TASKS, snapshot_payload


def create_router(authenticated_session, sessions, cache_database, snapshots, releases, release_tasks,
                  jira_filters, jira_cache_owner, jira_analytics, facts) -> APIRouter:
    router = APIRouter()

    def audit_session(request: Request) -> str:
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
            raise HTTPException(status_code=401, detail={"state": "invalid_credentials"}) from error
        raise error

    def downstream_refresh_error(value, error):
        if not remote_credentials_rejected(error):
            return "failed"
        sessions.invalidate_credentials(value.username)
        return "invalid_credentials"

    def access_context(request):
        return confluence_access(sessions, cache_database, request)

    def release_access(request):
        access = access_context(request)
        access.require_active()
        return access

    @router.get("/api/dashboard/releases")
    def dashboard_releases(request: Request, value=Depends(authenticated_session)):
        access = release_access(request)
        selection = snapshots.get(access.session_hash)
        if selection is None:
            raise HTTPException(status_code=409, detail={"state": "no_snapshot"})
        result = releases.dashboard(
            visible_ids=access.ids("project", "catalog"), project_ids=selection.project_ids, filters={},
        )
        return {**result, "dataState": "ready", "querySnapshot": {"scope": selection.scope, "updatedAt": selection.updated_at}, "syncState": "idle"}

    def refresh_jira_release_scope(value, project_ids):
        if not project_ids:
            return
        quoted = ",".join(f'"{str(project_id).replace(chr(34), chr(92) + chr(34))}"' for project_id in project_ids)
        query = f'"Project ID" in ({quoted})'
        owner = resolve_jira_cache(value)
        page = 0
        while True:
            check_cancellation()
            refreshed = owner.refresh_release_issues(query, page=page)
            page_size = max(1, int(refreshed.get("page_size") or 100))
            if (page + 1) * page_size >= int(refreshed.get("total") or 0):
                break
            page += 1

    def applied_jira_filters(selection, username):
        result = {key: tuple(values) for key, values in selection.filters.items()}
        if result.get("currentUser"):
            result["currentUser"] = (username,)
        return result

    @router.post("/api/dashboard/releases/sync")
    def sync_dashboard_releases(request: Request, value=Depends(authenticated_session)):
        access = release_access(request)
        selection = snapshots.get(access.session_hash)
        if selection is None:
            raise HTTPException(status_code=409, detail={"state": "no_snapshot"})

        def synchronize(token, _progress):
            try:
                facts.sync_details(
                    access, value.password, filters={"project id": selection.project_ids}, search="",
                    parent_task_id=token.task_id,
                )
                refresh_jira_release_scope(value, selection.project_ids)
                return "ready"
            except TaskCancelled:
                raise
            except Exception as error:
                return downstream_refresh_error(value, error)
        future = WEB_TASKS.submit_coordinator("release-dashboard-sync", synchronize)
        task_id = WEB_TASKS.task_id(future)
        release_tasks.replace((access.session_hash, "dashboard"), task_id)
        result = releases.dashboard(
            visible_ids=access.ids("project", "catalog"), project_ids=selection.project_ids,
            filters={},
        )
        return {**result, "dataState": "ready", "querySnapshot": {"scope": selection.scope, "updatedAt": selection.updated_at},
                "syncState": "loading", "taskId": task_id}

    @router.get("/api/dashboard/releases/sync/{task_id}")
    def dashboard_release_sync(task_id: str, request: Request, value=Depends(authenticated_session)):
        del value
        access = release_access(request)
        if not release_tasks.owns((access.session_hash, "dashboard"), task_id):
            raise HTTPException(status_code=404, detail={"state": "not_found"})
        snapshot = WEB_TASKS.snapshot(task_id)
        return {"id": task_id, **snapshot_payload(snapshot),
                "syncState": WEB_TASKS.result(task_id) if snapshot.state == "completed" else snapshot.state}

    @router.get("/api/dashboard/releases/{project_id}")
    def dashboard_release(project_id: str, request: Request, value=Depends(authenticated_session)):
        del value
        access = release_access(request)
        selection = snapshots.get(access.session_hash)
        if selection is None or project_id not in selection.project_ids:
            raise HTTPException(status_code=404, detail={"state": "not_found"})
        result = releases.dashboard(
            visible_ids=access.ids("project", "catalog"), project_ids=(project_id,), filters={},
        )
        if not result.get("releases"):
            raise HTTPException(status_code=404, detail={"state": "not_found"})
        return result["releases"][0]

    @router.get("/api/jira/release-issues")
    def jira_release_issues(request: Request, value=Depends(authenticated_session)):
        access = release_access(request)
        try:
            page = int(request.query_params.get("page", "0"))
            page_size = int(request.query_params.get("pageSize", "50"))
            if page < 0 or not 1 <= page_size <= 200:
                raise ValueError
        except ValueError as error:
            raise HTTPException(status_code=422, detail={"state": "invalid_pagination"}) from error
        confluence = snapshots.get(access.session_hash)
        jira = jira_filters.get(access.session_hash)
        if confluence is None or jira is None:
            raise HTTPException(status_code=409, detail={"state": "no_snapshot"})
        filters = applied_jira_filters(jira, value.username)
        project_ids = confluence.project_ids
        snapshot_mode = request.query_params.get("snapshot", "")
        if snapshot_mode == "dashboard":
            requested_project = request.query_params.get("projectId", "")
            if not requested_project and len(project_ids) == 1:
                requested_project = project_ids[0]
            if requested_project not in project_ids:
                raise HTTPException(status_code=404, detail={"state": "not_found"})
            dashboard = releases.dashboard(visible_ids=access.ids("project", "catalog"),
                                           project_ids=(requested_project,), filters={})
            rows = dashboard.get("releases") or []
            if not rows:
                raise HTTPException(status_code=409, detail={"state": "stale_snapshot"})
            project_ids = (requested_project,)
            filters.update({
                "_scopeRelease": ("" if rows[0].get("releaseName") == "版本未填写" else rows[0].get("releaseName", ""),),
                "_openOnly": True,
            })
        result = releases.issues(
            visible_ids=access.ids("project", "catalog"), project_ids=project_ids,
            filters=filters, page=page, page_size=page_size,
        )
        return {**result, "dataState": "ready", "querySnapshot": {"scope": jira.scope, "updatedAt": jira.revision}, "syncState": "idle"}

    @router.post("/api/jira/release-issues/sync")
    def sync_jira_release_issues(request: Request, value=Depends(authenticated_session)):
        access = release_access(request)
        confluence = snapshots.get(access.session_hash)
        jira = jira_filters.get(access.session_hash)
        if confluence is None or jira is None:
            raise HTTPException(status_code=409, detail={"state": "no_snapshot"})

        def synchronize(_token, _progress):
            try:
                refresh_jira_release_scope(value, confluence.project_ids)
                return "ready"
            except TaskCancelled:
                raise
            except Exception as error:
                return downstream_refresh_error(value, error)
        future = WEB_TASKS.submit_coordinator("jira-release-sync", synchronize)
        task_id = WEB_TASKS.task_id(future)
        release_tasks.replace((access.session_hash, "jira"), task_id)
        result = releases.issues(
            visible_ids=access.ids("project", "catalog"), project_ids=confluence.project_ids,
            filters=applied_jira_filters(jira, value.username), page=0, page_size=50,
        )
        return {**result, "dataState": "ready", "querySnapshot": {"scope": jira.scope, "updatedAt": jira.revision},
                "syncState": "loading", "taskId": task_id}

    @router.get("/api/jira/release-issues/sync/{task_id}")
    def jira_release_sync(task_id: str, request: Request, value=Depends(authenticated_session)):
        del value
        access = release_access(request)
        if not release_tasks.owns((access.session_hash, "jira"), task_id):
            raise HTTPException(status_code=404, detail={"state": "not_found"})
        snapshot = WEB_TASKS.snapshot(task_id)
        return {"id": task_id, **snapshot_payload(snapshot),
                "syncState": WEB_TASKS.result(task_id) if snapshot.state == "completed" else snapshot.state}

    @router.get("/api/jira/release-issues/{issue_key}")
    def jira_release_issue(issue_key: str, request: Request, value=Depends(authenticated_session)):
        access = release_access(request)
        confluence = snapshots.get(access.session_hash)
        jira = jira_filters.get(access.session_hash)
        if confluence is None or jira is None:
            raise HTTPException(status_code=404, detail={"state": "not_found"})
        release_detail = releases.issue_detail(
            issue_key, visible_ids=access.ids("project", "catalog"), project_ids=confluence.project_ids,
            filters=applied_jira_filters(jira, value.username),
        )
        if release_detail is None:
            raise HTTPException(status_code=404, detail={"state": "not_found"})
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
        current = _issue_payload(issue, names)
        return {**release_detail, "details": current["details"]}

    return router
