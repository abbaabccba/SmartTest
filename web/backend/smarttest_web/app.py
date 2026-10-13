from __future__ import annotations

from contextlib import asynccontextmanager
from functools import lru_cache
from threading import Lock
from time import perf_counter
from uuid import uuid4
import os

from fastapi import Body, FastAPI, HTTPException, Request, Response

from core.authentication import LdapAuthenticator
from core.jira.gateway import JiraGateway
from core.jira.mapper import JiraIssueMapper
from core.jira.services.filter_service import JiraFilterService
from core.logging import smart_log

from .audit.email_history import AuditEmailHistory
from .audit.email_job import AuditEmailJob
from .audit.registry import ManualAuditRegistry
from .audit_email_api import create_router as create_audit_email_router
from .audit_http import (
    audit_task_payload as _audit_task_payload,
    session_owner as _session_owner,
)
from .background_refresh import BackgroundFactsRefresh
from .config import DatabaseSettings
from .confluence_api import create_router as create_confluence_router
from .credentials import CredentialMissingError, CredentialStoreError
from .database import ReadonlyDatabase, WebDatabase
from .downloads import DownloadArtifactService
from .downloads_api import create_router as create_downloads_router
from .jira.analytics_repository import JiraAnalyticsRepository
from .jira.analytics_tasks import JIRA_ANALYTICS_TASKS
from .jira.cache_service import JiraIssueCacheService
from .jira.issue_repository import JiraIssueRepository
from .jira_api import create_router as create_jira_router
from .jira_filter_snapshot_repository import JiraFilterSnapshotRepository
from .preferences_api import create_router as create_preferences_router
from .project_facts_api import ProjectFactsWebOwner
from .query_snapshot_repository import ConfluenceQuerySnapshotRepository
from .release_api import create_router as create_release_router
from .release_query import ProjectReleaseQueryService
from .report_workspace import ClientAuditReportOwner
from .report_workspace_api import create_router as create_report_workspace_router
from .schema import initialize_web_schema
from .service import WifiDatabaseQueries
from .session import PersistentSessionStore, default_web_database_path
from .task_manager import WEB_TASKS, ScopedTaskIndex
from .test_suite_api import create_router as create_test_suite_router
from .test_suite_repository import TestSuiteRepository


SESSION_COOKIE = "smarttest_session"


@lru_cache(maxsize=1)
def default_query_owner():
    return WifiDatabaseQueries(ReadonlyDatabase(DatabaseSettings.from_environment()))


def default_authenticator():
    return LdapAuthenticator(platform="web")


def default_jira_cache_owner(username: str, password: str):
    base_url = os.getenv("SMARTTEST_JIRA_BASE_URL", "https://jira.amlogic.com")
    gateway = JiraGateway(base_url, username, password)
    return JiraIssueCacheService(gateway, JiraIssueMapper(base_url),
                                 JiraIssueRepository(WebDatabase(default_web_database_path())))


def default_jira_audit_owner(username: str, password: str):
    from .audit.jira_adapter import WebJiraAuditOwner
    return WebJiraAuditOwner.from_credentials(username, password)


def default_jira_filter_owner(username: str, password: str):
    base_url = os.getenv("SMARTTEST_JIRA_BASE_URL", "https://jira.amlogic.com")
    gateway = JiraGateway(base_url, username, password)
    return JiraFilterService(gateway), gateway


def default_confluence_audit_owner(username: str, password: str):
    from .audit.confluence_adapter import WebConfluenceAuditOwner
    return WebConfluenceAuditOwner.from_credentials(username, password)


def create_app(query_owner=default_query_owner, report_owner=ClientAuditReportOwner.from_environment,
               project_facts_owner=ProjectFactsWebOwner, authenticator=default_authenticator,
               session_store=PersistentSessionStore, facts_refresh=BackgroundFactsRefresh,
               jira_cache_owner=default_jira_cache_owner,
               release_query_owner=ProjectReleaseQueryService,
               audit_registry=ManualAuditRegistry,
               download_service=DownloadArtifactService,
               jira_audit_owner=default_jira_audit_owner,
               confluence_audit_owner=default_confluence_audit_owner,
               jira_filter_owner=default_jira_filter_owner) -> FastAPI:
    auth = authenticator()
    sessions = session_store()
    cache_database = WebDatabase(sessions.path)
    initialize_web_schema(cache_database)
    facts = project_facts_owner()
    refresh = facts_refresh()
    snapshots = ConfluenceQuerySnapshotRepository(cache_database)
    jira_filters = JiraFilterSnapshotRepository(cache_database)
    jira_analytics = JiraAnalyticsRepository(cache_database)
    jira_analytics.interrupt_pending()
    releases = release_query_owner(cache_database)
    test_suites = TestSuiteRepository(cache_database)
    audits = audit_registry()
    downloads = download_service()
    email_history = AuditEmailHistory(cache_database)
    email_job = AuditEmailJob(email_history)
    release_tasks = ScopedTaskIndex(WEB_TASKS)

    @asynccontextmanager
    async def lifespan(_app):
        from .task_manager import close_web_tasks, open_web_tasks
        open_web_tasks()
        try:
            yield
        finally:
            email_job.close()
            audits.close()
            downloads.close()
            close_web_tasks()
    app = FastAPI(title="SmartTest Wi-Fi Database", docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)

    def set_session_cookie(request: Request, response: Response, token: str) -> None:
        secure = request.url.scheme.lower() == "https"
        response.set_cookie(
            SESSION_COOKIE,
            token,
            httponly=True,
            secure=secure,
            samesite="lax",
            max_age=sessions.ttl_seconds,
            path="/",
        )
        smart_log(
            f"Web session cookie cookie_action=set secure={str(secure).lower()} "
            f"request_id={request.state.request_id}",
            platform="web", domain="auth", source="session_cookie",
            request_id=request.state.request_id,
            extra={"cookie_action": "set", "secure": secure,
                   "request_scheme": request.url.scheme.lower(),
                   "request_id": request.state.request_id},
            emit_runtime_event=False,
        )

    def clear_session_cookie(request: Request, response: Response) -> None:
        secure = request.url.scheme.lower() == "https"
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            httponly=True,
            secure=secure,
            samesite="lax",
        )
        smart_log(
            f"Web session cookie cookie_action=clear secure={str(secure).lower()} "
            f"request_id={request.state.request_id}",
            platform="web", domain="auth", source="session_cookie",
            request_id=request.state.request_id,
            extra={"cookie_action": "clear", "secure": secure,
                   "request_scheme": request.url.scheme.lower(),
                   "request_id": request.state.request_id},
            emit_runtime_event=False,
        )

    @app.middleware("http")
    async def log_request(request: Request, call_next):
        started = perf_counter()
        request_id = request.headers.get("x-request-id", "").strip() or str(uuid4())
        request.state.request_id = request_id
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            if getattr(request.state, "renew_session_cookie", False):
                set_session_cookie(request, response, request.cookies[SESSION_COOKIE])
            response.headers["x-request-id"] = request_id
            return response
        finally:
            smart_log(
                f"{request.method} {request.url.path} {status} request_id={request_id}",
                platform="web",
                domain="web",
                level="error" if status >= 500 else "warning" if status >= 400 else "info",
                source="request",
                request_id=request_id,
                extra={"method": request.method, "path": request.url.path, "status": status,
                       "request_id": request_id,
                       "duration_ms": round((perf_counter() - started) * 1000, 3)},
                emit_runtime_event=False,
            )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    def current_session(request: Request):
        cookie_present = bool(request.cookies.get(SESSION_COOKIE, ""))
        value = sessions.get(request.cookies.get(SESSION_COOKIE, ""))
        smart_log(
            f"Web session resolve cookie_present={str(cookie_present).lower()} "
            f"session_found={str(value is not None).lower()} "
            f"avatar_present={str(bool(value and value.avatar_bytes)).lower()} "
            f"request_id={request.state.request_id}",
            platform="web", domain="auth", source="session_resolve",
            request_id=request.state.request_id,
            extra={"cookie_present": cookie_present, "session_found": value is not None,
                   "avatar_present": bool(value and value.avatar_bytes),
                   "request_id": request.state.request_id},
            emit_runtime_event=False,
        )
        if value is not None and value.cookie_renewal_required:
            request.state.renew_session_cookie = True
        return value

    def public_session(value):
        if value is None:
            return {"authenticated": False}
        return {"authenticated": True, "username": value.username,
                "displayName": value.display_name,
                "avatarUrl": "/api/auth/avatar" if value.avatar_bytes else ""}

    def establish_session(request: Request, response: Response, *, username: str,
                          password: str | None, display_name: str = "",
                          avatar_bytes: bytes = b""):
        try:
            if password is None:
                session_id = sessions.create_from_saved(username, display_name, avatar_bytes)
            else:
                session_id = sessions.create(username, password, display_name, avatar_bytes)
        except CredentialStoreError as error:
            smart_log("Persistent Web credential storage failed", platform="web", domain="auth",
                      source="session", level="error",
                      extra={"exception_type": type(error).__name__})
            raise HTTPException(
                status_code=503,
                detail={"state": "credential_store_unavailable"},
            ) from error
        old_token = request.cookies.get(SESSION_COOKIE, "")
        if old_token:
            sessions.delete(old_token)
            audits.cancel_session(_session_owner(old_token))
        set_session_cookie(request, response, session_id)
        value = sessions.get(session_id)
        return public_session(value)

    @app.post("/api/auth/login")
    def login(request: Request, response: Response, payload: dict = Body(...)):
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        try:
            _stored_username, saved_password = sessions.saved_credentials(username)
        except CredentialMissingError:
            pass
        except CredentialStoreError as error:
            raise HTTPException(
                status_code=503,
                detail={"state": "credential_store_unavailable"},
            ) from error
        else:
            if not password or password == saved_password:
                return establish_session(
                    request,
                    response,
                    username=username,
                    password=None,
                    display_name=username,
                )
        result = auth.authenticate(username, password)
        if not result.get("success"):
            code = result.get("code") if result.get("code") in {"invalid_credentials", "ldap_unavailable"} else "ldap_unavailable"
            raise HTTPException(status_code=401 if code == "invalid_credentials" else 503,
                                detail={"state": code})
        return establish_session(
            request,
            response,
            username=result["username"],
            password=password,
            display_name=result.get("display_name", ""),
            avatar_bytes=result.get("avatar_bytes", b""),
        )

    @app.post("/api/auth/client-session")
    def client_session(request: Request, response: Response, payload: dict = Body(...)):
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        if not username or not password:
            raise HTTPException(status_code=422, detail={"state": "invalid_input"})
        return establish_session(
            request,
            response,
            username=username,
            password=password,
            display_name=username,
        )

    @app.get("/api/auth/session")
    def session(request: Request):
        value = current_session(request)
        return public_session(value)

    @app.get("/api/auth/avatar")
    def avatar(request: Request):
        value = current_session(request)
        if value is None or not value.avatar_bytes:
            raise HTTPException(status_code=404, detail="Avatar not found.")
        return Response(value.avatar_bytes, media_type="image/jpeg")

    @app.post("/api/auth/logout")
    def logout(request: Request, response: Response):
        token = request.cookies.get(SESSION_COOKIE, "")
        session_id = _session_owner(token)
        downloads.clear_session(session_id)
        jira_analytics.delete_session(session_id)
        JIRA_ANALYTICS_TASKS.clear_session(session_id)
        release_tasks.clear_prefix(session_id)
        sessions.delete(token)
        audits.cancel_session(_session_owner(token))
        clear_session_cookie(request, response)
        return {"authenticated": False}

    @app.post("/api/auth/logout-all")
    def logout_all(request: Request, response: Response):
        value = current_session(request)
        if value is None:
            raise HTTPException(status_code=401, detail={"state": "unauthenticated"})
        session_id = _session_owner(request.cookies.get(SESSION_COOKIE, ""))
        downloads.clear_session(session_id)
        jira_analytics.delete_account(value.username)
        sessions.delete_all(value.username)
        JIRA_ANALYTICS_TASKS.clear_session(session_id)
        release_tasks.clear_prefix(session_id)
        request.state.renew_session_cookie = False
        clear_session_cookie(request, response)
        return {"authenticated": False}

    def authenticated_session(request: Request):
        value = current_session(request)
        if value is None:
            state = sessions.rejection_state(request.cookies.get(SESSION_COOKIE, ""))
            raise HTTPException(status_code=401, detail={"state": state or "unauthenticated"})
        return value
    app.include_router(create_test_suite_router(authenticated_session, test_suites))
    app.include_router(create_release_router(authenticated_session, sessions, cache_database,
                                             snapshots, releases, release_tasks, jira_filters,
                                             jira_cache_owner, jira_analytics, facts))
    app.include_router(create_preferences_router(authenticated_session, sessions, query_owner))
    app.include_router(create_jira_router(authenticated_session, sessions, cache_database,
                                          jira_cache_owner, jira_analytics, jira_filter_owner,
                                          jira_filters, audits, downloads, jira_audit_owner))
    app.include_router(create_confluence_router(authenticated_session, sessions, cache_database,
                                                facts, refresh, snapshots, audits, downloads,
                                                confluence_audit_owner, smart_log))
    app.include_router(create_audit_email_router(authenticated_session, email_history, email_job,
                                                 sessions, cache_database, facts,
                                                 jira_audit_owner, confluence_audit_owner))
    app.include_router(create_downloads_router(authenticated_session, downloads, sessions,
                                               cache_database, audits, confluence_audit_owner))
    app.include_router(create_report_workspace_router(authenticated_session, report_owner, downloads))

    @app.exception_handler(PermissionError)
    async def permission_error(_request, error):
        from fastapi.responses import JSONResponse
        state = "reauthentication_required" if str(error) == "reauthentication_required" else "permission_denied"
        return JSONResponse(status_code=401 if state == "reauthentication_required" else 403,
                            content={"detail": {"state": state}})
    return app
_default_app = None
_default_app_lock = Lock()


def _get_default_app():
    global _default_app
    if _default_app is None:
        with _default_app_lock:
            if _default_app is None:
                _default_app = create_app()
    return _default_app


async def app(scope, receive, send):
    """Create the default ASGI application lazily so imports never mutate app-data."""
    await _get_default_app()(scope, receive, send)
