from __future__ import annotations

import asyncio
from .QtTaskAdapter import QtTaskAdapter
from .desktop_tasks import DESKTOP_TASKS
from datetime import datetime
from pathlib import Path
import sys
import time
from typing import Any

from PySide6.QtCore import QObject, Property, Signal, Slot
from PySide6.QtGui import QGuiApplication

from core.testing.cases.catalog import is_packaged_runtime, load_packaged_test_catalog
from core.testing.cases.discovery import PytestDiscoveryError, discover_pytest_cases
from core.testing.params.contracts import case_param_keys, default_env_device_type, env_kinds_for_case, required_param_keys
from core.testing.params.options import normalize_option_values
from core.testing.params.registry import SchemaRegistry, default_registry
from core.testing.test_context import smarttest_context
from core.testing.params.schema import ParamField, ParamSchema, ParamScope, ParamValueType
from core.testing.state.models import SelectedCase, TestPageState
from core.testing.state.store import ensure_state, load_state, save_state
from core.testing.tool.dut_tool.parameter_helper import ParameterHelper
from core.testing.tool.env_tool import build_env_equipment_row, default_env_config
from mobile.android import ensure_test_apk_installed
from core.logging import smart_log

try:
    from example.helper.AppPaths import app_data_dir
except ImportError:  # pragma: no cover - direct unit-test imports may use the ui.example package path
    from client.app.ui.example.helper.AppPaths import app_data_dir


class TestPageBridge(QObject):
    casesChanged = Signal()
    stateChanged = Signal()
    errorOccurred = Signal(str)
    _refreshStageChanged = Signal(str, int, str)

    suiteChanged = Signal()

    def __init__(self, root_dir: Path, *, auth_bridge=None, suite_source=None):
        super().__init__(QGuiApplication.instance())
        self._tasks = QtTaskAdapter(manager=DESKTOP_TASKS)
        self._root_dir = root_dir.resolve()
        self._auth_bridge = auth_bridge
        self._suite_source = suite_source
        self._suite_panel_loading = False
        self._suite_refresh_running = False
        self._suite_refresh_pending = False
        self._suite_action_running = False
        self._suite_action_kind = ""
        self._suite_error = ""
        self._suite_scope = "mine"
        self._suite_search_text = ""
        self._suite_rows: list[dict[str, Any]] = []
        self._active_suite_id = ""
        self._active_suite_revision = 0
        self._suite_session_generation = 0
        self._suite_view_generation = 0
        self._suite_io_lock = asyncio.Lock()
        self._registry: SchemaRegistry = default_registry()
        self._cases: list[dict[str, Any]] = []
        self._cases_by_nodeid: dict[str, dict[str, Any]] = {}
        self._cases_by_file: dict[str, list[dict[str, Any]]] = {}
        self._state_path = self._default_state_path()
        self._trace_log_path = self._state_path.parent / "test_page_trace.log"
        self._parameter_helper = ParameterHelper(apk_ensurer=ensure_test_apk_installed, to_thread=lambda work, *args, **kwargs: self._tasks.to_thread("dut-work", work, *args, **kwargs))
        self._state = ensure_state(self._state_path)
        smarttest_context().params.bind_ui_state(self._state)
        self._adb_devices: list[str] = []
        self._env_options: dict[str, list[Any]] = {}
        self._adb_refresh_running = False
        self._adb_refresh_phase = "idle"
        self._adb_refresh_progress = 0
        self._adb_refresh_detail = ""
        self._adb_refresh_error = ""
        self._adb_refresh_started = False
        self._context_refresh_running = False
        self._dynamic_fields_refreshing: set[str] = set()
        self._discovery_running = False
        self._discovery_loaded = False
        self._refreshStageChanged.connect(self._apply_refresh_stage)
        state_changed = self._ensure_state_defaults()
        state_changed = self._clear_case_param_options() or state_changed
        state_changed = self._sync_dut_selection() or state_changed
        if state_changed:
            save_state(self._state_path, self._state)
        if auth_bridge is not None and hasattr(auth_bridge, "authChanged"):
            auth_bridge.authChanged.connect(self._on_auth_changed)

    def _suite_credentials(self):
        getter = getattr(self._auth_bridge, "runtime_credentials", None)
        return getter() if callable(getter) else None

    def _suite_username(self) -> str:
        credentials = self._suite_credentials()
        return str(getattr(credentials, "username", "") or "")

    def _set_suite_error(self, error) -> None:
        self._suite_error = str(getattr(error, "code", "") or "service_unavailable")
        invalidator = getattr(self._auth_bridge, "invalidate_runtime_credentials", None)
        if self._suite_error == "invalid_credentials" and callable(invalidator):
            invalidator(self._suite_error)

    @Slot()
    def _on_auth_changed(self) -> None:
        self._suite_session_generation += 1
        self._suite_view_generation += 1
        generation = self._suite_session_generation
        view_generation = self._suite_view_generation
        self._suite_rows = []
        self._active_suite_id = ""
        self._active_suite_revision = 0
        self._suite_error = ""
        self._suite_refresh_running = False
        self._suite_refresh_pending = False
        self._suite_action_running = False
        self._suite_action_kind = ""
        credentials = self._suite_credentials()
        if self._suite_source is None:
            self.suiteChanged.emit()
            return
        self._suite_panel_loading = True
        self.suiteChanged.emit()
        self._create_task(
            self._reset_suite_session(credentials, generation, view_generation),
            label="suite_session",
        )

    async def _reset_suite_session(self, credentials, generation: int, view_generation: int) -> None:
        self._trace(
            "suite_session_start",
            generation=generation,
            view_generation=view_generation,
        )
        try:
            async with self._suite_io_lock:
                if generation != self._suite_session_generation:
                    return
                result = await self._tasks.to_thread(
                    "suite-account",
                    self._suite_source.switch_account,
                    credentials,
                    self._suite_scope,
                )
                if (generation == self._suite_session_generation
                        and view_generation == self._suite_view_generation):
                    if result.ok:
                        self._suite_rows = [dict(row) for row in (result.value or [])]
                        self._suite_error = ""
                    else:
                        self._set_suite_error(result.error)
        finally:
            current = generation == self._suite_session_generation
            if current:
                self._suite_panel_loading = False
                self.suiteChanged.emit()
            self._trace(
                "suite_session_done",
                generation=generation,
                view_generation=view_generation,
                current=current,
                final_error=self._suite_error if current else "stale_result_discarded",
            )

    async def _refresh_suites_task(
        self, generation: int, view_generation: int, scope: str
    ) -> None:
        self._trace(
            "suite_refresh_start",
            generation=generation,
            view_generation=view_generation,
            scope=scope,
        )
        try:
            async with self._suite_io_lock:
                if (generation != self._suite_session_generation
                        or view_generation != self._suite_view_generation):
                    return
                credentials = self._suite_credentials()
                if credentials is None:
                    self._suite_error = "authentication_required"
                    return
                result = await self._tasks.to_thread(
                    "suite-list", self._suite_source.list_suites, credentials, scope
                )
                if (generation == self._suite_session_generation
                        and view_generation == self._suite_view_generation
                        and scope == self._suite_scope):
                    if result.ok:
                        self._suite_rows = [dict(row) for row in (result.value or [])]
                        self._suite_error = ""
                    else:
                        self._set_suite_error(result.error)
        finally:
            current = generation == self._suite_session_generation
            if current:
                self._suite_refresh_running = False
                self.suiteChanged.emit()
                if self._suite_refresh_pending:
                    self._suite_refresh_pending = False
                    self.refreshSuites()
            self._trace(
                "suite_refresh_done",
                generation=generation,
                view_generation=view_generation,
                current=current,
                final_error=self._suite_error if current else "stale_result_discarded",
            )

    @Slot()
    def refreshSuites(self) -> None:
        if self._suite_source is None or self._suite_refresh_running:
            return
        self._suite_view_generation += 1
        generation = self._suite_session_generation
        view_generation = self._suite_view_generation
        scope = self._suite_scope
        self._suite_refresh_running = True
        self.suiteChanged.emit()
        self._create_task(
            self._refresh_suites_task(generation, view_generation, scope),
            label="suite_refresh",
        )

    @Slot(str)
    def setSuiteScope(self, scope: str) -> None:
        if scope not in {"mine", "shared"} or scope == self._suite_scope:
            return
        self._suite_scope = scope
        self._suite_rows = []
        self.suiteChanged.emit()
        if self._suite_refresh_running:
            self._suite_view_generation += 1
            self._suite_refresh_pending = True
            return
        self.refreshSuites()

    def _suite_write_payload(self, name: str, description: str, visibility: str) -> dict[str, Any]:
        return {"name": str(name or "").strip(), "description": str(description or ""),
                "visibility": visibility, "orderedNodeids": self._selected_nodeids()}

    def _apply_suite_selection(self, detail: dict[str, Any], current_username: str) -> list[str]:
        available = set(self._cases_by_nodeid)
        ordered = [str(item) for item in detail.get("orderedNodeids", [])]
        valid = [nodeid for nodeid in ordered if nodeid in available]
        missing = [nodeid for nodeid in ordered if nodeid not in available]
        self._state.selected = []
        self._state.selected_files = []
        for nodeid in valid:
            self._set_case_selected(nodeid, True)
        self._active_suite_id = str(detail.get("id", ""))
        self._active_suite_revision = int(detail.get("revision", 0) or 0)
        if str(detail.get("ownerUsername", "")) != current_username:
            self._active_suite_revision = 0
        self._save_and_emit()
        self._schedule_context_refresh("suite_loaded")
        return missing

    async def _suite_action(self, kind: str, generation: int, function, *args) -> None:
        self._suite_action_running = True
        self._suite_action_kind = kind
        self._suite_error = ""
        self.suiteChanged.emit()
        try:
            async with self._suite_io_lock:
                if generation != self._suite_session_generation:
                    return
                action_result = await self._tasks.to_thread("suite-action", function, *args)
                if generation != self._suite_session_generation:
                    return
                if not action_result.ok:
                    self._set_suite_error(action_result.error)
                    return
                result = action_result.value
                rows_result = None
                if kind != "load":
                    credentials = self._suite_credentials()
                    if credentials is not None:
                        rows_result = await self._tasks.to_thread(
                            "suite-list",
                            self._suite_source.list_suites,
                            credentials,
                            self._suite_scope,
                        )
                if generation != self._suite_session_generation:
                    return
            if kind == "load":
                missing = self._apply_suite_selection(result, self._suite_username())
                if missing:
                    self._suite_error = "missing_nodeids:" + "\n".join(missing)
            elif isinstance(result, dict):
                self._active_suite_id = str(result.get("id", ""))
                self._active_suite_revision = int(result.get("revision", 0) or 0)
            elif kind == "delete":
                if self._active_suite_id == str(args[0] if args else ""):
                    self._active_suite_id = ""
                    self._active_suite_revision = 0
            if rows_result is not None and generation == self._suite_session_generation:
                if rows_result.ok:
                    self._suite_rows = [dict(row) for row in (rows_result.value or [])]
                else:
                    self._set_suite_error(rows_result.error)
        finally:
            if generation == self._suite_session_generation:
                self._suite_action_running = False
                self._suite_action_kind = ""
                self.suiteChanged.emit()

    def _start_suite_action(self, kind: str, function, *args) -> None:
        if self._suite_action_running or self._suite_source is None:
            return
        credentials = self._suite_credentials()
        if credentials is None:
            self._suite_error = "authentication_required"
            self.suiteChanged.emit()
            return
        self._create_task(
            self._suite_action(
                kind, self._suite_session_generation, function, credentials, *args
            ),
            label=f"suite_{kind}",
        )

    @Slot(str)
    def loadSuite(self, suite_id: str) -> None:
        if not self._discovery_loaded:
            self._suite_error = "invalid_input"
            self.suiteChanged.emit()
            return
        self._start_suite_action("load", self._suite_source.get_suite, suite_id)

    @Slot(str, str, str)
    def createSuite(self, name: str, description: str, visibility: str) -> None:
        payload = self._suite_write_payload(name, description, visibility)
        if not payload["orderedNodeids"]:
            self._suite_error = "invalid_input"
            self.suiteChanged.emit()
            return
        self._start_suite_action("create", self._suite_source.create_suite, payload)

    @Slot(str, str, str)
    def updateActiveSuite(self, name: str, description: str, visibility: str) -> None:
        if not self._active_suite_id or not self._active_suite_revision:
            return
        payload = self._suite_write_payload(name, description, visibility)
        payload["revision"] = self._active_suite_revision
        self._start_suite_action("update", self._suite_source.update_suite,
                                 self._active_suite_id, payload)

    @Slot(str)
    def deleteSuite(self, suite_id: str) -> None:
        self._start_suite_action("delete", self._suite_source.delete_suite, suite_id)

    @Slot(str, str, str)
    def copySuite(self, suite_id: str, name: str, visibility: str) -> None:
        self._start_suite_action("copy", self._suite_source.copy_suite, suite_id,
                                 {"name": name, "visibility": visibility})

    @Slot(result="QVariantList")
    def suiteRows(self):
        query = self._suite_search_text.casefold()
        if not query:
            return list(self._suite_rows)
        return [row for row in self._suite_rows if query in str(row.get("name", "")).casefold()
                or query in str(row.get("ownerDisplayName", "")).casefold()]

    @Slot(str)
    def setSuiteSearchText(self, value: str) -> None:
        normalized = str(value or "").strip()
        if normalized == self._suite_search_text:
            return
        self._suite_search_text = normalized
        self.suiteChanged.emit()

    suitePanelLoading = Property(bool, lambda self: self._suite_panel_loading, notify=suiteChanged)
    suiteRefreshRunning = Property(bool, lambda self: self._suite_refresh_running, notify=suiteChanged)
    suiteActionRunning = Property(bool, lambda self: self._suite_action_running, notify=suiteChanged)
    suiteActionKind = Property(str, lambda self: self._suite_action_kind, notify=suiteChanged)
    suiteError = Property(str, lambda self: self._suite_error, notify=suiteChanged)
    suiteScope = Property(str, lambda self: self._suite_scope, notify=suiteChanged)
    suiteSearchText = Property(str, lambda self: self._suite_search_text, notify=suiteChanged)
    activeSuiteId = Property(str, lambda self: self._active_suite_id, notify=suiteChanged)
    activeSuiteRevision = Property(int, lambda self: self._active_suite_revision, notify=suiteChanged)
    discoveryLoaded = Property(bool, lambda self: self._discovery_loaded, notify=casesChanged)

    def _trace(self, stage: str, **values: Any) -> None:
        details = " ".join(f"{key}={values[key]}" for key in sorted(values))
        line = f"{_trace_timestamp()} [TEST_UI] {stage} {details}".rstrip()
        smart_log(line, domain="ui", source="TestPageBridge", emit_runtime_event=False)
        try:
            self._trace_log_path.parent.mkdir(parents=True, exist_ok=True)
            with self._trace_log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except Exception:
            pass

    def _default_state_path(self) -> Path:
        return app_data_dir() / "test_page_state.json"

    def _bind_params_state(self) -> None:
        smarttest_context().params.bind_ui_state(self._state)

    def _ensure_state_defaults(self) -> bool:
        changed = smarttest_context().params.ensure_persisted_defaults()
        if not hasattr(self._state, "selected_files"):
            self._state.selected_files = []
            changed = True
        changed = self._migrate_local_playback_storage_paths() or changed
        changed = self._prune_stored_fixed_enum_values() or changed
        return changed

    def _migrate_local_playback_storage_paths(self) -> bool:
        old_default = "/storage/emulated/0/Movies"
        legacy_default = "/mnt/media_rw"
        legacy_movies_default = "/mnt/media_rw/*/Movies"
        new_default = "/storage/*/Movies /storage/*/Video"

        def migrate_value(key: str, value: Any) -> tuple[Any, bool]:
            if key == "local_playback_stress:media_dir":
                if value in (old_default, legacy_default, legacy_movies_default):
                    return new_default, True
                if isinstance(value, str) and value.startswith("/mnt/media_rw/"):
                    return _storage_playback_path(value), True
            if key == "local_playback_stress:media_files" and isinstance(value, list):
                next_files = [_storage_playback_path(str(item or "").strip()) for item in value]
                return next_files, next_files != value
            return value, False

        return smarttest_context().params.migrate_case_parameter_values(migrate_value)

    def _prune_stored_fixed_enum_values(self) -> bool:
        changes = smarttest_context().params.prune_stored_fixed_enum_values()
        for item in changes:
            self._trace(
                "param_fixed_enum_pruned",
                field=str(item.get("field", "")),
                removed=item.get("removed", 0),
                kept=item.get("kept", 0),
            )
        return bool(changes)

    def _create_task(self, coro, *, label: str) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError as exc:
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                self._trace("async_task_schedule_error", label=label, error=exc)
                raise
        self._tasks.schedule_coroutine(loop, label, coro)
        self._trace("async_task_scheduled", label=label)

    def _schedule_adb_refresh(self, reason: str) -> None:
        if self._adb_refresh_running:
            self._trace("adb_refresh_skip_running", reason=reason)
            return
        self._adb_refresh_running = True
        self._adb_refresh_phase = "scan"
        self._adb_refresh_progress = 0
        self._adb_refresh_detail = self.tr("Scanning ADB devices")
        self._adb_refresh_error = ""
        self._trace("adb_refresh_start", reason=reason)
        selected_serial = self._current_dut_serial() if reason == "user_refresh" else ""
        self._create_task(
            self._refresh_adb_devices_task(
                selected_serial,
                prepare_android_client=reason == "user_refresh",
            ),
            label="adb_refresh",
        )
        self.stateChanged.emit()

    def _current_dut_serial(self) -> str:
        selected = self._selected_dut_serials()
        if selected:
            return selected[0]
        return str(smarttest_context().params.global_value("dut", "") or "").strip()

    def _selected_dut_serials(self) -> list[str]:
        raw = self._state.global_context.get("duts", [])
        selected: list[str] = []
        if isinstance(raw, list):
            for item in raw:
                serial = str(item or "").strip()
                if serial and serial not in selected:
                    selected.append(serial)
        legacy = str(self._state.global_context.get("dut", "") or "").strip()
        if not selected and legacy:
            selected.append(legacy)
        return selected

    def _set_selected_dut_serials(self, serials: list[str]) -> bool:
        normalized: list[str] = []
        for item in serials:
            serial = str(item or "").strip()
            if serial and serial not in normalized:
                normalized.append(serial)
        current = self._selected_dut_serials()
        changed = current != normalized
        if changed:
            self._state.global_context["duts"] = normalized
        first = normalized[0] if normalized else ""
        if str(self._state.global_context.get("dut", "") or "").strip() != first:
            self._state.global_context["dut"] = first
            changed = True
        return changed

    def _schedule_context_refresh(self, reason: str) -> None:
        if self._context_refresh_running:
            self._trace("context_refresh_skip_running", reason=reason)
            return
        param_targets = self._selected_dynamic_param_targets()
        env_targets = self._selected_dynamic_env_targets()
        selected_duts = self._selected_dut_serials()
        if param_targets and not selected_duts:
            if self._clear_case_param_options(param_targets):
                save_state(self._state_path, self._state)
                self.stateChanged.emit()
            self._trace("context_refresh_skip_no_dut", reason=reason, params=len(param_targets))
            param_targets = []
        if not param_targets and not env_targets:
            self._trace("context_refresh_skip_empty", reason=reason)
            return
        if param_targets and self._clear_case_param_options(param_targets):
            save_state(self._state_path, self._state)
        self._context_refresh_running = True
        self._dynamic_fields_refreshing = {
            *(
                f"param:{str(target.get('nodeid', '') or '').strip()}:{str(target.get('field_key', '') or '').strip()}"
                for target in param_targets
            ),
            *(
                f"param:*:{str(target.get('field_key', '') or '').strip()}"
                for target in param_targets
            ),
            *(
                f"env:{str(target.get('kind', '') or '').strip()}:{str(target.get('field_key', '') or '').strip()}"
                for target in env_targets
            ),
        }
        self._trace(
            "context_refresh_start",
            reason=reason,
            params=len(param_targets),
            env=len(env_targets),
        )
        self.stateChanged.emit()
        self._create_task(
            self._refresh_context_task(selected_duts, param_targets, env_targets),
            label="context_refresh",
        )

    async def _refresh_adb_devices_task(
        self,
        selected_serial: str = "",
        *,
        prepare_android_client: bool = True,
    ) -> None:
        started_at = time.monotonic()
        try:
            devices = await self._parameter_helper.refresh_duts_async(selected_serial=selected_serial)
        except Exception as exc:  # noqa: BLE001
            self._trace("adb_refresh_error", error=exc)
            devices = []
            self._finish_adb_refresh(error=str(exc), started_at=started_at)
            return
        self._publish_adb_devices(devices, int((time.monotonic() - started_at) * 1000))
        if not prepare_android_client:
            self._finish_adb_refresh(started_at=started_at)
            return
        target = selected_serial if selected_serial in self._adb_devices else ""
        if not target and len(self._adb_devices) == 1:
            target = self._adb_devices[0]
        if not target:
            self._finish_adb_refresh(started_at=started_at)
            return
        try:
            await self._parameter_helper.prepare_android_client_async(
                target,
                stage_callback=lambda stage, value, detail: self._refreshStageChanged.emit(
                    str(stage), int(value), str(detail)
                ),
            )
        except Exception as exc:  # noqa: BLE001
            self._trace("android_client_prepare_error", phase=self._adb_refresh_phase, error=exc)
            self._finish_adb_refresh(error=str(exc), started_at=started_at)
            return
        self._finish_adb_refresh(started_at=started_at)

    async def _refresh_context_task(
        self,
        selected_serials: list[str],
        param_targets: list[dict[str, str]],
        env_targets: list[dict[str, str]],
    ) -> None:
        started_at = time.monotonic()
        try:
            param_results: list[dict[str, Any]] = []
            env_results: list[dict[str, Any]] = []
            for index, selected_serial in enumerate(selected_serials or [""]):
                result = await self._parameter_helper.refresh_context_async(
                    selected_serial=selected_serial or None,
                    param_targets=param_targets,
                    env_targets=env_targets if index == 0 else [],
                )
                param_results.extend(result.param_results)
                env_results.extend(result.env_results)
            payload = {
                "param_results": param_results,
                "env_results": env_results,
                "elapsed_ms": int((time.monotonic() - started_at) * 1000),
            }
        except Exception as exc:  # noqa: BLE001
            self._trace("context_refresh_error", error=exc)
            payload = {"param_results": [], "env_results": [], "elapsed_ms": int((time.monotonic() - started_at) * 1000)}
        self._apply_context_refresh_result(payload)

    @Slot("QVariantList", int)
    def _apply_adb_refresh_result(self, devices: list[Any], elapsed_ms: int) -> None:
        self._publish_adb_devices(devices, elapsed_ms)
        self._finish_adb_refresh(started_at=time.monotonic())

    def _publish_adb_devices(self, devices: list[Any], elapsed_ms: int) -> None:
        normalized: list[str] = []
        for value in devices:
            serial = str(value or "").strip()
            if serial and serial not in normalized:
                normalized.append(serial)
        self._adb_devices = normalized
        changed = self._sync_dut_selection()
        if changed:
            save_state(self._state_path, self._state)
        self._trace("adb_refresh_done", devices=len(self._adb_devices), elapsed_ms=elapsed_ms)
        self.stateChanged.emit()

    @Slot(str, int, str)
    def _apply_refresh_stage(self, stage: str, value: int, detail: str) -> None:
        self._adb_refresh_phase = str(stage or "prepare")
        self._adb_refresh_progress = max(0, min(100, int(value)))
        labels = {
            "check_apk": self.tr("Checking Android Client package"),
            "install_status": self.tr("Checking installation status"),
            "root": self.tr("Requesting ADB root"),
            "remount": self.tr("Remounting system partitions"),
            "push": self.tr("Pushing Android Client files"),
            "provision": self.tr("Provisioning Android Client"),
            "reboot": self.tr("Rebooting DUT"),
            "wait_online": self.tr("Waiting for DUT to come online"),
            "install": self.tr("Installing Android Client"),
            "verify": self.tr("Verifying Android Client"),
        }
        self._adb_refresh_detail = labels.get(self._adb_refresh_phase, str(detail or ""))
        self.stateChanged.emit()

    def _finish_adb_refresh(self, *, started_at: float, error: str = "") -> None:
        self._adb_refresh_running = False
        self._adb_refresh_error = str(error or "")
        if error:
            failed_phase = self._adb_refresh_phase
            self._adb_refresh_phase = "failed"
            self._adb_refresh_detail = f"{failed_phase}: {error}"
        else:
            self._adb_refresh_phase = "complete"
            self._adb_refresh_progress = 100
            self._adb_refresh_detail = self.tr("DUT refresh completed")
        self._trace(
            "adb_refresh_sequence_done",
            elapsed_ms=int((time.monotonic() - started_at) * 1000),
            error=error or "<none>",
        )
        self._schedule_context_refresh("adb_refresh_done")
        self.stateChanged.emit()

    @Property(bool, notify=stateChanged)
    def dutRefreshRunning(self) -> bool:
        return self._adb_refresh_running

    @Property(str, notify=stateChanged)
    def dutRefreshPhase(self) -> str:
        return self._adb_refresh_phase

    @Property(int, notify=stateChanged)
    def dutRefreshProgress(self) -> int:
        return self._adb_refresh_progress

    @Property(str, notify=stateChanged)
    def dutRefreshDetail(self) -> str:
        return self._adb_refresh_detail

    @Property(str, notify=stateChanged)
    def dutRefreshError(self) -> str:
        return self._adb_refresh_error

    @Slot("QVariantMap")
    def _apply_context_refresh_result(self, payload: dict[str, Any]) -> None:
        self._bind_params_state()
        self._context_refresh_running = False
        self._dynamic_fields_refreshing.clear()
        elapsed_ms = int(payload.get("elapsed_ms", 0) or 0)
        changed = False
        state_synced = False
        for result in list(payload.get("param_results", []) or []):
            source = str(result.get("source", "") or "").strip()
            nodeid = str(result.get("nodeid", "") or "").strip()
            field_key = str(result.get("field_key", "") or "").strip()
            dut_serial = str(result.get("dut_serial", "") or "").strip()
            options = _storage_playback_options(normalize_option_values(result.get("options", [])))
            error = str(result.get("error", "") or "").strip()
            if error:
                if self._set_case_param_options(nodeid=nodeid, key=field_key, options=[]):
                    changed = True
                    state_synced = True
                self._trace(
                    "param_options_refresh_error",
                    source=source,
                    nodeid=nodeid or "<none>",
                    field=field_key or "<none>",
                    elapsed_ms=elapsed_ms,
                    error=error,
                )
                continue
            if self._set_case_param_options(nodeid=nodeid, key=field_key, options=options):
                changed = True
                state_synced = True
            if self._sync_dynamic_values_from_options(source=source, nodeid=nodeid, options=options, dut=dut_serial):
                state_synced = True
        for result in list(payload.get("env_results", []) or []):
            kind = str(result.get("kind", "") or "").strip().lower()
            field_key = str(result.get("field_key", "") or "").strip()
            device_type = str(result.get("device_type", "") or "").strip()
            cache_key = f"{kind}:{device_type}:{field_key}"
            options = list(result.get("options", []) or [])
            error = str(result.get("error", "") or "").strip()
            if error:
                self._trace(
                    "env_options_refresh_error",
                    cache_key=cache_key,
                    elapsed_ms=elapsed_ms,
                    error=error,
                )
                continue
            if self._env_options.get(cache_key, []) != options:
                self._env_options[cache_key] = options
                changed = True
            self._trace(
                "env_options_refresh_result",
                cache_key=cache_key,
                options=len(options),
                changed=changed,
            )
        if state_synced:
            save_state(self._state_path, self._state)
        self._trace("context_refresh_done", elapsed_ms=elapsed_ms, changed=changed, state_synced=state_synced)
        self.stateChanged.emit()

    def _set_case_param_options(self, *, nodeid: str, key: str, options: list[str]) -> bool:
        normalized_nodeid = str(nodeid or "").strip()
        normalized_key = str(key or "").strip()
        if not normalized_nodeid or not normalized_key:
            return False
        normalized_options = _storage_playback_options(normalize_option_values(options))
        node_options = self._state.case_parameter_options.setdefault(normalized_nodeid, {})
        changed = node_options.get(normalized_key, []) != normalized_options
        if changed:
            node_options[normalized_key] = normalized_options
            self._trace(
                "param_options_saved",
                nodeid=normalized_nodeid,
                field=normalized_key,
                options=len(normalized_options),
            )
        if self._prune_case_param_value_to_options(nodeid=normalized_nodeid, key=normalized_key, options=normalized_options):
            changed = True
        return changed

    def _clear_case_param_options(self, targets: list[dict[str, str]] | None = None) -> bool:
        if targets is None:
            if not self._state.case_parameter_options:
                return False
            count = sum(len(values) for values in self._state.case_parameter_options.values() if isinstance(values, dict))
            self._state.case_parameter_options = {}
            self._trace("param_options_cleared", fields=count)
            return True
        changed = False
        for target in targets:
            nodeid = str(target.get("nodeid", "") or "").strip()
            field_key = str(target.get("field_key", "") or "").strip()
            if not nodeid or not field_key:
                continue
            node_options = self._state.case_parameter_options.get(nodeid)
            if not isinstance(node_options, dict) or field_key not in node_options:
                continue
            node_options.pop(field_key, None)
            if not node_options:
                self._state.case_parameter_options.pop(nodeid, None)
            self._trace("param_options_cleared", nodeid=nodeid, field=field_key)
            changed = True
        return changed

    def _prune_case_param_value_to_options(self, *, nodeid: str, key: str, options: list[str]) -> bool:
        changed, previous_count, next_count = smarttest_context().params.prune_multi_enum_to_options(
            nodeid=nodeid,
            key=key,
            options=options,
        )
        if not changed:
            return False
        self._trace(
            "param_multi_enum_pruned_to_options",
            nodeid=nodeid,
            field=key,
            previous=previous_count,
            next=next_count,
        )
        return True

    def _sync_dynamic_values_from_options(self, *, source: str, nodeid: str, options: list[str], dut: str = "") -> bool:
        normalized_source = str(source or "").strip()
        if not normalized_source:
            return False
        normalized_nodeid = str(nodeid or "").strip()
        next_options = _storage_playback_options(normalize_option_values(options))
        changed = False
        for selected in self._state.selected:
            if normalized_nodeid and str(selected.nodeid or "").strip() != normalized_nodeid:
                continue
            case = self._case_info(selected.nodeid)
            if case is None:
                continue
            case_nodeid = str(case.get("nodeid", "") or "").strip()
            if not case_nodeid:
                continue
            for param_key in case_param_keys(case):
                field = self._field_for_key(str(param_key))
                if field is None or str(field.options_source or "").strip() != normalized_source:
                    continue
                next_value = _dynamic_value_for_field(field, next_options)
                current = smarttest_context().params.dut_dynamic_value(
                    dut,
                    field.key,
                    smarttest_context().params.case_display_value(case, field.key),
                )
                if next_value == current:
                    continue
                if not self._set_dut_dynamic_param_value(dut=dut, key=field.key, value=next_value):
                    continue
                self._trace(
                    "param_options_synced_selected_values",
                    nodeid=case_nodeid,
                    dut=dut or "<none>",
                    field=field.key,
                    source=normalized_source,
                    previous=_value_size(current),
                    next=_value_size(next_value),
                )
                changed = True
        return changed

    def _sync_dut_selection(self) -> bool:
        self._bind_params_state()
        current_values = [serial for serial in self._selected_dut_serials() if serial in self._adb_devices]
        if current_values != self._selected_dut_serials():
            return self._set_selected_dut_serials(current_values)
        if current_values:
            return self._set_selected_dut_serials(current_values)
        if len(self._adb_devices) == 1:
            only_device = self._adb_devices[0]
            return self._set_selected_dut_serials([only_device])
        return False

    def _selected_dynamic_param_targets(self) -> list[dict[str, str]]:
        targets: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for selected in self._state.selected:
            case = self._case_info(selected.nodeid)
            if case is None:
                self._trace("dynamic_param_targets_case_missing", nodeid=selected.nodeid)
                continue
            nodeid = str(selected.nodeid or "").strip()
            for param_key in case_param_keys(case):
                field = self._field_for_key(str(param_key))
                source = str(field.options_source if field is not None else "").strip()
                target = (nodeid, str(param_key), source)
                if source and target not in seen:
                    seen.add(target)
                    targets.append({"nodeid": nodeid, "field_key": str(param_key), "source": source})
        self._trace(
            "dynamic_param_targets",
            selected=len(self._state.selected),
            targets=",".join(
                f"{str(item.get('field_key', ''))}@{str(item.get('nodeid', ''))}"
                for item in targets
            ) or "<none>",
            dut=self._current_dut_serial() or "<default>",
        )
        return targets

    def _selected_dynamic_env_targets(self) -> list[dict[str, str]]:
        targets: list[dict[str, str]] = []
        seen: set[tuple[str, str, str]] = set()
        for kind in self._selected_required_equipment():
            config = self._equipment_config().get(kind, {})
            config = dict(config) if isinstance(config, dict) else {}
            device_type = str(config.get("type") or default_env_device_type(kind)).strip()
            if not device_type:
                continue
            for target in self._parameter_helper.env_targets_for_kind(kind=kind, device_type=device_type):
                identity = (
                    str(target.get("kind", "") or ""),
                    str(target.get("device_type", "") or ""),
                    str(target.get("field_key", "") or ""),
                )
                if identity in seen:
                    continue
                seen.add(identity)
                targets.append(target)
        self._trace(
            "dynamic_env_targets",
            targets=",".join(
                f"{str(item.get('kind', ''))}:{str(item.get('field_key', ''))}"
                for item in targets
            ) or "<none>",
        )
        return targets

    def _refresh_options_affected_by_param_change(self, *, nodeid: str, key: str) -> None:
        field = self._field_for_key(key)
        if field is None:
            return
        sources = [str(source or "").strip() for source in field.refreshes_options_sources]
        sources = [source for source in sources if source]
        if not sources:
            return
        selected_nodeids = set(self._selected_nodeids())
        normalized_nodeid = str(nodeid or "").strip()
        if normalized_nodeid not in selected_nodeids:
            self._trace(
                "param_dependency_refresh_skip_unselected",
                nodeid=normalized_nodeid or "<none>",
                key=key,
            )
            return
        self._schedule_context_refresh(f"param_dependency_changed:{key}")

    def _param_text_key(self, param_key: str, part: str) -> str:
        normalized = str(param_key or "").strip().replace(":", ".")
        return f"test.param.{normalized}.{part}"

    def _field_label(self, field: ParamField) -> str:
        return self._param_text_key(field.key, "label")

    def _field_description(self, field: ParamField) -> str:
        return self._param_text_key(field.key, "description")

    def _scope_label(self, scope: Any) -> str:
        raw_scope = scope.value if hasattr(scope, "value") else str(scope or "")
        labels = {
            ParamScope.GLOBAL_CONTEXT.value: "test.param.scope.global_context",
            ParamScope.CASE_TYPE_SHARED.value: "test.param.scope.case_type_shared",
            ParamScope.CASE.value: "test.param.scope.case",
        }
        return labels.get(str(raw_scope), labels[ParamScope.CASE.value])

    def _schema_to_jsonable(self, schema: ParamSchema) -> dict[str, Any]:
        return {
            "schema_id": schema.schema_id,
            "title": f"test.schema.{schema.schema_id}.title",
            "title_source": "fixed",
            "fields": [self._field_to_jsonable(f) for f in schema.fields],
        }

    def _field_to_jsonable(
        self,
        field: ParamField,
        *,
        nodeid: str = "",
        value: Any = None,
        has_value: bool = False,
        readonly: bool | None = None,
        dut_serial: str = "",
        loading_owner: str | None = None,
    ) -> dict[str, Any]:
        if has_value:
            resolved_value = value
        elif nodeid:
            case = self._case_info(nodeid)
            resolved_value = smarttest_context().params.case_display_value(case or {}, field.key)
        elif field.scope == ParamScope.GLOBAL_CONTEXT:
            resolved_value = smarttest_context().params.global_value(field.key, field.default)
        else:
            resolved_value = field.default
        if readonly is None:
            readonly = str(getattr(field, "source_kind", "") or "").strip() == "dut_dynamic"
        owner = nodeid if loading_owner is None else loading_owner
        return {
            "key": field.key,
            "label": self._field_label(field),
            "label_source": "fixed",
            "type": field.type.value if hasattr(field.type, "value") else field.type,
            "category": field.category.value if hasattr(field.category, "value") else field.category,
            "scope": field.scope.value if hasattr(field.scope, "value") else field.scope,
            "scope_label": self._scope_label(field.scope),
            "scope_label_source": "fixed",
            "default": field.default,
            "description": self._field_description(field),
            "description_source": "fixed",
            "group": field.group,
            "required": False,
            "enum_values": self._enum_values_for_field(field, nodeid=nodeid),
            "enum_values_source": "dynamic" if field.options_source else "fixed",
            "enum_labels": {
                value: self._param_text_key(field.key, f"option.{value}")
                for value in field.enum_values
            } if field.key == "iptv_middle_screen_067:image_viewers" else {},
            "enum_labels_source": "fixed",
            "source_kind": str(getattr(field, "source_kind", "") or "user_input"),
            "options_source": field.options_source,
            "refreshes_options_sources": list(field.refreshes_options_sources),
            "value_source": "state",
            "dut_serial": dut_serial,
            "value": resolved_value,
            "list_values": normalize_option_values(resolved_value) if self._is_multi_enum_field(field) else [],
            "readonly": readonly,
            "loading": self._is_dynamic_field_loading(kind="param", owner=owner, key=field.key),
        }

    def _set_dut_dynamic_param_value(self, *, dut: str, key: str, value: Any) -> bool:
        field = self._field_for_key(key)
        if field is None:
            self._trace("dut_dynamic_value_skip_unknown_field", dut=str(dut or "").strip() or "<none>", field=key)
            return False
        normalized_dut = str(dut or "").strip()
        if not normalized_dut:
            self._trace("dut_dynamic_value_skip_no_dut", field=field.key)
            return False
        current = smarttest_context().params.dut_dynamic_value(normalized_dut, field.key)
        changed = smarttest_context().params.set_dut_dynamic_value(normalized_dut, field.key, value)
        next_value = smarttest_context().params.dut_dynamic_value(normalized_dut, field.key)
        if not changed:
            self._trace("dut_dynamic_value_unchanged", dut=normalized_dut, field=field.key, value=_value_size(next_value))
            return False
        self._trace(
            "dut_dynamic_value_changed",
            dut=normalized_dut,
            field=field.key,
            previous=_value_size(current),
            value=_value_size(next_value),
        )
        return True

    def _selected_dut_dynamic_fields(self) -> list[ParamField]:
        fields: list[ParamField] = []
        seen: set[str] = set()
        for selected in self._state.selected:
            case = self._case_info(selected.nodeid)
            if case is None:
                continue
            for param_key in case_param_keys(case):
                field = self._field_for_key(str(param_key))
                if field is None:
                    continue
                if str(getattr(field, "source_kind", "") or "").strip() != "dut_dynamic":
                    continue
                if field.key in seen:
                    continue
                seen.add(field.key)
                fields.append(field)
        return fields

    def _is_dynamic_field_loading(self, *, kind: str, owner: str, key: str) -> bool:
        return f"{kind}:{str(owner or '').strip()}:{str(key or '').strip()}" in self._dynamic_fields_refreshing

    def _enum_values_for_field(self, field: ParamField, *, nodeid: str = "") -> list[str]:
        if field.key == "dut":
            return list(self._adb_devices)
        source = str(field.options_source or "").strip()
        if source:
            values = _storage_playback_options(
                list(self._state.case_parameter_options.get(str(nodeid or "").strip(), {}).get(field.key, []))
            )
            self._trace(
                "param_options_read_state",
                field=field.key,
                source=source,
                dut=self._current_dut_serial() or "<default>",
                nodeid=str(nodeid or "").strip() or "<none>",
                options=len(values),
            )
            return values
        return list(field.enum_values)

    def _rebuild_case_indexes(self) -> None:
        self._cases_by_nodeid = {}
        self._cases_by_file = {}
        for case in self._cases:
            nodeid = str(case.get("nodeid", "")).strip()
            file_path = str(case.get("file", "")).strip()
            if nodeid:
                self._cases_by_nodeid[nodeid] = case
            if file_path:
                self._cases_by_file.setdefault(file_path, []).append(case)

    def _migrate_legacy_android_selection(self) -> bool:
        by_android_id = {
            str(case.get("android_case_id", "") or ""): str(case.get("nodeid", "") or "")
            for case in self._cases
            if str(case.get("android_case_id", "") or "") and str(case.get("nodeid", "") or "")
        }
        if not by_android_id:
            return False

        changed = False
        for selected in self._state.selected:
            raw_nodeid = str(selected.nodeid or "").strip()
            if not raw_nodeid.startswith("android://"):
                continue
            case_id = raw_nodeid[len("android://") :].strip()
            mapped_nodeid = by_android_id.get(case_id, "")
            if not mapped_nodeid:
                self._trace("legacy_android_selection_unmapped", case_id=case_id)
                continue
            selected.nodeid = mapped_nodeid
            selected.case_type = str((self._case_info(mapped_nodeid) or {}).get("case_type", selected.case_type))
            smarttest_context().params.migrate_case_nodeid(old_nodeid=raw_nodeid, new_nodeid=mapped_nodeid)
            self._trace("legacy_android_selection_migrated", from_nodeid=raw_nodeid, to_nodeid=mapped_nodeid)
            changed = True
        return changed

    def _case_info(self, nodeid: str) -> dict[str, Any] | None:
        normalized = (nodeid or "").strip()
        if not normalized:
            return None
        return self._cases_by_nodeid.get(normalized)

    def _field_for_key(self, key: str) -> ParamField | None:
        normalized = (key or "").strip()
        if not normalized:
            return None
        return self._registry.get_param(normalized)

    def _is_multi_enum_field(self, field: ParamField) -> bool:
        field_type = field.type.value if hasattr(field.type, "value") else field.type
        return field_type == ParamValueType.MULTI_ENUM.value

    def _cases_for_file(self, file_path: str) -> list[dict[str, Any]]:
        normalized = (file_path or "").strip()
        if not normalized:
            return []
        return list(self._cases_by_file.get(normalized, ()))

    def _trimmed_case_path(self, file_path: str) -> str:
        path = str(file_path or "").strip()
        prefix = "testing/tests/"
        if path.startswith(prefix):
            return path[len(prefix) :]
        return path

    def _file_base_name(self, file_path: str) -> str:
        cases = self._cases_for_file(file_path)
        if len(cases) == 1:
            return str(cases[0].get("title", "") or cases[0].get("name", "") or file_path)
        short_file = self._trimmed_case_path(file_path)
        return short_file.split("/")[-1] if short_file else short_file

    def _selected_file_paths_from_cases(self) -> list[str]:
        file_paths: list[str] = []
        seen: set[str] = set()
        for selected in self._state.selected:
            case_info = self._case_info(selected.nodeid)
            if case_info is None:
                continue
            file_path = str(case_info.get("file", "")).strip()
            if not file_path or file_path in seen:
                continue
            seen.add(file_path)
            file_paths.append(file_path)
        return file_paths

    def _reorder_selected_cases_by_file_order(self) -> bool:
        if not self._cases or not self._state.selected:
            return False

        grouped: dict[str, list[SelectedCase]] = {}
        remaining_files: list[str] = []
        for selected in self._state.selected:
            case_info = self._case_info(selected.nodeid)
            if case_info is None:
                continue
            file_path = str(case_info.get("file", "")).strip()
            if not file_path:
                continue
            grouped.setdefault(file_path, []).append(selected)
            if file_path not in remaining_files:
                remaining_files.append(file_path)

        ordered_files = [path for path in self._state.selected_files if path in grouped]
        ordered_files.extend(path for path in remaining_files if path not in ordered_files)
        reordered: list[SelectedCase] = []
        for file_path in ordered_files:
            reordered.extend(grouped.get(file_path, []))

        if [c.nodeid for c in reordered] == [c.nodeid for c in self._state.selected]:
            return False
        self._state.selected = reordered
        return True

    def _sync_selected_file_order(self) -> bool:
        selected_files = self._selected_file_paths_from_cases()
        next_order: list[str] = []
        seen: set[str] = set()

        for file_path in self._state.selected_files:
            if file_path in selected_files and file_path not in seen:
                seen.add(file_path)
                next_order.append(file_path)

        for file_path in selected_files:
            if file_path not in seen:
                seen.add(file_path)
                next_order.append(file_path)

        if next_order == self._state.selected_files:
            return False
        self._state.selected_files = next_order
        return True

    def _resolve_case_value(self, *, nodeid: str, key: str) -> Any:
        case = self._case_info(nodeid)
        if case is None:
            return None
        return smarttest_context().params.case_display_value(case, key)

    def _case_param_display_model(self, nodeid: str) -> dict[str, Any]:
        case = self._case_info(nodeid)
        if case is None:
            return {}

        required_params = case_param_keys(case)
        required_param_key_set = set(required_param_keys(case))
        fields: list[dict[str, Any]] = []
        for param_key in required_params:
            field = self._field_for_key(str(param_key))
            if field is None:
                continue
            row = self._field_to_jsonable(field, nodeid=str(case.get("nodeid", "")))
            row["required"] = field.key in required_param_key_set
            fields.append(row)

        return {
            "nodeid": str(case.get("nodeid", "")),
            "nodeid_source": "dynamic",
            "file": str(case.get("file", "")),
            "file_source": "dynamic",
            "name": str(case.get("name", "")),
            "name_source": "dynamic",
            "case_type": str(case.get("case_type", "default")),
            "case_type_source": "dynamic",
            "required_params": required_params,
            "required_param_groups": list(case.get("required_param_groups", [])),
            "required_equipment": env_kinds_for_case(case),
            "fields": fields,
            "fields_source": "display_model",
        }

    def _set_case_param_value(self, *, nodeid: str, key: str, value: Any) -> bool:
        self._bind_params_state()
        case = self._case_info(nodeid)
        if case is None:
            return False
        return smarttest_context().params.set_case_display_value(case, key, value)

    def _selected_nodeids(self) -> list[str]:
        return [c.nodeid for c in self._state.selected]

    def _set_case_selected(self, nodeid: str, selected: bool) -> bool:
        self._bind_params_state()
        normalized = (nodeid or "").strip()
        if not normalized:
            return False

        if selected:
            if any(c.nodeid == normalized for c in self._state.selected):
                return False
            case_type = "default"
            for case in self._cases:
                if case.get("nodeid") == normalized:
                    case_type = str(case.get("case_type") or "default")
                    break
            self._state.selected.append(SelectedCase(nodeid=normalized, case_type=case_type))
            case_info = self._case_info(normalized)
            if case_info is not None:
                file_path = str(case_info.get("file", "")).strip()
                if file_path and file_path not in self._state.selected_files:
                    self._state.selected_files.append(file_path)
            smarttest_context().params.ensure_case_defaults(nodeid=normalized, case_type=case_type)
            return True

        original_len = len(self._state.selected)
        self._state.selected = [case for case in self._state.selected if case.nodeid != normalized]
        case_info = self._case_info(normalized)
        if case_info is not None:
            file_path = str(case_info.get("file", "")).strip()
            if file_path:
                still_selected = any(
                    str((self._case_info(case.nodeid) or {}).get("file", "")).strip() == file_path
                    for case in self._state.selected
                )
                if not still_selected:
                    self._state.selected_files = [path for path in self._state.selected_files if path != file_path]
        # Keep per-case configs on deselect so previously entered values can be restored
        # when the user selects the same case again in a later session.
        return len(self._state.selected) != original_len

    @Slot()
    def discoverCases(self) -> None:
        started_at = time.monotonic()
        self._trace(
            "discover_start",
            packaged=is_packaged_runtime(),
            root_dir=str(self._root_dir),
            executable=sys.executable,
        )
        if self._discovery_loaded and self._cases:
            self._trace(
                "discover_cached",
                cases=len(self._cases),
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            )
            self.casesChanged.emit()
            return
        if self._discovery_running:
            self._trace(
                "discover_skip_running",
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            )
            return
        self._discovery_running = True
        if not self._adb_refresh_started:
            self._adb_refresh_started = True
            self._schedule_adb_refresh("page_init_async")
        self._create_task(self._discover_cases_task(started_at), label="discover_cases")

    async def _discover_cases_task(self, started_at: float) -> None:
        if is_packaged_runtime():
            load_started = time.monotonic()
            cases = await self._tasks.to_thread("discover_catalog", load_packaged_test_catalog)
            self._trace(
                "catalog_loaded",
                cases=len(cases),
                elapsed_ms=int((time.monotonic() - load_started) * 1000),
            )
            if not cases:
                self._apply_discovery_error(
                    "Packaged test catalog is missing or empty.",
                    int((time.monotonic() - started_at) * 1000),
                )
                return
            rows = [
                {
                    "nodeid": str(c.get("nodeid", "")),
                    "file": str(c.get("file", "")),
                    "name": str(c.get("name", "")),
                    "markers": [str(m) for m in list(c.get("markers") or [])],
                    "case_type": str(c.get("case_type", "default")),
                    "required_params": [str(p) for p in list(c.get("required_params") or [])],
                    "required_param_groups": [str(g) for g in list(c.get("required_param_groups") or [])],
                    "required_equipment": [str(e) for e in list(c.get("required_equipment") or [])],
                    "android_case_id": str(c.get("android_case_id", "")),
                }
                for c in cases
            ]
            self._apply_discovery_result(rows, int((time.monotonic() - started_at) * 1000))
            return
        try:
            collect_started = time.monotonic()
            cases = await self._tasks.to_thread(
                "discover_cases", discover_pytest_cases,
                root_dir=self._root_dir,
                python_executable=sys.executable,
            )
            self._trace(
                "pytest_collect_done",
                cases=len(cases),
                elapsed_ms=int((time.monotonic() - collect_started) * 1000),
            )
        except PytestDiscoveryError as e:
            self._apply_discovery_error(str(e), int((time.monotonic() - started_at) * 1000))
            return
        except Exception as e:  # noqa: BLE001
            self._apply_discovery_error(f"Pytest discovery failed: {e}", int((time.monotonic() - started_at) * 1000))
            return

        rows = [
            {
                "nodeid": c.nodeid,
                "file": c.file,
                "name": c.name,
                "markers": c.markers,
                "case_type": c.case_type,
                "required_params": c.required_params,
                "required_param_groups": c.required_param_groups,
                "required_equipment": c.required_equipment,
                "android_case_id": c.android_case_id,
            }
            for c in cases
        ]
        self._apply_discovery_result(rows, int((time.monotonic() - started_at) * 1000))

    @Slot("QVariantList", int)
    def _apply_discovery_result(self, rows: list[dict[str, Any]], elapsed_ms: int) -> None:
        self._cases = [dict(row) for row in rows]
        self._rebuild_case_indexes()
        changed = self._migrate_legacy_android_selection()
        changed = self._sync_selected_file_order() or changed
        changed = self._reorder_selected_cases_by_file_order() or changed
        if changed:
            save_state(self._state_path, self._state)
        self._discovery_running = False
        self._discovery_loaded = True
        self.casesChanged.emit()
        self._trace(
            "discover_done",
            cases=len(self._cases),
            elapsed_ms=elapsed_ms,
        )
        self._schedule_context_refresh("discover_done")

    @Slot(str, int)
    def _apply_discovery_error(self, message: str, elapsed_ms: int) -> None:
        self._discovery_running = False
        self._trace("discover_error", elapsed_ms=elapsed_ms, error=message)
        self.errorOccurred.emit(message)

    @Slot(result="QVariantList")
    def cases(self):
        return self._cases

    @Slot(result="QVariantList")
    def caseRows(self):
        selected = set(self._selected_nodeids())
        rows = [
            {
                "nodeid": str(item.get("nodeid", "")),
                "file": str(item.get("file", "")),
                "name": str(item.get("name", "")),
                "markers": list(item.get("markers", [])),
                "case_type": str(item.get("case_type", "default")),
                "required_params": list(item.get("required_params", [])),
                "required_param_groups": list(item.get("required_param_groups", [])),
                "required_equipment": list(item.get("required_equipment", [])),
                "selected": str(item.get("nodeid", "")) in selected,
            }
            for item in self._cases
        ]
        return rows

    @Slot(result="QVariantList")
    def selectedCases(self):
        selected = [{"nodeid": c.nodeid, "case_type": c.case_type} for c in self._state.selected]
        return selected

    @Slot(result="QVariantList")
    def selectedFiles(self):
        self._sync_selected_file_order()
        return list(self._state.selected_files)

    @Slot(result="QVariantList")
    def selectedFileRows(self):
        self._sync_selected_file_order()
        rows: list[dict[str, Any]] = []
        for file_path in self._state.selected_files:
            rows.append(
                {
                    "file": file_path,
                    "file_source": "dynamic",
                    "name": self._file_base_name(file_path),
                    "name_source": "dynamic",
                    "short_file": self._trimmed_case_path(file_path),
                    "short_file_source": "dynamic",
                }
            )
        return rows

    @Slot(result="QVariantList")
    def selectedCaseParamRows(self):
        self._sync_selected_file_order()
        rows: list[dict[str, Any]] = []
        for file_path in self._state.selected_files:
            for case in self._cases_for_file(file_path):
                model = self._case_param_display_model(str(case.get("nodeid", "")))
                if model:
                    rows.append(model)
        return rows

    @Slot(result="QVariantList")
    def activeCaseTypes(self):
        types: list[str] = []
        seen: set[str] = set()
        self._sync_selected_file_order()
        for file_path in self._state.selected_files:
            for case in self._cases_for_file(file_path):
                case_type = str(case.get("case_type", "default"))
                if case_type in seen:
                    continue
                seen.add(case_type)
                types.append(case_type)
        return types

    def _selected_required_equipment(self) -> list[str]:
        required: list[str] = []
        seen: set[str] = set()
        for selected in self._state.selected:
            case = self._case_info(selected.nodeid)
            if case is None:
                continue
            for value in env_kinds_for_case(case):
                kind = str(value or "").strip().lower()
                if not kind or kind in seen:
                    continue
                seen.add(kind)
                required.append(kind)
        return required

    def _equipment_config(self) -> dict[str, Any]:
        return smarttest_context().params.equipment_config()

    def _env_default_config(self, kind: str, device_type: str | None = None) -> dict[str, Any]:
        return default_env_config(kind, device_type)

    def _env_equipment_row(self, kind: str) -> dict[str, Any] | None:
        normalized_kind = str(kind or "").strip().lower()
        config = self._equipment_config().get(normalized_kind, {})
        config = dict(config) if isinstance(config, dict) else {}
        device_type = str(config.get("type") or default_env_device_type(normalized_kind)).strip()
        self._trace(
            "env_row_build",
            kind=normalized_kind,
            type=device_type or "<none>",
            option_keys=",".join(sorted(self._env_options.keys())) or "<none>",
        )
        return build_env_equipment_row(
            kind=normalized_kind,
            config=config,
            env_options=self._env_options,
            is_loading=lambda kind, owner, key: self._is_dynamic_field_loading(
                kind=kind,
                owner=owner,
                key=key,
            ),
            tr=self.tr,
        )

    @Slot(result="QVariantList")
    def envEquipmentRows(self):
        rows: list[dict[str, Any]] = []
        for kind in self._selected_required_equipment():
            row = self._env_equipment_row(kind)
            if row is not None:
                rows.append(row)
        return rows

    @Slot(str, "QVariantList", result="QVariantList")
    def caseTree(self, filter_text: str, expanded_keys: list[Any]):
        selected_files = set(self.selectedFiles())
        selected = set(self._selected_nodeids())
        expanded = {str(item) for item in (expanded_keys or []) if str(item)}
        normalized_filter = str(filter_text or "").strip().lower()

        root: dict[str, Any] = {"folders": [], "files": []}

        def case_matches(file_path: str, case_name: str) -> bool:
            if not normalized_filter:
                return True
            short_file = self._trimmed_case_path(file_path).lower()
            return (
                normalized_filter in str(file_path or "").lower()
                or normalized_filter in short_file
                or normalized_filter in str(case_name or "").lower()
            )

        def sort_nodes(nodes: list[dict[str, Any]]) -> None:
            nodes.sort(key=lambda item: str(item.get("label", "")))

        for case in self._cases:
            file_path = str(case.get("file", "")).strip()
            if not file_path:
                continue
            if not case_matches(file_path, str(case.get("name", ""))):
                continue

            short_file = self._trimmed_case_path(file_path)
            parts = short_file.split("/") if short_file else []
            folder_parts = parts[:-1]
            file_name = parts[-1] if parts else short_file
            parent = root
            folder_path = ""
            for part in folder_parts:
                folder_path = f"{folder_path}/{part}" if folder_path else part
                folder_key = f"folder:{folder_path}"
                folder_node = next((item for item in parent["folders"] if item["key"] == folder_key), None)
                if folder_node is None:
                    folder_node = {
                        "key": folder_key,
                        "label": part,
                        "label_source": "dynamic",
                        "folders": [],
                        "files": [],
                    }
                    parent["folders"].append(folder_node)
                parent = folder_node

            file_key = f"file:{short_file}"
            if not any(item["key"] == file_key for item in parent["files"]):
                parent["files"].append(
                    {
                        "key": file_key,
                        "label": file_name,
                        "label_source": "dynamic",
                        "file": file_path,
                        "file_source": "dynamic",
                        "checked": file_path in selected_files,
                        "nodeids": [],
                    }
                )
            file_node = next(item for item in parent["files"] if item["key"] == file_key)
            nodeid = str(case.get("nodeid", ""))
            if nodeid and nodeid not in file_node["nodeids"]:
                file_node["nodeids"].append(nodeid)

        force_expand = bool(normalized_filter)

        def build_folder(node: dict[str, Any]) -> dict[str, Any]:
            sort_nodes(node["folders"])
            sort_nodes(node["files"])
            children = [build_folder(item) for item in node["folders"]]
            children.extend(build_file(item) for item in node["files"])
            nodeids = [nodeid for child in children for nodeid in child.get("_nodeids", [])]
            selected_count = sum(nodeid in selected for nodeid in nodeids)
            selection_state = _selection_state(selected_count, len(nodeids))
            return {
                "title": node["label"],
                "title_source": node.get("label_source", "dynamic"),
                "_key": node["key"],
                "rowType": "folder",
                "iconSource": "Folder",
                "expanded": force_expand or node["key"] in expanded,
                "children": children,
                "selectionState": selection_state,
                "checked": selection_state == "checked",
                "selectableCount": len(nodeids),
                "selectedCount": selected_count,
                "_nodeids": nodeids,
            }

        def build_file(node: dict[str, Any]) -> dict[str, Any]:
            nodeids = list(node.get("nodeids", []))
            return {
                "title": node["label"],
                "title_source": node.get("label_source", "dynamic"),
                "_key": node["key"],
                "rowType": "file",
                "iconSource": "Document",
                "file": node["file"],
                "file_source": node.get("file_source", "dynamic"),
                "checked": node["checked"],
                "_nodeids": nodeids,
            }

        sort_nodes(root["folders"])
        sort_nodes(root["files"])
        children = [build_folder(item) for item in root["folders"]]
        children.extend(build_file(item) for item in root["files"])
        if not children:
            return []
        root_nodeids = [nodeid for child in children for nodeid in child.get("_nodeids", [])]
        selected_count = sum(nodeid in selected for nodeid in root_nodeids)
        root_selection_state = _selection_state(selected_count, len(root_nodeids))
        result = {
                "title": "tests",
                "title_source": "dynamic",
                "_key": "root:tests",
                "rowType": "root",
                "iconSource": "Folder",
                "expanded": True,
                "children": children,
                "selectionState": root_selection_state,
                "checked": root_selection_state == "checked",
                "selectableCount": len(root_nodeids),
                "selectedCount": selected_count,
                "_nodeids": root_nodeids,
            }
        def remove_internal_nodeids(node: dict[str, Any]) -> None:
            node.pop("_nodeids", None)
            for child in node.get("children", []):
                remove_internal_nodeids(child)
        remove_internal_nodeids(result)
        return [result]

    @Slot(result="QVariantList")
    def globalParamRows(self):
        return list(self._schema_to_jsonable(self._registry.global_context).get("fields", []))

    @Slot()
    def refreshGlobalSchema(self) -> None:
        self._schedule_adb_refresh("user_refresh")

    @Slot(str, str)
    def setEnvEquipmentType(self, kind: str, device_type: str) -> None:
        self._bind_params_state()
        normalized_kind = str(kind or "").strip().lower()
        normalized_type = str(device_type or "").strip()
        if not normalized_kind or not normalized_type:
            return
        equipment = self._equipment_config()
        next_config = self._env_default_config(normalized_kind, normalized_type)
        if equipment.get(normalized_kind) == next_config:
            return
        equipment[normalized_kind] = next_config
        smarttest_context().params.set_equipment_config(equipment)
        self._save_and_emit()
        self._schedule_context_refresh(f"env_type_changed:{normalized_kind}")

    @Slot(str, str, result="QVariant")
    def envEquipmentValue(self, kind: str, key: str) -> Any:
        normalized_kind = str(kind or "").strip().lower()
        normalized_key = str(key or "").strip()
        if not normalized_kind or not normalized_key:
            return ""
        current = self._equipment_config().get(normalized_kind, {})
        if not isinstance(current, dict):
            return ""
        if normalized_key in current:
            return current.get(normalized_key)
        default_config = self._env_default_config(normalized_kind, str(current.get("type") or ""))
        return default_config.get(normalized_key, "")

    @Slot(str, str, "QVariant")
    def setEnvEquipmentValue(self, kind: str, key: str, value: Any) -> None:
        self._bind_params_state()
        normalized_kind = str(kind or "").strip().lower()
        normalized_key = str(key or "").strip()
        if not normalized_kind or not normalized_key:
            return
        normalized_value = _normalize_env_equipment_value(normalized_key, value)
        equipment = self._equipment_config()
        current = equipment.get(normalized_kind, {})
        config = dict(current) if isinstance(current, dict) else {}
        if "type" not in config:
            config = self._env_default_config(normalized_kind)
        if config.get(normalized_key) == normalized_value:
            self._trace("env_value_unchanged", kind=normalized_kind, key=normalized_key)
            return
        config[normalized_key] = normalized_value
        equipment[normalized_kind] = config
        smarttest_context().params.set_equipment_config(equipment)
        self._trace(
            "env_value_changed",
            kind=normalized_kind,
            key=normalized_key,
            value=_value_size(normalized_value),
        )
        self._save_and_emit()

    @Slot(str)
    def addEnvRelayTerminal(self, kind: str) -> None:
        normalized_kind = str(kind or "").strip().lower()
        if not normalized_kind:
            return
        rows = self._env_relay_terminals(normalized_kind)
        rows.append({"terminal": len(rows) + 1, "mode": "NO", "press_seconds": 1})
        self._trace("env_terminal_add", kind=normalized_kind, rows=len(rows))
        self._set_env_relay_terminals(normalized_kind, rows)

    @Slot(str, int)
    def removeEnvRelayTerminal(self, kind: str, index: int) -> None:
        normalized_kind = str(kind or "").strip().lower()
        if not normalized_kind:
            return
        rows = self._env_relay_terminals(normalized_kind)
        if len(rows) <= 1 or index < 0 or index >= len(rows):
            self._trace("env_terminal_remove_skip", kind=normalized_kind, index=index, rows=len(rows))
            return
        rows.pop(index)
        self._trace("env_terminal_remove", kind=normalized_kind, index=index, rows=len(rows))
        self._set_env_relay_terminals(normalized_kind, rows)

    @Slot(str, int, str, "QVariant")
    def setEnvRelayTerminalValue(self, kind: str, index: int, key: str, value: Any) -> None:
        normalized_kind = str(kind or "").strip().lower()
        normalized_key = str(key or "").strip()
        if not normalized_kind or not normalized_key:
            return
        rows = self._env_relay_terminals(normalized_kind)
        if index < 0 or index >= len(rows):
            self._trace("env_terminal_update_skip", kind=normalized_kind, index=index, key=normalized_key)
            return
        if normalized_key not in {"terminal", "mode", "press_seconds"}:
            self._trace("env_terminal_update_skip", kind=normalized_kind, index=index, key=normalized_key)
            return
        rows[index][normalized_key] = value
        rows = _normalize_env_equipment_value("terminals", rows)
        self._trace("env_terminal_update", kind=normalized_kind, index=index, key=normalized_key, rows=len(rows))
        self._set_env_relay_terminals(normalized_kind, rows)

    def _env_relay_terminals(self, kind: str) -> list[dict[str, Any]]:
        config = self._equipment_config().get(kind, {})
        if not isinstance(config, dict):
            config = self._env_default_config(kind, "usb_relay")
        return _normalize_env_equipment_value("terminals", config.get("terminals", []))

    def _set_env_relay_terminals(self, kind: str, rows: list[dict[str, Any]]) -> None:
        self._bind_params_state()
        normalized_rows = _normalize_env_equipment_value("terminals", rows)
        equipment = self._equipment_config()
        current = equipment.get(kind, {})
        config = dict(current) if isinstance(current, dict) else self._env_default_config(kind, "usb_relay")
        if "type" not in config:
            config = self._env_default_config(kind, "usb_relay")
        if config.get("terminals") == normalized_rows:
            self._trace("env_terminal_unchanged", kind=kind, rows=len(normalized_rows))
            return
        config["terminals"] = normalized_rows
        equipment[kind] = config
        smarttest_context().params.set_equipment_config(equipment)
        self._save_and_emit()

    @Slot(str, bool)
    def setCaseSelected(self, nodeid: str, selected: bool) -> None:
        if not self._set_case_selected(nodeid, selected):
            return
        self._save_and_emit()
        if selected:
            self._schedule_context_refresh("case_selected")

    @Slot(str, bool)
    def setFileSelected(self, file_path: str, selected: bool) -> None:
        cases = self._cases_for_file(file_path)
        if not cases:
            return
        changed = False
        normalized = (file_path or "").strip()
        if selected and normalized and normalized not in self._state.selected_files:
            self._state.selected_files.append(normalized)
            changed = True
        if not selected and normalized:
            next_files = [path for path in self._state.selected_files if path != normalized]
            if next_files != self._state.selected_files:
                self._state.selected_files = next_files
                changed = True
        for case in cases:
            changed = self._set_case_selected(str(case.get("nodeid", "")), selected) or changed
        if not changed:
            return
        self._sync_selected_file_order()
        self._reorder_selected_cases_by_file_order()
        self._save_and_emit()
        if selected:
            self._schedule_context_refresh("file_selected")

    def _tree_nodeids(self, node_key: str, filter_text: str) -> list[str]:
        key = str(node_key or "").strip()
        normalized_filter = str(filter_text or "").strip().lower()
        result: list[str] = []
        for case in self._cases:
            nodeid = str(case.get("nodeid", ""))
            file_path = str(case.get("file", ""))
            short_file = self._trimmed_case_path(file_path)
            matches = (not normalized_filter or normalized_filter in file_path.lower()
                       or normalized_filter in short_file.lower()
                       or normalized_filter in str(case.get("name", "")).lower())
            if not matches:
                continue
            if key == "root:tests":
                belongs = True
            elif key.startswith("folder:"):
                prefix = key[len("folder:"):].rstrip("/") + "/"
                belongs = short_file.startswith(prefix)
            elif key.startswith("file:"):
                belongs = short_file == key[len("file:"):]
            else:
                belongs = False
            if belongs and nodeid and nodeid not in result:
                result.append(nodeid)
        return result

    @Slot(str, bool, str)
    def setTreeNodeSelected(self, node_key: str, selected: bool, filter_text: str) -> None:
        nodeids = self._tree_nodeids(node_key, filter_text)
        changed = False
        for nodeid in nodeids:
            changed = self._set_case_selected(nodeid, selected) or changed
        if not changed:
            return
        self._sync_selected_file_order()
        self._reorder_selected_cases_by_file_order()
        self._save_and_emit()
        self._schedule_context_refresh("tree_selection")

    @Slot()
    def clearSelectedCases(self) -> None:
        if not self._state.selected and not self._state.selected_files:
            return
        self._state.selected = []
        self._state.selected_files = []
        self._save_and_emit()
        self._schedule_context_refresh("selection_cleared")

    @Slot(str, result=bool)
    def isCaseSelected(self, nodeid: str) -> bool:
        return any(c.nodeid == nodeid for c in self._state.selected)

    @Slot(int, int)
    def moveSelected(self, from_index: int, to_index: int) -> None:
        self.moveSelectedFile(from_index, to_index)

    @Slot(int, int)
    def moveSelectedFile(self, from_index: int, to_index: int) -> None:
        if from_index == to_index:
            return
        if from_index < 0 or to_index < 0:
            return
        if from_index >= len(self._state.selected_files) or to_index >= len(self._state.selected_files):
            return
        item = self._state.selected_files.pop(from_index)
        self._state.selected_files.insert(to_index, item)
        self._reorder_selected_cases_by_file_order()
        self._save_and_emit()

    @Slot(str, str, "QVariant")
    def setGlobalValue(self, key: str, value: Any) -> None:
        if not key:
            return
        self._bind_params_state()
        if not smarttest_context().params.set_global_value(key, value):
            return
        if key == "dut":
            self._set_selected_dut_serials([str(value or "").strip()] if str(value or "").strip() else [])
            self._schedule_context_refresh("dut_changed")
        self._save_and_emit()

    @Slot(str, bool)
    def setDutSelected(self, serial: str, selected: bool) -> None:
        normalized = str(serial or "").strip()
        if not normalized:
            return
        values = self._selected_dut_serials()
        if selected and normalized not in values:
            values.append(normalized)
        if not selected:
            values = [item for item in values if item != normalized]
        if not self._set_selected_dut_serials(values):
            return
        self._schedule_context_refresh("dut_changed")
        self._save_and_emit()

    @Slot(result="QVariantList")
    def selectedDuts(self):
        return self._selected_dut_serials()

    @Slot(result="QVariantList")
    def dutDynamicParamRows(self):
        fields = self._selected_dut_dynamic_fields()
        if not fields:
            return []
        nodeid = self._selected_nodeids()[0] if self._selected_nodeids() else ""
        rows: list[dict[str, Any]] = []
        for dut in self._selected_dut_serials():
            rows.append(
                {
                    "dut_serial": dut,
                    "fields": [
                        self._field_to_jsonable(
                            field,
                            nodeid=nodeid,
                            value=smarttest_context().params.dut_dynamic_value(dut, field.key, field.default),
                            has_value=True,
                            readonly=False,
                            dut_serial=dut,
                            loading_owner="*",
                        )
                        for field in fields
                    ],
                }
            )
        return rows

    @Slot(str, str, "QVariant")
    def setDutDynamicParamValue(self, dut: str, key: str, value: Any) -> None:
        self._trace("dut_dynamic_set_value_request", dut=str(dut or "").strip() or "<none>", field=str(key or "").strip(), value=_value_size(value))
        if not self._set_dut_dynamic_param_value(dut=dut, key=key, value=value):
            return
        self._save_and_emit()
        self._refresh_options_affected_by_param_change(nodeid=self._selected_nodeids()[0] if self._selected_nodeids() else "", key=key)

    @Slot(str, str, "QVariant")
    def saveDutDynamicParamValue(self, dut: str, key: str, value: Any) -> None:
        self._trace("dut_dynamic_save_value_request", dut=str(dut or "").strip() or "<none>", field=str(key or "").strip(), value=_value_size(value))
        if not self._set_dut_dynamic_param_value(dut=dut, key=key, value=value):
            return
        save_state(self._state_path, self._state)

    @Slot(str, str, str, bool)
    def setDutDynamicParamListItemSelected(self, dut: str, key: str, value: str, selected: bool) -> None:
        option = str(value or "").strip()
        if not option:
            self._trace("dut_dynamic_list_toggle_skip_empty", dut=str(dut or "").strip() or "<none>", field=str(key or "").strip())
            return
        self._trace(
            "dut_dynamic_list_toggle_request",
            dut=str(dut or "").strip() or "<none>",
            field=str(key or "").strip(),
            option=option,
            selected=selected,
        )
        current = normalize_option_values(smarttest_context().params.dut_dynamic_value(dut, key, []))
        if selected and option not in current:
            current.append(option)
        if not selected:
            current = [item for item in current if item != option]
        if not self._set_dut_dynamic_param_value(dut=dut, key=key, value=current):
            return
        self._save_and_emit()

    @Slot(str, "QVariant")
    def saveGlobalValue(self, key: str, value: Any) -> None:
        if not key:
            return
        self._bind_params_state()
        if not smarttest_context().params.set_global_value(key, value):
            return
        save_state(self._state_path, self._state)

    @Slot(str, str, "QVariant")
    def setCaseParamValue(self, nodeid: str, key: str, value: Any) -> None:
        if not self._set_case_param_value(nodeid=nodeid, key=key, value=value):
            return
        self._save_and_emit()
        self._refresh_options_affected_by_param_change(nodeid=nodeid, key=key)

    @Slot(str, str, "QVariant")
    def saveCaseParamValue(self, nodeid: str, key: str, value: Any) -> None:
        if not self._set_case_param_value(nodeid=nodeid, key=key, value=value):
            return
        save_state(self._state_path, self._state)

    @Slot(str, str, str, bool)
    def setCaseParamListItemSelected(self, nodeid: str, key: str, value: str, selected: bool) -> None:
        option = str(value or "").strip()
        if not option:
            return
        current = normalize_option_values(self._resolve_case_value(nodeid=nodeid, key=key))
        if selected and option not in current:
            current.append(option)
        if not selected:
            current = [item for item in current if item != option]
        if not self._set_case_param_value(nodeid=nodeid, key=key, value=current):
            return
        self._save_and_emit()

    @Slot()
    def reloadState(self) -> None:
        self._state = load_state(self._state_path)
        smarttest_context().params.bind_ui_state(self._state)
        changed = self._ensure_state_defaults()
        changed = self._sync_selected_file_order() or changed
        changed = self._reorder_selected_cases_by_file_order() or changed
        if changed:
            save_state(self._state_path, self._state)
        self.stateChanged.emit()

    def _save_and_emit(self) -> None:
        save_state(self._state_path, self._state)
        self.stateChanged.emit()


def _trace_timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _selection_state(selected_count: int, selectable_count: int) -> str:
    if selectable_count <= 0 or selected_count <= 0:
        return "unchecked"
    if selected_count >= selectable_count:
        return "checked"
    return "partial"


def _storage_playback_path(value: str) -> str:
    path = str(value or "").strip()
    if path.startswith("/mnt/media_rw/"):
        return "/storage/" + path[len("/mnt/media_rw/") :]
    return path


def _storage_playback_options(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        path = _storage_playback_path(value)
        if path and path not in normalized:
            normalized.append(path)
    return normalized


def _dynamic_value_for_field(field: ParamField, options: list[str]) -> Any:
    field_type = field.type.value if hasattr(field.type, "value") else field.type
    if field_type == ParamValueType.MULTI_ENUM.value:
        return list(options)
    if not options:
        return field.default
    return options[0]


def _normalize_env_equipment_value(key: str, value: Any) -> Any:
    if str(key or "").strip() != "terminals":
        return value
    try:
        raw_rows = list(value)
    except TypeError:
        raw_rows = []
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        try:
            terminal = max(1, int(str(item.get("terminal", 1)).strip()))
        except (TypeError, ValueError):
            terminal = 1
        try:
            press_seconds: int | float = float(str(item.get("press_seconds", 1)).strip())
        except (TypeError, ValueError):
            press_seconds = 1
        if isinstance(press_seconds, float) and press_seconds.is_integer():
            press_seconds = int(press_seconds)
        mode = str(item.get("mode", "NO") or "NO").strip().upper()
        rows.append(
            {
                "terminal": terminal,
                "mode": mode if mode in {"NO", "NC"} else "NO",
                "press_seconds": press_seconds,
            }
        )
    return rows or [{"terminal": 1, "mode": "NO", "press_seconds": 1}]


def _value_size(value: Any) -> int:
    if isinstance(value, (list, tuple, set)):
        return len(value)
    if value in (None, ""):
        return 0
    return 1
