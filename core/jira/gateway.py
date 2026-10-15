from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from threading import local
from time import monotonic, sleep
from typing import Any, Callable

try:
    from atlassian import Jira
except ImportError:  # pragma: no cover - dependency error is normalized at construction
    Jira = None

from core.jira.attachments import (
    AttachmentCancellation,
    AttachmentUploadCancelled,
    CreateIssueAttachment,
    JiraAttachmentMetadata,
)
from core.jira.commands import CreateIssueCommand, UpdateIssueCommand
from core.logging import smart_log
from core.product_lines import PRODUCT_LINES


_DEFAULT_USER_SEARCH_PROJECT = PRODUCT_LINES[1].jira_project_keys[0]


@dataclass(frozen=True)
class JiraGatewayConfig:
    base_url: str
    page_size: int = 100


class JiraGatewayError(RuntimeError):
    def __init__(self, code: str, *, messages: list[str] | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.messages = tuple(messages or ())


class JiraGateway:
    FULL_SEARCH_PAGE_SIZE = 1000
    FULL_SEARCH_WORKERS = 4
    _RETRY_DELAYS = (0.25, 0.5, 1.0)
    CORE_FIELDS = (
        "summary",
        "project",
        "status",
        "issuetype",
        "priority",
        "assignee",
        "reporter",
        "creator",
        "components",
        "resolution",
        "created",
        "updated",
        "labels",
    )
    RELEASE_FIELD_NAMES = (
        "Project ID", "Software Release", "Severity", "Compare Status", "QA Assignee", "Manager",
    )

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        api: Any = None,
        api_factory: Callable[[], Any] | None = None,
        page_size: int = 100,
    ) -> None:
        clean_url = str(base_url or "").rstrip("/")
        if not clean_url:
            raise JiraGatewayError("jira_base_url_required")
        self.config = JiraGatewayConfig(clean_url, page_size)
        self._client_state = local()
        self._full_search_executor = ThreadPoolExecutor(
            max_workers=self.FULL_SEARCH_WORKERS,
            thread_name_prefix="jira-full-search",
        )
        if api is not None:
            self._api = api
            self._api_factory = api_factory or (lambda: api)
            return
        if Jira is None:
            raise JiraGatewayError("jira_dependency_unavailable")
        try:
            self._api = Jira(url=clean_url, username=username, password=password)
            self._api_factory = lambda: Jira(url=clean_url, username=username, password=password)
        except Exception as exc:
            raise JiraGatewayError("jira_initialization_failed") from exc

    def search_issues(self, query: str, page: int = 0) -> dict[str, Any]:
        start = int(page) * self.config.page_size
        return self.search_payload(query, start_at=start, max_results=self.config.page_size, fields=list(self.CORE_FIELDS))

    def search_release_issues(self, query: str, page: int = 0) -> dict[str, Any]:
        metadata = self.release_field_metadata()
        requested = [
            *self.CORE_FIELDS, "fixVersions", "resolutiondate",
            *(key for key, name in metadata.items() if name in self.RELEASE_FIELD_NAMES),
        ]
        start = int(page) * self.config.page_size
        payload = self.search_payload(
            query, start_at=start, max_results=self.config.page_size, fields=requested,
        )
        return {**payload, "fieldMetadata": metadata}

    def release_field_metadata(self) -> dict[str, str]:
        try:
            getter = getattr(self._api, "get_all_fields", None)
            payload = getter() if getter is not None else self._api.get("rest/api/2/field")
        except Exception as exc:
            raise JiraGatewayError("jira_field_metadata_failed") from exc
        wanted = set(self.RELEASE_FIELD_NAMES)
        return {
            str(item.get("id") or ""): str(item.get("name") or "")
            for item in payload or ()
            if isinstance(item, dict) and str(item.get("name") or "") in wanted and item.get("id")
        }

    def search_payload(
        self,
        query: str,
        *,
        start_at: int = 0,
        max_results: int | None = None,
        fields: list[str] | None = None,
        expand: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            payload = self._api.jql(
                query,
                fields=fields or list(self.CORE_FIELDS),
                start=start_at,
                limit=max_results or self.config.page_size,
                expand=",".join(expand) if expand else None,
                validate_query="strict",
            )
        except Exception as exc:
            raise JiraGatewayError("jira_search_failed") from exc
        return payload if isinstance(payload, dict) else {}

    def search_all_payloads(
        self,
        query: str,
        *,
        fields: list[str] | None = None,
        expand: list[str] | None = None,
        progress: Callable[[int, int], None] | None = None,
        page_size: int | None = None,
        workers: int | None = None,
    ) -> list[dict[str, Any]]:
        requested_fields = fields or list(self.CORE_FIELDS)
        requested_page_size = self.FULL_SEARCH_PAGE_SIZE if page_size is None else page_size
        if workers is not None:
            with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="jira-activity-search") as executor:
                return self._collect_search_pages(query, requested_fields, expand, progress, requested_page_size, executor)
        return self._collect_search_pages(query, requested_fields, expand, progress, requested_page_size, self._full_search_executor)

    def _collect_search_pages(self, query, requested_fields, expand, progress, requested_page_size, executor):
        pages = {0: executor.submit(
            self._search_full_page, query, 0, requested_fields, expand, requested_page_size,
        ).result()}
        observed_total = int(pages[0].get("total") or 0)
        completed_count = len(pages[0].get("issues") or ())
        if progress:
            progress(completed_count, observed_total)
        scheduled = {0}
        pending = {}

        def schedule(total: int) -> None:
            for start in range(requested_page_size, total, requested_page_size):
                if start not in scheduled:
                    scheduled.add(start)
                    future = executor.submit(
                        self._search_full_page, query, start, requested_fields, expand, requested_page_size,
                    )
                    pending[future] = start

        try:
            schedule(observed_total)
            while pending:
                completed, _ = wait(tuple(pending), return_when=FIRST_COMPLETED)
                for future in completed:
                    start = pending.pop(future)
                    page = future.result()
                    pages[start] = page
                    completed_count += len(page.get("issues") or ())
                    observed_total = max(observed_total, int(page.get("total") or 0))
                    if progress:
                        progress(completed_count, observed_total)
                schedule(observed_total)
        finally:
            for future in pending:
                future.cancel()

        rows: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for start in sorted(pages):
            for issue in pages[start].get("issues") or ():
                if not isinstance(issue, dict):
                    continue
                key = str(issue.get("key") or "")
                if key and key in seen_keys:
                    continue
                if key:
                    seen_keys.add(key)
                rows.append(issue)
        if progress:
            progress(len(rows), observed_total)
        return rows

    def search_activity_payloads(self, query, *, progress=None):
        rows = self.search_all_payloads(query, fields=["comment", "created"], expand=["changelog"], page_size=1000, workers=12, progress=progress)
        return self._complete_activity_payloads(rows, progress=progress)

    def search_metric_page(self, query, start, metric):
        fields = ["comment", "created"] if metric == "comment" else ["created"]
        page = self._search_full_page(query, start, fields, ["changelog"] if metric == "verify" else None, 100)
        self._complete_activity_payloads(page.get("issues") or [], comment=metric == "comment", verify=metric == "verify")
        return page

    def _complete_activity_payloads(self, rows, *, progress=None, comment=True, verify=True):
        for row in rows:
            if verify:
                history = row.get("changelog")
                if not isinstance(history, dict):
                    raise JiraGatewayError("jira_changelog_missing")
                if history.get("total") != len(history.get("histories") or []) or history.get("startAt", 0) != 0:
                    if progress:
                        progress(len(rows), len(rows))
                    try:
                        payload = self._api.get_issue(row["key"], fields=["created"], expand="changelog")
                    except Exception as exc:
                        raise JiraGatewayError("jira_changelog_failed") from exc
                    history = payload.get("changelog") or {}
                    if history.get("total") != len(history.get("histories") or []) or history.get("startAt", 0) != 0:
                        raise JiraGatewayError("jira_changelog_incomplete")
                    row["changelog"] = history
            if not comment:
                continue
            fields = row.get("fields") or {}
            embedded = fields.get("comment") or {}
            if int(embedded.get("total", len(embedded.get("comments") or []))) <= len(embedded.get("comments") or []):
                continue
            comments = []
            try:
                while True:
                    if progress:
                        progress(len(rows), len(rows))
                    payload = self._api.get(f"rest/api/2/issue/{row['key']}/comment",
                                            params={"startAt": len(comments), "maxResults": 1000})
                    items = payload.get("comments") or []
                    comments.extend(items)
                    if len(comments) >= int(payload.get("total", len(comments))):
                        break
                    if not items:
                        raise JiraGatewayError("jira_comments_incomplete")
                fields["comment"] = {**embedded, "comments": comments, "total": len(comments)}
            except JiraGatewayError:
                raise
            except Exception as exc:
                raise JiraGatewayError("jira_comments_failed") from exc
        return rows

    def _search_full_page(
        self,
        query: str,
        start: int,
        fields: list[str],
        expand: list[str] | None,
        page_size: int,
    ) -> dict[str, Any]:
        api = self._thread_api()
        started = monotonic()
        for attempt in range(len(self._RETRY_DELAYS) + 1):
            attempt_started = monotonic()
            smart_log("Jira gateway page request", domain="jira", source="jira_gateway",
                emit_runtime_event=False, extra={"operation": "search_page", "outcome": "started",
                "start_at": start, "page_size": page_size, "attempt": attempt + 1,
                "field_count": len(fields), "expand_changelog": "changelog" in (expand or [])})
            try:
                payload = api.jql(
                    query,
                    fields=fields,
                    start=start,
                    limit=page_size,
                    expand=",".join(expand) if expand else None,
                    validate_query="strict",
                )
                smart_log("Jira gateway page request", domain="jira", source="jira_gateway",
                    emit_runtime_event=False, extra={"operation": "search_page", "outcome": "completed",
                    "start_at": start, "page_size": page_size, "attempt": attempt + 1,
                    "duration_ms": round((monotonic() - attempt_started) * 1000, 3),
                    "processed": len(payload.get("issues") or []) if isinstance(payload, dict) else 0})
                return payload if isinstance(payload, dict) else {}
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status != 502 or attempt >= len(self._RETRY_DELAYS):
                    smart_log("Jira gateway search failed", domain="jira", source="jira_gateway", level="ERROR",
                        emit_runtime_event=False, extra={"operation": "search_page", "http_status": status,
                        "cause_type": type(exc).__name__, "start_at": start, "page_size": page_size,
                        "attempt": attempt + 1, "duration_ms": round((monotonic() - started) * 1000, 3),
                        "outcome": "failed"})
                    raise JiraGatewayError("jira_search_failed") from exc
                smart_log("Jira gateway page retry", domain="jira", source="jira_gateway", level="WARNING",
                    emit_runtime_event=False, extra={"operation": "search_page", "outcome": "retry",
                    "start_at": start, "page_size": page_size, "attempt": attempt + 1,
                    "http_status": status, "cause_type": type(exc).__name__,
                    "duration_ms": round((monotonic() - attempt_started) * 1000, 3)})
                sleep(self._RETRY_DELAYS[attempt])
        raise JiraGatewayError("jira_search_failed")  # pragma: no cover

    def _thread_api(self) -> Any:
        if not hasattr(self._client_state, "api"):
            try:
                self._client_state.api = self._api_factory()
            except Exception as exc:
                raise JiraGatewayError("jira_initialization_failed") from exc
        return self._client_state.api

    def get_issue(self, issue_key: str) -> dict[str, Any]:
        started = monotonic()
        try:
            payload = self._api.get_issue(issue_key, fields=list(self.CORE_FIELDS), expand=None)
        except Exception as exc:
            response = getattr(exc, "response", None)
            smart_log(
                "Jira gateway issue request", domain="jira",
                source="jira_gateway", level="ERROR", emit_runtime_event=False,
                extra={
                    "operation": "get_issue", "issue_key": issue_key,
                    "outcome": "failed",
                    "duration_ms": round((monotonic() - started) * 1000, 3),
                    "http_status": getattr(response, "status_code", None),
                    "cause_type": type(exc).__name__,
                },
            )
            raise JiraGatewayError("jira_issue_get_failed") from exc
        smart_log(
            "Jira gateway issue request", domain="jira",
            source="jira_gateway", emit_runtime_event=False,
            extra={
                "operation": "get_issue", "issue_key": issue_key,
                "outcome": "success",
                "duration_ms": round((monotonic() - started) * 1000, 3),
            },
        )
        return payload if isinstance(payload, dict) else {}

    def load_issue_sections(self, issue_key: str, sections: tuple[str, ...]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if "comments" in sections:
            try:
                comments = self._api.issue_get_comments(issue_key) or {}
            except Exception as exc:
                raise JiraGatewayError("jira_comments_failed") from exc
            result["comments"] = comments.get("comments", comments if isinstance(comments, list) else [])
        field_map = {
            "description": "description",
            "attachments": "attachment",
            "links": "issuelinks",
        }
        requested_fields = [field_map[name] for name in sections if name in field_map]
        if "custom_fields" in sections:
            requested_fields.append("*all")
        if requested_fields:
            started = monotonic()
            try:
                payload = self._api.get_issue(issue_key, fields=requested_fields, expand='names' if 'custom_fields' in sections else None) or {}
            except Exception as exc:
                response = getattr(exc, "response", None)
                smart_log(
                    "Jira gateway issue request", domain="jira",
                    source="jira_gateway", level="ERROR", emit_runtime_event=False,
                    extra={
                        "operation": "load_issue_sections", "issue_key": issue_key,
                        "sections": list(sections), "outcome": "failed",
                        "duration_ms": round((monotonic() - started) * 1000, 3),
                        "http_status": getattr(response, "status_code", None),
                        "cause_type": type(exc).__name__,
                    },
                )
                raise JiraGatewayError("jira_details_failed") from exc
            smart_log(
                "Jira gateway issue request", domain="jira",
                source="jira_gateway", emit_runtime_event=False,
                extra={
                    "operation": "load_issue_sections", "issue_key": issue_key,
                    "sections": list(sections), "outcome": "success",
                    "duration_ms": round((monotonic() - started) * 1000, 3),
                },
            )
            fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
            for section, jira_field in field_map.items():
                if section in sections:
                    result[section] = fields.get(jira_field)
            if "custom_fields" in sections:
                result["custom_fields"] = {key: value for key, value in fields.items() if str(key).startswith("customfield_")}
                for field_id, name in (payload.get('names') or {}).items():
                    if name in ('Channel of Reporter', 'Test Case ID') and field_id in fields:
                        result['custom_fields'][name] = fields[field_id]
        return result

    def create_issue(self, command: CreateIssueCommand) -> dict[str, Any]:
        fields = self.command_fields(command)
        try:
            payload = self._api.create_issue(fields)
        except Exception as exc:
            raise JiraGatewayError("jira_create_failed") from exc
        return payload if isinstance(payload, dict) else {}

    def update_issue(self, command: UpdateIssueCommand) -> dict[str, Any]:
        try:
            self._api.issue_update(command.issue_key, command.fields)
            payload = self._api.get_issue(command.issue_key, fields=list(self.CORE_FIELDS), expand=None)
        except Exception as exc:
            raise JiraGatewayError("jira_update_failed") from exc
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def command_fields(command: CreateIssueCommand) -> dict[str, Any]:
        fields: dict[str, Any] = {
            "project": {"key": command.project_key},
            "issuetype": {"name": command.issue_type},
            "summary": command.summary,
            "description": _description(command),
            "labels": _labels(command),
        }
        if command.priority:
            fields["priority"] = _field_value(command.priority, command.field_controls.get("priority", ""), default_kind="name")
        if command.assignee:
            fields["assignee"] = {"name": command.assignee}
        if command.components:
            control = command.field_controls.get("components", "")
            fields["components"] = _field_value(list(command.components), control) if control else [{"name": item} for item in command.components if item]
        fields.update({key: _field_value(value, command.field_controls.get(key, "")) for key, value in command.extra_fields.items() if value not in (None, "", [], {})})
        return fields

    def find_issue_for_source(self, command: CreateIssueCommand) -> dict[str, Any] | None:
        if not command.source_system or not command.source_id:
            return None
        jql = f'project = "{_jql(command.project_key)}" AND labels = "source_{_safe(command.source_system)}" AND description ~ "{_jql(command.source_id)}"'
        return self._first(jql)

    def find_issue_for_external_url(self, project_key: str, external_url: str) -> dict[str, Any] | None:
        clean_url = str(external_url or "").strip()
        if not project_key or not clean_url:
            return None
        for jql in (
            f'project = "{_jql(project_key)}" AND "Attachment links" = "{_jql(clean_url)}"',
            f'project = "{_jql(project_key)}" AND text ~ "{_jql(clean_url)}"',
        ):
            try:
                found = self._first(jql)
            except JiraGatewayError:
                continue
            if found:
                return found
        return None

    def _first(self, jql: str) -> dict[str, Any] | None:
        payload = self.search_payload(jql, max_results=1, fields=["summary"])
        issues = payload.get("issues") or ()
        return issues[0] if issues else None

    def fetch_filter(self, filter_id: str) -> dict[str, Any]:
        try:
            payload = self._api.get_filter(filter_id) or {}
        except Exception as exc:
            raise JiraGatewayError("jira_filter_failed") from exc
        return payload if isinstance(payload, dict) else {}

    def fetch_query_fields(self) -> dict[str, Any]:
        try:
            payload = self._api.get("rest/api/2/jql/autocompletedata") or {}
        except Exception as exc:
            raise JiraGatewayError("jira_field_metadata_failed") from exc
        return payload if isinstance(payload, dict) else {}

    def fetch_query_suggestions(self, field_name: str, query: str = "") -> dict[str, Any]:
        try:
            payload = self._api.get(
                "rest/api/2/jql/autocompletedata/suggestions",
                params={"fieldName": str(field_name), "fieldValue": str(query)},
            ) or {}
        except Exception as exc:
            raise JiraGatewayError("jira_field_suggestions_failed") from exc
        return payload if isinstance(payload, dict) else {}

    def fetch_saved_filters(self) -> list[dict[str, Any]]:
        """Return the filters Jira exposes through its read-only favourite endpoint."""
        try:
            payload = self._api.get("rest/api/2/filter/favourite") or []
        except Exception as exc:
            raise JiraGatewayError("jira_filter_list_failed") from exc
        return [item for item in payload if isinstance(item, dict)]

    def validate_jql(self, jql: str) -> dict[str, Any]:
        try:
            self._api.jql(
                str(jql), fields=[], start=0, limit=0, expand=None,
                validate_query="strict",
            )
        except Exception as exc:
            response = getattr(exc, "response", None)
            if getattr(response, "status_code", None) == 400:
                try:
                    payload = response.json() or {}
                except Exception:
                    payload = {}
                messages = [str(item) for item in payload.get("errorMessages") or () if str(item)]
                messages.extend(str(item) for item in (payload.get("errors") or {}).values() if str(item))
                return {"valid": False, "errors": messages or ["Invalid JQL"]}
            raise JiraGatewayError("jira_jql_validation_failed") from exc
        return {"valid": True, "errors": []}

    def fetch_create_metadata(self, project_key: str, issue_type: str) -> dict[str, Any]:
        try:
            payload = self._api.issue_createmeta(project_key, expand="projects.issuetypes.fields") or {}
        except Exception as exc:
            raise JiraGatewayError("jira_create_metadata_failed") from exc
        return payload if isinstance(payload, dict) else {}

    def search_users(
        self,
        query: str,
        *,
        project_key: str = _DEFAULT_USER_SEARCH_PROJECT,
    ) -> list[dict[str, Any]]:
        try:
            payload = self._api.get_all_assignable_users_for_project(project_key, start=0, limit=1000) or []
        except Exception as exc:
            raise JiraGatewayError("jira_user_search_failed") from exc
        needle = str(query or "").strip().casefold()
        return [
            {**_public_user(item), "active": item.get("active") is not False}
            for item in payload
            if isinstance(item, dict)
            and (
                not needle
                or needle in str(item.get("name") or "").casefold()
                or needle in str(item.get("displayName") or "").casefold()
            )
        ]

    def user_groups(self, username: str) -> tuple[str, ...]:
        try:
            payload = self._api.get("rest/api/2/user", params={
                "username": str(username), "expand": "groups",
            }) or {}
        except Exception as exc:
            raise JiraGatewayError("jira_user_groups_failed") from exc
        groups = payload.get("groups") if isinstance(payload, dict) else None
        items = groups.get("items") if isinstance(groups, dict) else None
        if not isinstance(items, list):
            raise JiraGatewayError("jira_user_groups_failed")
        return tuple(sorted({
            str(item.get("name") or "").strip()
            for item in (items or ()) if isinstance(item, dict) and str(item.get("name") or "").strip()
        }, key=str.casefold))

    def current_user(self) -> dict[str, str]:
        try:
            payload = self._api.get("rest/api/2/myself") or {}
        except Exception as exc:
            raise JiraGatewayError("jira_current_user_failed") from exc
        return _public_user(payload)

    def attachment_metadata(self) -> JiraAttachmentMetadata:
        try:
            payload = self._api.get_attachment_meta() or {}
        except Exception as exc:
            raise JiraGatewayError("jira_attachment_metadata_failed") from exc
        limit = payload.get("uploadLimit")
        return JiraAttachmentMetadata(True, payload.get("enabled") if isinstance(payload.get("enabled"), bool) else None, int(limit) if isinstance(limit, int) and not isinstance(limit, bool) else None)

    def list_attachments(self, issue_key: str) -> list[dict[str, Any]]:
        try:
            payload = self._api.get_issue(issue_key, fields=["attachment"], expand=None) or {}
        except Exception as exc:
            raise JiraGatewayError("jira_attachments_list_failed") from exc
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        return [item for item in fields.get("attachment") or () if isinstance(item, dict)]

    def upload_attachment(
        self,
        issue_key: str,
        attachment: CreateIssueAttachment,
        *,
        cancellation: AttachmentCancellation | None = None,
    ) -> dict[str, Any]:
        try:
            with attachment.path.open("rb") as source:
                payload = self._api.add_attachment_object(
                    issue_key,
                    _AttachmentStream(source, attachment.upload_filename, cancellation),
                )
        except Exception as exc:
            if isinstance(exc, AttachmentUploadCancelled):
                raise
            raise JiraGatewayError("jira_attachment_upload_failed") from exc
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            return payload[0]
        return payload if isinstance(payload, dict) else {}


def _field_value(value: Any, control: str, *, default_kind: str = "") -> Any:
    if control in {"text", "multiline"}:
        return value
    if control == "single":
        return {"id": str(value)}
    if control == "multi":
        return [{"id": str(item)} for item in (value if isinstance(value, (list, tuple)) else [value]) if item]
    if control == "cascade":
        payload = {"id": str(value.get("parent") or "")}
        if value.get("child"):
            payload["child"] = {"id": str(value["child"])}
        return payload
    if control == "user":
        return {"name": str(value)}
    return {default_kind: value} if default_kind else value


def _labels(command: CreateIssueCommand) -> list[str]:
    labels = list(command.labels)
    if command.source_system and command.source_id:
        labels.extend(("clone_external", f"source_{_safe(command.source_system)}"))
    return list(dict.fromkeys(item for item in labels if item))


def _description(command: CreateIssueCommand) -> str:
    if command.description_includes_source_identity:
        return command.description
    lines = [command.description]
    if command.source_system and command.source_id:
        lines.extend(("", f"Source: {command.source_system}", f"Source ID: {command.source_id}"))
        if command.source_url:
            lines.append(f"Source URL: {command.source_url}")
    return "\n".join(lines).strip()


def _safe(value: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._") or "unknown"


def _jql(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _public_user(payload: dict[str, Any]) -> dict[str, str]:
    return {
        "account": str(payload.get("name") or payload.get("accountId") or ""),
        "display_name": str(payload.get("displayName") or payload.get("name") or ""),
        "avatar_url": str((payload.get("avatarUrls") or {}).get("48x48") or ""),
    }


class _AttachmentStream:
    def __init__(self, source: Any, filename: str, cancellation: AttachmentCancellation | None) -> None:
        self._source = source
        self.name = filename
        self._cancellation = cancellation

    def read(self, size: int = -1) -> bytes:
        if self._cancellation is not None:
            self._cancellation.raise_if_cancelled()
        return self._source.read(size)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._source, name)
