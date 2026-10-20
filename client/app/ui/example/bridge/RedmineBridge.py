from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from concurrent.futures import Future, TimeoutError as FutureTimeoutError
from threading import Event, Thread

from PySide6.QtCore import (
    QCoreApplication,
    QObject,
    Property,
    QT_TRANSLATE_NOOP,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices
from PySide6.QtCore import QUrl

from core.browser_automation import BrowserRuntime, SupportedBrowserNotInstalledError
from core.jira.gateway import JiraGateway
from core.jira.issue_command_service import IssueCommandService
from core.jira.services.create_schema_service import JiraCreateSchemaService
from core.logging import smart_log
from core.tools.SmartHome.redmine.auth import RedmineAuthService
from core.tools.SmartHome.redmine.attachment_transfer import RedmineAttachmentTransfer
from core.tools.SmartHome.redmine.clone_controller import RedmineCloneController
from core.tools.SmartHome.redmine.collector import RedmineContextCollector, project_options
from core.tools.SmartHome.redmine.issue_controller import RedmineIssueController
from core.tools.SmartHome.redmine.issue_analysis_loader import IssueAnalysisLoader, analysis_work_count, consolidate_context
from core.tools.SmartHome.redmine.overdue import load_redmine_people
from core.tools.SmartHome.redmine.query import RedmineQuery, parse_terms
from core.tools.SmartHome.redmine import context_store
from core.tools.SmartHome.redmine.models import AuthResult, AuthState, Credential
from client.app.ui.example.bridge.ToolBridge import amlogic_employees, employee_department, load_tool_access
from .desktop_tasks import DESKTOP_TASKS
from core.async_tasks import TaskCancelled


async def _native_query(collector, filters):
    tracker = str(filters.get("type", "") or "").strip()
    if tracker and not tracker.isdecimal():
        metadata = await collector.collect_filter_metadata(project=str(filters.get("project", "") or ""))
        matches = [value for label, value in metadata.items() if label.casefold() == tracker.casefold()]
        if len(matches) != 1:
            raise ValueError(f"Redmine tracker option is unavailable: {tracker}")
        tracker = matches[0]
    return RedmineQuery(
        project=str(filters.get("project", "") or ""),
        status=str(filters.get("status", "") or ""),
        tracker=tracker,
        subject=str(filters.get("subject", "") or ""),
        text=str(filters.get("text", "") or ""),
    )


class _ManagedCoroutineFuture:
    def __init__(self, manager, manager_future, coroutine_future):
        self._manager = manager
        self._manager_future = manager_future
        self._coroutine_future = coroutine_future

    def cancel(self):
        self._coroutine_future.cancel()
        return self._manager.cancel(self._manager.task_id(self._manager_future))

    def result(self, timeout=None):
        return self._manager_future.result(timeout=timeout)

    def add_done_callback(self, callback):
        self._manager_future.add_done_callback(lambda _future: callback(self))

    def snapshot(self):
        return self._manager.snapshot(self._manager.task_id(self._manager_future))


class _AsyncLoopWorker:
    """Owns the browser's loop; its finite operations are bounded by the desktop task manager."""
    def __init__(self, *, manager=DESKTOP_TASKS):
        self.loop = asyncio.new_event_loop()
        self._manager = manager
        self._ready = Event()
        self._thread = Thread(target=self._run, name="redmine-browser", daemon=True)
        self._thread.start()
        self._ready.wait()

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def submit(self, coroutine) -> Future:
        coroutine_future = asyncio.run_coroutine_threadsafe(coroutine, self.loop)

        def wait(token, _progress):
            try:
                while True:
                    token.raise_if_cancelled()
                    try:
                        return coroutine_future.result(timeout=0.05)
                    except FutureTimeoutError:
                        continue
            except TaskCancelled:
                coroutine_future.cancel()
                raise

        manager_future = self._manager.submit("redmine-browser", wait)
        return _ManagedCoroutineFuture(self._manager, manager_future, coroutine_future)

    def stop(self, timeout=5):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            return False
        if not self.loop.is_closed():
            self.loop.close()
        return True


class RedmineBridge(QObject):
    changed = Signal()
    issueRowsChanged = Signal()
    issueSelectionChanged = Signal()
    cloneDraftFieldChanged = Signal(str, "QVariantMap")
    cloneInvalidFieldRequested = Signal(str, str)
    credentialsRequired = Signal()
    verificationRequired = Signal()
    supportedBrowserMissing = Signal()
    resultReady = Signal(int, object)
    dataReady = Signal(int, object)
    dataProgressReady = Signal(int, int, str)
    projectsReady = Signal(int, object)
    cloneReady = Signal(int, int, str, object)

    def __init__(
        self,
        auth_bridge,
        *,
        runtime=None,
        service_factory=None,
        clone_checker=None,
        worker=None,
    ):
        super().__init__()
        self._auth = auth_bridge
        self._runtime = runtime or BrowserRuntime()
        self._service_factory = service_factory
        self._clone_checker = clone_checker
        self._worker = worker or _AsyncLoopWorker()
        self._service = None
        self._login_future = None
        self._data_future = None
        self._projects_future = None
        self._data_operation_kind = ""
        self._pending_detail_issue_id = ""
        self._pending_filters = None
        self._submitted_filters = None
        self._refresh_after_search = False
        self._active_search_filter_requested = False
        self._generation = 0
        self._data_generation = 0
        self._projects_generation = 0
        self._closed = False
        self._state = AuthState.IDLE
        self._status = self.tr("Ready to sign in to Redmine.")
        self._account = ""
        self._auth_account = str(getattr(auth_bridge, "username", "") or "").strip()
        self._issue_controller = RedmineIssueController(
            all_projects=self.tr("All projects"),
        )
        self._data_status = self.tr("Redmine data is not loaded.")
        self._data_loading = False
        self._data_loaded = 0
        self._data_total = 0
        self._project_options = []
        self._projects_loading = False
        self._projects_ready = False
        self._projects_status = self.tr("Projects are not loaded.")
        self._watched_issue_text = ""
        self._watched_issue_error = ""
        self._batch_future = None
        self._batch_done_event = None
        self._jira_client = None
        self._jira_create_service = None
        self._jira_schema_service = None
        self._batch_controller = RedmineCloneController(
            self._issue_controller,
            jira_dependencies=self._jira_dependencies,
            account=lambda: str(getattr(self._auth, "username", "") or "").strip(),
            identity=self._jira_identity,
            load_detail=self._load_source_detail,
            download_attachments=self._download_source_attachments,
            update_source_subject=self._update_source_subject,
            task_manager=DESKTOP_TASKS,
        )
        self.resultReady.connect(self._apply)
        self.dataReady.connect(self._apply_data)
        self.dataProgressReady.connect(self._apply_data_progress)
        self.projectsReady.connect(self._apply_projects)
        self.cloneReady.connect(self._apply_clone_result)
        auth_bridge.authChanged.connect(self._on_auth_changed)

    @Slot()
    def _on_auth_changed(self):
        next_account = str(getattr(self._auth, "username", "") or "").strip()
        if next_account == self._auth_account:
            return
        old_account = self._account or self._auth_account
        self._auth_account = next_account
        self._generation += 1
        self._data_generation += 1
        self._projects_generation += 1
        if self._login_future is not None:
            self._login_future.cancel()
            self._login_future = None
        if self._data_future is not None:
            self._data_future.cancel()
            self._data_future = None
        for name in ("_projects_future",):
            future = getattr(self, name)
            if future is not None:
                future.cancel()
                setattr(self, name, None)
        service, self._service = self._service, None
        if service is not None or old_account:
            self._worker.submit(self._close_account_flow(service, old_account))
        self._clone_checker = None
        self._jira_client = None
        self._jira_create_service = None
        self._jira_schema_service = None
        self._reset_batch(cancel_future=True)
        self._pending_filters = None
        self._submitted_filters = None
        self._pending_detail_issue_id = ""
        self._refresh_after_search = False
        self._active_search_filter_requested = False
        self._state = AuthState.IDLE
        self._account = ""
        self._issue_controller.reset()
        self._data_status = self.tr("Redmine data is not loaded.")
        self._data_loading = False
        self._data_loaded = 0
        self._data_total = 0
        self._project_options = []
        self._projects_loading = False
        self._projects_ready = False
        self._projects_status = self.tr("Projects are not loaded.")
        self._emit_issue_projection()
        self.changed.emit()

    async def _close_account_flow(self, service, account):
        if service is not None:
            try:
                await service.close()
            except Exception:
                pass
        if account:
            try:
                await self._runtime.close_context("amlogic_redmine", account)
            except Exception:
                pass

    state = Property(str, lambda self: self._state.value, notify=changed)
    statusText = Property(str, lambda self: self._status, notify=changed)
    account = Property(str, lambda self: self._account, notify=changed)
    loading = Property(bool, lambda self: self._state is AuthState.SIGNING_IN, notify=changed)
    dataLoading = Property(bool, lambda self: self._data_loading, notify=changed)
    dataStatusText = Property(str, lambda self: self._data_status, notify=changed)
    dataLoaded = Property(int, lambda self: self._data_loaded, notify=changed)
    dataTotal = Property(int, lambda self: self._data_total, notify=changed)
    quickViews = Property("QVariantList", lambda self: [
        {"id": "my_assigned", "label": self.tr("Issues assigned to me")},
        {"id": "watched", "label": self.tr("Watched issues")},
    ], notify=changed)
    activeQuickViewId = Property(str, lambda self: self._issue_controller.active_view_id, notify=changed)
    watchedIssueText = Property(str, lambda self: self._watched_issue_text, notify=changed)
    watchedIssueError = Property(str, lambda self: self._watched_issue_error, notify=changed)
    projectOptions = Property(
        "QVariantList",
        lambda self: [{"id": "", "label": self.tr("All projects")}, *list(self._project_options)],
        notify=changed,
    )
    projectsLoading = Property(bool, lambda self: self._projects_loading, notify=changed)
    projectsReadyState = Property(bool, lambda self: self._projects_ready, notify=changed)
    projectsStatusText = Property(str, lambda self: self._projects_status, notify=changed)
    searchLoading = Property(bool, lambda self: self._data_loading and self._data_operation_kind == "search", notify=changed)
    searchCanCancel = Property(bool, lambda self: self._data_loading and self._data_operation_kind == "search" and self._data_future is not None, notify=changed)
    projectFilterLabels = Property("QVariantList", lambda self: list(self._issue_controller.project_filter_labels), notify=changed)
    filters = Property(
        "QVariantMap",
        lambda self: self._submitted_filters
        if self._submitted_filters is not None
        else self._issue_controller.filters,
        notify=changed,
    )
    issueRows = Property("QVariantList", lambda self: list(self._issue_controller.issue_rows), notify=issueRowsChanged)
    selectedIssue = Property("QVariantMap", lambda self: self._issue_controller.selected_issue, notify=issueSelectionChanged)
    actionableIssues = Property("QVariantList", lambda self: list(self._issue_controller.actionable_issues), notify=issueRowsChanged)
    cloneSelectionMode = Property(bool, lambda self: self._batch_controller.status.state == "selecting", notify=changed)
    cloneSelectedIds = Property("QVariantList", lambda self: list(self._batch_controller.status.selected_ids), notify=changed)
    cloneDrafts = Property(
        "QVariantList",
        lambda self: self._localized_clone_drafts(
            self._batch_controller.draft_payloads()
        ),
        notify=changed,
    )
    cloneBatchState = Property(str, lambda self: self._batch_controller.status.state, notify=changed)
    cloneBatchLoaded = Property(int, lambda self: self._batch_controller.status.loaded, notify=changed)
    cloneBatchTotal = Property(int, lambda self: self._batch_controller.status.total, notify=changed)
    cloneBatchError = Property(str, lambda self: self._batch_controller.status.error, notify=changed)
    firstInvalidIssueId = Property(str, lambda self: self._batch_controller.status.first_invalid_issue_id, notify=changed)
    firstInvalidFieldId = Property(str, lambda self: self._batch_controller.status.first_invalid_field_id, notify=changed)

    def _localized_clone_drafts(self, drafts):
        projected = []
        for draft in drafts:
            item = dict(draft)
            item["attachmentWarnings"] = [
                {
                    **warning,
                    "attachmentWarningText": self._attachment_warning_text(
                        warning
                    ),
                }
                for warning in draft.get("attachmentWarnings", ())
            ]
            projected.append(item)
        return projected

    def _attachment_warning_text(self, warning):
        args = warning.get("reasonArgs") or {}
        filename = str(warning.get("filename") or "")
        messages = {
            "attachment_oversized": (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge",
                    "Attachment %1 is %2 bytes; Jira limit is %3 bytes.",
                ),
                (
                    filename,
                    args.get("actual_bytes", warning.get("size") or 0),
                    args.get("limit_bytes", 0),
                ),
            ),
            "jira_attachments_disabled": (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge",
                    "Jira attachments are disabled for %1.",
                ),
                (filename,),
            ),
            "source_url_missing": (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge",
                    "Attachment source URL is unavailable for %1.",
                ),
                (filename,),
            ),
            "source_http_error": (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge",
                    "Attachment download failed for %1 (HTTP %2).",
                ),
                (filename, args.get("status", "")),
            ),
            "source_download_failed": (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge",
                    "Attachment download failed for %1: %2",
                ),
                (filename, args.get("detail", "")),
            ),
            "source_file_invalid": (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge",
                    "Attachment source is invalid for %1.",
                ),
                (filename,),
            ),
            "jira_existing_size_conflict": (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge",
                    "Jira already has %1 with a different size.",
                ),
                (filename,),
            ),
            "jira_list_failed": (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge",
                    "Could not check Jira attachments for %1: %2",
                ),
                (filename, args.get("detail", "")),
            ),
            "jira_upload_failed": (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge",
                    "Attachment upload failed for %1: %2",
                ),
                (filename, args.get("detail", "")),
            ),
            "upload_cancelled": (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge",
                    "Attachment upload was cancelled for %1.",
                ),
                (filename,),
            ),
            "source_downloader_unavailable": (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge",
                    "Attachment downloader is unavailable for %1.",
                ),
                (filename,),
            ),
            "jira_sync_failed": (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge",
                    "Jira attachment synchronization failed for %1: %2",
                ),
                (filename, args.get("detail", "")),
            ),
            "temp_cleanup_failed": (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge",
                    "Temporary attachment cleanup failed: %1",
                ),
                (args.get("detail", ""),),
            ),
        }
        source, values = messages.get(
            warning.get("reasonCode"),
            (
                QT_TRANSLATE_NOOP(
                    "RedmineBridge", "Attachment warning for %1."
                ),
                (filename,),
            ),
        )
        return _translated_args(
            QCoreApplication.translate("RedmineBridge", source), *values
        )

    def _load_cached_view(self):
        self._issue_controller.set_account(self._account)
        self._issue_controller.activate_view("")
        if not self._issue_controller.load_cached():
            return
        self._data_status = self.tr("Redmine data loaded.")
        self._emit_issue_projection()
        self.changed.emit()

    def _emit_issue_projection(self):
        self._batch_controller.reconcile_selection(
            self._issue_controller.issue_rows
        )
        self.issueRowsChanged.emit()
        self.issueSelectionChanged.emit()

    def _checker(self):
        if self._clone_checker is not None:
            return self._clone_checker
        _client, create_service, _schema_service = self._jira_dependencies()
        if create_service is None:
            return None
        self._clone_checker = _RedmineCloneChecker(create_service)
        return self._clone_checker

    def _jira_dependencies(self):
        if self._jira_client is not None:
            return self._jira_client, self._jira_create_service, self._jira_schema_service
        credentials = self._auth.runtime_credentials()
        if credentials is None:
            return None, None, None
        username, password = credentials.username, credentials.password
        base_url = os.getenv("SMARTTEST_JIRA_BASE_URL", "https://jira.amlogic.com")
        self._jira_client = JiraGateway(base_url, username, password)
        self._jira_create_service = IssueCommandService(
            self._jira_client,
            browse_base_url=base_url,
        )
        self._jira_schema_service = JiraCreateSchemaService(self._jira_client)
        return self._jira_client, self._jira_create_service, self._jira_schema_service

    def _jira_identity(self, account, current_user):
        personnel = load_tool_access(
            Path(__file__).resolve().parents[5] / "core" / "config" / "personnel.json"
        )
        display_names = {
            str(employee.get("account") or "").strip(): str(
                employee.get("display_name") or ""
            ).strip()
            for employee in amlogic_employees(personnel)
            if str(employee.get("account") or "").strip()
        }
        reporter = str(current_user.get("account") or "").strip()
        display_name = str(current_user.get("display_name") or "").strip()
        if reporter and display_name:
            display_names[reporter] = display_name
        return employee_department(personnel, account), display_names

    async def _load_source_detail(self, source):
        service = await self._service_for(self._account)
        async with self._operation_page(service) as page:
            return await RedmineContextCollector(
                page, account=self._account
            ).collect_issue_detail(source.item, project=source.project)

    async def _download_source_attachments(
        self, attachments, metadata, *, duplicate_filenames
    ):
        service = await self._service_for(self._account)
        async with self._operation_page(service) as page:
            return await _download_redmine_attachments(
                page,
                attachments,
                metadata,
                duplicate_filenames=duplicate_filenames,
            )

    async def _update_source_subject(self, issue_id, issue_key):
        service = await self._service_for(self._account)
        async with self._operation_page(service) as page:
            return await RedmineContextCollector(
                page, account=self._account
            ).update_issue_subject(issue_id, issue_key)

    def _check_clone_status(self, rows, *, progress_base: int = 0, progress_total: int | None = None, emit_progress: bool = True):
        checker = self._checker()
        if checker is None:
            smart_log("[REDMINE_LOAD] clone finished", domain="tool", source="RedmineBridge", level="info", extra={"checked": 0, "total": len(rows), "skipped": True})
            return None
        def on_progress(loaded, total, _label):
            self._emit_data_progress(loaded, total, "clone")
            smart_log("[REDMINE_LOAD] clone progress", domain="tool", source="RedmineBridge", level="debug", extra={"loaded": loaded, "total": total})
        try:
            result = checker.check_many(rows, progress_callback=on_progress if emit_progress else None, progress_base=progress_base, progress_total=progress_total)
            smart_log("[REDMINE_LOAD] clone finished", domain="tool", source="RedmineBridge", level="info", extra={"checked": len(rows), "total": progress_total or progress_base + len(rows), "matches": len(result)})
            return result
        except Exception as exc:
            smart_log("[REDMINE_LOAD] clone failure", domain="tool", source="RedmineBridge", level="warning", extra={"checked": 0, "total": len(rows), "error_type": type(exc).__name__})
            return None

    async def _service_for(self, account):
        if self._service is None:
            if self._service_factory:
                self._service = self._service_factory(account)
            else:
                session = await self._runtime.context("amlogic_redmine", account)
                self._service = RedmineAuthService(session)
        return self._service

    @asynccontextmanager
    async def _operation_page(self, service):
        operation_page = getattr(service, "operation_page", None)
        if operation_page is not None:
            async with operation_page() as page:
                yield page
            return
        yield service.page

    async def _enrich_issue_context(self, page, context, filters, *, progress=True):
        context = consolidate_context(context)
        analysis_total = analysis_work_count(context)
        planned_clone_rows = list(
            self._issue_controller.enrichment_projection(
                context, filters
            ).issue_rows
        )
        total_work = analysis_total + len(planned_clone_rows)
        if progress:
            self._emit_data_progress(0, total_work, "analysis")
        aml_names, _departments = load_redmine_people(Path(__file__).resolve().parents[5] / "core" / "config" / "personnel.json")
        loader = IssueAnalysisLoader(
            page,
            max_concurrency=int(os.getenv("SMARTTEST_REDMINE_DETAIL_CONCURRENCY", "6")),
            progress_callback=(lambda done, _total, _label: self._emit_data_progress(done, total_work, "analysis")) if progress else None,
        )
        context = await loader.analyze(context, aml_names=aml_names)
        selected = self._issue_controller.enrichment_projection(
            context, filters
        ).source
        if progress:
            self._emit_data_progress(analysis_total, total_work, "clone")
        clone_status = self._check_clone_status(planned_clone_rows, progress_base=analysis_total, progress_total=total_work, emit_progress=progress)
        return context, selected.project.identifier if selected.project and selected.item else "", selected.detail, filters, clone_status

    def _launch(self, operation):
        if self._closed or self._state is AuthState.SIGNING_IN:
            return
        self._generation += 1
        generation = self._generation
        self._state = AuthState.SIGNING_IN
        self._status = self.tr("Signing in to Redmine...")
        self.changed.emit()
        future = self._worker.submit(operation())
        self._login_future = future

        def finished(future):
            try:
                result = future.result()
            except Exception as exc:
                if isinstance(exc, SupportedBrowserNotInstalledError):
                    self.supportedBrowserMissing.emit()
                result = AuthResult(AuthState.FAILED, username=self._account, reason="operation_exception")
                smart_log(
                    "[REDMINE_AUTH] login failure",
                    domain="tool",
                    source="RedmineBridge",
                    level="warning",
                    extra={"state": "failed", "reason": result.reason, "error_type": type(exc).__name__},
                )
            self.resultReady.emit(generation, result)

        future.add_done_callback(finished)

    @Slot(int, object)
    def _apply(self, generation, result):
        if self._closed or generation != self._generation:
            return
        self._login_future = None
        self._state = result.state
        self._account = result.username or self._account
        self._issue_controller.set_account(self._account)
        reason_status = {
            "incorrect_verification_code": self.tr(
                "The verification code was rejected. Enter the latest code from your phone."
            ),
            "verification_required": self.tr("Enter the mobile verification code."),
            "credentials_rejected": self.tr("Redmine needs a different account or password."),
            "login_failed": self.tr("Redmine sign-in failed."),
            "verification_not_pending": self.tr("Redmine sign-in failed."),
            "verification_failed": self.tr("Redmine sign-in failed."),
            "unsupported_auth_state": self.tr("Redmine sign-in failed."),
        }
        default_status = {
            AuthState.AUTHENTICATED: self.tr("Redmine sign-in succeeded."),
            AuthState.CREDENTIALS_REQUIRED: self.tr("Redmine needs a different account or password."),
            AuthState.VERIFICATION_REQUIRED: self.tr("Enter the mobile verification code."),
            AuthState.FAILED: self.tr("Redmine sign-in failed."),
        }.get(result.state, result.message)
        self._status = reason_status.get(result.reason, result.message or default_status)
        if result.state is AuthState.FAILED and result.reason != "operation_exception":
            smart_log(
                "[REDMINE_AUTH] login failure",
                domain="tool",
                source="RedmineBridge",
                level="warning",
                extra={"state": result.state.value, "reason": result.reason, "error_type": ""},
            )
        self.changed.emit()
        if result.state is AuthState.CREDENTIALS_REQUIRED:
            self.credentialsRequired.emit()
        elif result.state is AuthState.VERIFICATION_REQUIRED:
            self.verificationRequired.emit()
        elif result.state is AuthState.AUTHENTICATED:
            self.activateQuickView("my_assigned")
            self.refreshProjects()

    def _load_quick_view_cache(self, quick_view_id):
        self._issue_controller.set_account(self._account)
        self._issue_controller.activate_view(quick_view_id)
        if not self._issue_controller.load_cached():
            return False
        self._data_status = self.tr("Issues assigned to me loaded.")
        self._emit_issue_projection()
        self.changed.emit()
        return True

    @Slot()
    def refreshProjects(self):
        if self._state is not AuthState.AUTHENTICATED or self._projects_loading:
            return
        cached = context_store.load_project_options(self._account)
        if cached:
            self._project_options = cached
        self._projects_generation += 1
        generation = self._projects_generation
        self._projects_loading = True
        self._projects_ready = False
        self._projects_status = self.tr("Loading Redmine projects...")
        self.changed.emit()
        async def operation():
            service = await self._service_for(self._account)
            async with self._operation_page(service) as page:
                projects = await RedmineContextCollector(page, account=self._account).collect_projects()
                return project_options(projects)
        future = self._worker.submit(operation()); self._projects_future = future
        def finished(done):
            try:
                result = done.result()
            except Exception as exc:
                result = exc
            self.projectsReady.emit(generation, result)
        future.add_done_callback(finished)

    @Slot(int, object)
    def _apply_projects(self, generation, result):
        if self._closed or generation != self._projects_generation:
            return
        self._projects_future = None; self._projects_loading = False
        if isinstance(result, Exception):
            self._projects_status = self.tr("Redmine project loading failed.")
        elif result:
            self._project_options = list(result)
            context_store.save_project_options(self._account, self._project_options)
            self._projects_ready = True
            self._projects_status = self.tr("Redmine projects loaded.")
            if self._issue_controller.active_view_id == "my_assigned":
                self._issue_controller.apply_project_options(self._project_options)
        else:
            self._projects_status = self.tr("No Redmine projects were loaded. Retry project loading.")
        self.changed.emit()

    @Slot(str)
    def activateQuickView(self, quick_view_id):
        if self._state is not AuthState.AUTHENTICATED or self._data_loading or quick_view_id not in ("my_assigned", "watched"):
            return
        self._submitted_filters = None
        self._issue_controller.activate_view(quick_view_id)
        self._load_quick_view_cache(quick_view_id)
        if quick_view_id == "watched":
            issue_ids = context_store.load_watched_issue_ids(self._account)
            self._watched_issue_text = " ".join(issue_ids)
            if issue_ids:
                self._launch_watched_query(issue_ids, validate=False)
            else:
                self.changed.emit()
            return
        async def operation():
            service = await self._service_for(self._account)
            async with self._operation_page(service) as page:
                self._emit_data_progress(0, 0, "my_assigned")
                rows = await RedmineContextCollector(page, account=self._account).collect_my_page_assigned()
                if not rows:
                    return "quick_view_empty",
                metadata = list(self._project_options)
                metadata.extend(self._issue_controller.context_project_options())
                context = self._issue_controller.assigned_context(rows, metadata)
                return await self._enrich_issue_context(page, context, {}, progress=True)
        self._launch_data_load(operation, status=self.tr("Loading issues assigned to me..."), kind="my_assigned")

    def _launch_data_load(self, operation, *, status, kind="detail"):
        if self._closed:
            return
        self._data_generation += 1
        generation = self._data_generation
        self._data_loading = True
        self._data_operation_kind = kind
        self._data_loaded = 0
        self._data_total = 0
        self._data_status = status
        self.changed.emit()
        future = self._worker.submit(operation())
        self._data_future = future

        def finished(future):
            try:
                result = future.result()
            except Exception as exc:
                result = exc
            smart_log("[REDMINE_LOAD] data failure" if isinstance(result, Exception) else "[REDMINE_LOAD] data finished", domain="tool", source="RedmineBridge", level="warning" if isinstance(result, Exception) else "info", extra={"kind": kind, "generation": generation, "error_type": type(result).__name__ if isinstance(result, Exception) else ""})
            self.dataReady.emit(generation, result)

        future.add_done_callback(finished)

    @Slot()
    def startLogin(self):
        credentials = self._auth.runtime_credentials()
        if credentials is None:
            self.credentialsRequired.emit()
            return
        username, password = credentials.username, credentials.password
        self._account = username

        async def operation():
            return await (await self._service_for(username)).login(Credential(username, password))

        self._launch(operation)

    @Slot(str, str)
    def submitCredentials(self, username, password):
        self._account = username.strip()
        self._service = None

        async def operation():
            return await (await self._service_for(self._account)).login(Credential(self._account, password))

        self._launch(operation)

    @Slot(str)
    def submitVerification(self, code):
        async def operation():
            return await self._service.submit_verification(code)

        self._launch(operation)

    @Slot()
    def refreshData(self):
        if self._state is not AuthState.AUTHENTICATED:
            return
        if not self._projects_ready:
            return
        if self._data_loading:
            self._refresh_after_search = True
            return
        filter_requested = self._pending_filters is not None
        if not filter_requested:
            self._load_cached_view()
        saved_filters = self._issue_controller.filters_for_search(
            self._pending_filters if filter_requested else None
        )
        self._pending_filters = None
        self._active_search_filter_requested = filter_requested
        self._issue_controller.activate_view("")
        project_metadata = list(self._project_options)

        async def operation():
            service = await self._service_for(self._account)
            smart_log("[REDMINE_LOAD] discovery", domain="tool", source="RedmineBridge", level="debug", extra={"phase": "discovery"})
            self._emit_data_progress(0, 0, "discovery")
            async with self._operation_page(service) as page:
                collector = RedmineContextCollector(page, account=self._account)
                effective_filters = saved_filters
                context = await collector.collect_query(await _native_query(collector, saved_filters))
                context = self._issue_controller.reconcile_project_ids(
                    context, project_metadata
                )
                enriched = await self._enrich_issue_context(page, context, effective_filters, progress=True)
            project_count = sum(bool(project.project_id) for project in context.projects)
            collected_rows = sum(len(project.issues) for project in context.projects)
            unique_issues = sum(len(project.issues) for project in enriched[0].projects)
            filtered_rows = len(
                self._issue_controller.enrichment_projection(
                    enriched[0], effective_filters
                ).issue_rows
            )
            smart_log("[REDMINE_LOAD] plan", domain="tool", source="RedmineBridge", level="info", extra={"projects": project_count, "collected_rows": collected_rows, "unique_issues": unique_issues, "filtered_rows": filtered_rows})
            return enriched

        self._launch_data_load(operation, status=self.tr("Loading Redmine data..."), kind="search")

    def _emit_data_progress(self, loaded: int, total: int, label: str) -> None:
        self.dataProgressReady.emit(int(loaded), int(total), str(label or ""))

    @Slot(int, int, str)
    def _apply_data_progress(self, loaded: int, total: int, label: str):
        if not self._data_loading:
            return
        self._data_loaded = loaded
        self._data_total = total
        if label == "discovery":
            self._data_status = self.tr("Discovering Redmine projects and issues...")
        elif label == "my_assigned":
            self._data_status = self.tr("Loading issues assigned to me...")
        elif label == "analysis":
            self._data_status = self.tr("Analyzing Redmine issue activity... {loaded}/{total}").format(loaded=loaded, total=total)
        elif label == "clone":
            self._data_status = self.tr("Checking cloned Jira issues... {loaded}/{total}").format(loaded=loaded, total=total)
        elif total > 0:
            self._data_status = self.tr("Loading Redmine data... {loaded}/{total}").format(loaded=loaded, total=total)
        else:
            self._data_status = self.tr("Loading Redmine data...")
        self.changed.emit()

    def _launch_watched_query(self, issue_ids, *, validate, submitted_terms=None):
        submitted = tuple(issue_ids)
        terms = tuple(submitted_terms or submitted)
        project_metadata = list(self._project_options)
        async def operation():
            service = await self._service_for(self._account)
            async with self._operation_page(service) as page:
                context = await RedmineContextCollector(page, account=self._account).collect_query(
                    RedmineQuery(issue_ids=submitted)
                )
                context = self._issue_controller.reconcile_project_ids(
                    context, project_metadata
                )
                enriched = await self._enrich_issue_context(page, context, {}, progress=True)
                return (*enriched, submitted, terms, validate)
        self._launch_data_load(operation, status=self.tr("Loading watched issues..."), kind="watched")

    @Slot(str)
    def saveWatchedIssueIds(self, text):
        terms = parse_terms(text)
        submitted = tuple(term for term in terms if term.isdecimal())
        invalid_syntax = tuple(term for term in terms if not term.isdecimal())
        self._watched_issue_text = str(text or "")
        self._watched_issue_error = ""
        self._issue_controller.activate_view("watched")
        if not terms:
            context_store.save_watched_issue_ids(self._account, [])
            self._watched_issue_text = ""
            self._issue_controller.clear_watched_view()
            self.changed.emit()
            return
        if not submitted:
            self._watched_issue_error = self.tr("Invalid issue IDs: %1").replace("%1", ", ".join(invalid_syntax))
            self.changed.emit()
            return
        self._load_quick_view_cache("watched")
        self._launch_watched_query(submitted, validate=True, submitted_terms=terms)

    @Slot(dict)
    @Slot(object)
    def applyFilters(self, filters):
        if not self._projects_ready:
            return
        filters = dict(filters or {})
        project = "" if filters.get("project") == self.tr("All projects") else filters.get("project", "")
        status = "" if filters.get("status") == self.tr("All statuses") else filters.get("status", "")
        issue_type = "" if filters.get("type") in ("", self.tr("All types")) else filters.get("type", "")
        self._pending_filters = {"project": project, "status": status, "type": issue_type, "subject": str(filters.get("subject", "") or ""), "text": str(filters.get("text", "") or "")}
        self._submitted_filters = dict(self._pending_filters)
        if self._data_loading:
            self._refresh_after_search = True
            return
        self.refreshData()

    @Slot()
    def cancelSearch(self):
        if self._data_future is None or self._data_operation_kind != "search":
            return
        self._data_generation += 1
        self._data_future.cancel()
        self._data_future = None
        self._data_loading = False
        self._data_operation_kind = ""
        self._data_status = self.tr("Search cancelled.")
        self.changed.emit()

    @Slot(str)
    def selectIssue(self, issue_id):
        issue_id = str(issue_id or "").strip()
        if not issue_id or self._state is not AuthState.AUTHENTICATED:
            return
        selection = self._issue_controller.select_issue(issue_id)
        if selection.issue is None or selection.project is None or selection.item is None:
            return
        self.issueSelectionChanged.emit()
        if not selection.needs_detail:
            return

        if self._data_loading and self._data_operation_kind == "search":
            self._pending_detail_issue_id = issue_id
            return

        async def operation():
            service = await self._service_for(self._account)
            async with self._operation_page(service) as page:
                collector = RedmineContextCollector(page, account=self._account)
                return "detail", issue_id, await collector.collect_issue_detail(
                    selection.item, project=selection.project
                )

        self._launch_data_load(operation, status=self.tr("Refreshing Redmine issue detail..."), kind="detail")

    @Slot(int, object)
    def _apply_data(self, generation, result):
        if self._closed or generation != self._data_generation:
            return
        operation_kind = self._data_operation_kind
        self._issue_controller.set_account(self._account)
        self._data_loading = False
        self._data_operation_kind = ""
        self._data_future = None
        if self._refresh_after_search:
            self._refresh_after_search = False
            self.refreshData()
            return
        if isinstance(result, Exception):
            smart_log(
                "Redmine data load failed",
                domain="ui",
                source="RedmineBridge",
                level="warning",
                exc_info=(type(result), result, result.__traceback__),
            )
            if operation_kind == "my_assigned":
                smart_log("[REDMINE_MY_ASSIGNED] refresh failed", domain="tool", source="RedmineBridge", level="warning", extra={"error_type": type(result).__name__})
                self._data_status = self.tr("Issues assigned to me loading failed.")
            else:
                self._data_status = self.tr("Redmine data load failed.")
            self.changed.emit()
            return
        if operation_kind == "my_assigned" and result == ("quick_view_empty",):
            self._issue_controller.activate_view("my_assigned")
            self._issue_controller.clear_active_view()
            self._data_status = self.tr("Issues assigned to me loaded.")
            self._emit_issue_projection()
            self.changed.emit()
            return
        if isinstance(result, tuple) and len(result) == 3 and result[0] == "detail":
            _kind, issue_id, detail = result
            if not self._issue_controller.apply_selected_detail(issue_id, detail):
                return
            self._data_status = self.tr("Redmine issue detail refreshed.")
            self._emit_issue_projection()
            self.changed.emit()
            return
        watched_submission = ()
        watched_terms = ()
        watched_validate = False
        if operation_kind == "watched":
            context, _project_identifier, detail, filters, clone_status, watched_submission, watched_terms, watched_validate = result
        else:
            context, _project_identifier, detail, filters, clone_status = result
        if operation_kind == "search" and not self._active_search_filter_requested:
            wanted_id = self._issue_controller.selected_id
            detail = next((item for item in context.issues if item.id == wanted_id), detail if not wanted_id else None)
        self._data_loaded = sum(len(project.issues) for project in context.projects)
        self._data_total = self._data_loaded
        self._data_status = self.tr("Issues assigned to me loaded.") if operation_kind == "my_assigned" else self.tr("Redmine data loaded.")
        if operation_kind == "watched":
            valid_ids = {issue.id for project in context.projects for issue in project.issues}
            valid = [issue_id for issue_id in watched_submission if issue_id in valid_ids]
            invalid = [term for term in watched_terms if term not in valid_ids]
            if watched_validate and not valid:
                self._watched_issue_error = self.tr("No valid watched issue IDs were found: %1").replace("%1", ", ".join(invalid))
                if self._load_quick_view_cache("watched"):
                    self.changed.emit()
                    return
            else:
                if watched_validate:
                    context_store.save_watched_issue_ids(self._account, valid)
                    self._watched_issue_text = " ".join(valid)
                self._watched_issue_error = self.tr("Invalid issue IDs: %1").replace("%1", ", ".join(invalid)) if invalid else ""
        self._issue_controller.replace_result(
            context,
            filters=filters,
            selected_detail=detail,
            clone_status=clone_status,
        )
        self._submitted_filters = None
        self._emit_issue_projection()
        if operation_kind == "search" and self._active_search_filter_requested:
            self._active_search_filter_requested = False
        self.changed.emit()
        pending_issue_id, self._pending_detail_issue_id = self._pending_detail_issue_id, ""
        selected_issue_id = self._issue_controller.selected_id
        detail_issue_id = pending_issue_id or selected_issue_id
        if operation_kind == "search" and detail_issue_id and detail_issue_id == selected_issue_id and not any(item.id == detail_issue_id for item in context.issues):
            self.selectIssue(detail_issue_id)

    def _reset_batch(self, *, cancel_future=False, wait=False):
        if cancel_future and self._batch_future is not None:
            self._batch_future.cancel()
            if (
                wait
                and self._batch_done_event is not None
                and not self._batch_done_event.wait(timeout=40)
            ):
                smart_log(
                    "Redmine clone cancellation did not finish before close timeout",
                    domain="ui",
                    source="RedmineBridge",
                    level="warning",
                    extra={"timeout_seconds": 40},
                )
        self._batch_future = None
        self._batch_done_event = None
        self._batch_controller.reset()

    @Slot()
    def beginCloneSelection(self):
        if self._batch_controller.begin_selection():
            self.changed.emit()

    @Slot(str, bool)
    def toggleCloneSelection(self, issue_id, selected):
        if self._batch_controller.toggle_selection(
            issue_id, selected, self._issue_controller.issue_rows
        ):
            self.changed.emit()

    @Slot()
    def cancelCloneSelection(self):
        if self._batch_controller.cancel_selection():
            self.changed.emit()

    def _launch_batch_operation(self, operation):
        account_generation = self._generation
        completion = Event()

        async def tracked_operation():
            try:
                return await operation.awaitable
            finally:
                completion.set()

        future = self._worker.submit(tracked_operation())
        self._batch_future = future
        self._batch_done_event = completion

        def finished(done):
            try:
                result = done.result()
            except Exception as exc:
                result = exc
            self.cloneReady.emit(
                operation.generation, account_generation, operation.kind, result
            )

        future.add_done_callback(finished)

    @Slot()
    def prepareCloneDrafts(self):
        operation = self._batch_controller.start_prepare()
        if operation is not None:
            self.changed.emit()
            self._launch_batch_operation(operation)

    @Slot(str, str, "QVariant")
    def updateCloneDraft(self, issue_id, field_id, value):
        if hasattr(value, "toVariant"):
            value = value.toVariant()
        previous = self._batch_controller.field_payloads(issue_id)
        if self._batch_controller.update_draft(issue_id, field_id, value):
            for changed_id, field in self._batch_controller.field_payloads(issue_id).items():
                if changed_id == field_id or field != previous.get(changed_id):
                    self._emit_clone_draft_field(issue_id, changed_id, field)

    @Slot()
    def submitCloneBatch(self):
        operation = self._batch_controller.start_submit()
        self.changed.emit()
        if operation is not None:
            self._launch_batch_operation(operation)
            return
        status = self._batch_controller.status
        if status.first_invalid_issue_id and status.first_invalid_field_id:
            self.cloneInvalidFieldRequested.emit(
                status.first_invalid_issue_id,
                status.first_invalid_field_id,
            )

    @Slot()
    def retryFailedClones(self):
        operation = self._batch_controller.retry_failed()
        if operation is not None:
            self.changed.emit()
            self._launch_batch_operation(operation)

    @Slot()
    def closeCloneBatch(self):
        if self._batch_controller.close_batch():
            if self._batch_future is not None:
                self._batch_future.cancel()
                self._batch_future = None
            self.changed.emit()

    @Slot(str, str, str)
    def searchCloneUsers(self, issue_id, field_id, query):
        operation = self._batch_controller.start_user_search(
            issue_id, field_id, query
        )
        if operation is not None:
            self._launch_batch_operation(operation)

    @Slot(int, int, str, object)
    def _apply_clone_result(self, clone_generation, account_generation, kind, result):
        if (
            self._closed
            or account_generation != self._generation
            or not self._batch_controller.accepts_result(
                clone_generation, kind, result
            )
        ):
            return
        self._batch_future = None
        self._batch_done_event = None
        if kind == "users" and isinstance(result, Exception):
            return
        if isinstance(result, Exception):
            self._batch_controller.apply_error(kind, result)
        else:
            self._batch_controller.apply_result(kind, result)
        if kind == "submit" and not isinstance(result, Exception):
            self._emit_issue_projection()
            if self._batch_controller.status.state == "completed":
                self._batch_controller.close_batch()
        if kind == "users" and not isinstance(result, Exception):
            issue_id, field_id, _users = result
            self._emit_clone_draft_field(issue_id, field_id)
            return
        self.changed.emit()

    def _emit_clone_draft_field(self, issue_id, field_id, field=None):
        if field is None:
            field = self._batch_controller.field_payload(issue_id, field_id)
        if field is not None:
            self.cloneDraftFieldChanged.emit(str(issue_id or ""), field)
    async def _close_flow(self):
        service, self._service = self._service, None
        if service is not None:
            try:
                await service.close()
            except Exception:
                pass
        await self._runtime.close()

    @Slot()
    def cancelLogin(self):
        self._generation += 1
        self._data_generation += 1
        self._projects_generation += 1
        self._reset_batch(cancel_future=True, wait=True)
        if self._login_future is not None:
            self._login_future.cancel()
            self._login_future = None
        if self._data_future is not None:
            self._data_future.cancel()
            self._data_future = None
        for name in ("_projects_future",):
            future = getattr(self, name)
            if future is not None:
                future.cancel()
                setattr(self, name, None)
        self._worker.submit(self._close_flow())
        self._state = AuthState.IDLE
        self._status = self.tr("Redmine sign-in cancelled.")
        self._data_loading = False
        self._projects_loading = False
        self.changed.emit()

    @Slot()
    def close(self):
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._data_generation += 1
        self._projects_generation += 1
        self._reset_batch(cancel_future=True, wait=True)
        if self._login_future is not None:
            self._login_future.cancel()
            self._login_future = None
        if self._data_future is not None:
            self._data_future.cancel()
            self._data_future = None
        for name in ("_projects_future",):
            future = getattr(self, name)
            if future is not None:
                future.cancel()
                setattr(self, name, None)
        try:
            close_future = self._worker.submit(self._close_flow())
            try:
                close_future.result(timeout=10)
            except FutureTimeoutError:
                close_future.cancel()
                smart_log(
                    "Redmine close flow reached timeout",
                    domain="ui",
                    source="RedmineBridge",
                    level="warning",
                    extra={"timeout_seconds": 10},
                )
        finally:
            if not self._worker.stop():
                smart_log(
                    "Redmine async worker did not stop before timeout",
                    domain="ui",
                    source="RedmineBridge",
                    level="warning",
                )

    @Slot(str)
    def openWebUrl(self, url):
        clean_url = str(url or "").strip()
        if not clean_url:
            return
        QDesktopServices.openUrl(QUrl(clean_url))


async def _download_redmine_attachments(
    page,
    attachments,
    metadata,
    *,
    duplicate_filenames=(),
    transfer=None,
):
    owner = transfer or RedmineAttachmentTransfer()
    return await owner.stage(
        page,
        attachments,
        metadata,
        duplicate_filenames=duplicate_filenames,
    )


def _translated_args(template, *values):
    text = str(template)
    for index in range(len(values), 0, -1):
        text = text.replace(f"%{index}", str(values[index - 1]))
    return text


class _RedmineCloneChecker:
    def __init__(self, service: IssueCommandService, *, project_key: str | None = None):
        self._service = service
        self._project_key = project_key or os.getenv("SMARTTEST_REDMINE_JIRA_PROJECT_KEY", "SH")

    def check_many(self, rows, *, progress_callback=None, progress_base: int = 0, progress_total: int | None = None):
        result = {}
        total = len(rows)
        expected_total = progress_total if progress_total is not None else progress_base + total
        for index, row in enumerate(rows, start=1):
            issue_id = str(row.get("id") or row.get("key") or "")
            web_url = str(row.get("webUrl") or "")
            existing = self._service.check_issue_by_external_url(project_key=self._project_key, external_url=web_url)
            if issue_id and existing:
                result[issue_id] = existing
            if progress_callback:
                progress_callback(progress_base + index, expected_total, "Checking cloned Jira issues")
        return result
