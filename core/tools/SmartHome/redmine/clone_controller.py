from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, Awaitable, Callable, Iterable

from core.issues.description import render_notes_description
from core.jira.attachments import (
    AttachmentCancellation,
    AttachmentTransferResult,
)
from core.jira.commands import CreateIssueResult
from core.jira.domain import IssueRef
from core.logging import smart_log
from core.tools.SmartHome.redmine.clone_draft import (
    CloneDraft,
    RedmineCloneDraftService,
)
from core.tools.SmartHome.redmine.mapping import redmine_tracker_to_jira_type


@dataclass(frozen=True)
class CloneBatchSnapshot:
    state: str
    selected_ids: tuple[str, ...]
    drafts: tuple[dict[str, Any], ...]
    loaded: int
    total: int
    error: str
    first_invalid_issue_id: str
    first_invalid_field_id: str


@dataclass(frozen=True)
class CloneOperation:
    generation: int
    kind: str
    awaitable: Awaitable[Any]


class RedmineCloneController:
    """Own the Redmine clone batch state machine and Jira create orchestration."""

    def __init__(
        self,
        issue_controller,
        *,
        jira_dependencies: Callable[[], tuple[Any, Any, Any]] | None = None,
        account: Callable[[], str] | None = None,
        identity: Callable[[str, dict[str, Any]], tuple[str, dict[str, str]]]
        | None = None,
        load_detail: Callable[[Any], Awaitable[Any]] | None = None,
        download_attachments: Callable[
            [Iterable[Any], Any], Awaitable[Any]
        ] | None = None,
        draft_service: RedmineCloneDraftService | None = None,
        prepare_records: Callable[[], Awaitable[Any]] | None = None,
        submit_records: Callable[[list[dict[str, Any]]], Awaitable[Any]] | None = None,
        search_users: Callable[[str, str, str], Awaitable[Any]] | None = None,
        update_source_subject: Callable[[str, str], Awaitable[Any]] | None = None,
        attachment_cancel_wait: float = 35.0,
        task_manager=None,
    ):
        self._issue_controller = issue_controller
        self._jira_dependencies = jira_dependencies or (lambda: (None, None, None))
        self._account = account or (lambda: "")
        self._identity = identity or (lambda _account, _user: ("", {}))
        self._load_detail = load_detail
        self._download_attachments = download_attachments
        self._draft_service = draft_service or RedmineCloneDraftService()
        self._prepare_records_override = prepare_records
        self._submit_records_override = submit_records
        self._search_users_override = search_users
        self._update_source_subject = update_source_subject
        self._attachment_cancel_wait = attachment_cancel_wait
        self._task_manager = task_manager
        self._generation = 0
        self._state = "idle"
        self._selected_ids: list[str] = []
        self._records: list[dict[str, Any]] = []
        self._loaded = 0
        self._total = 0
        self._error = ""
        self._first_invalid_issue_id = ""
        self._first_invalid_field_id = ""
        self._user_options: dict[tuple[str, str], list[dict[str, Any]]] = {}
        self._user_search_generation = 0
        self._user_searches: dict[tuple[str, str], tuple[str, int]] = {}

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def snapshot(self) -> CloneBatchSnapshot:
        return replace(self.status, drafts=self.draft_payloads())

    @property
    def status(self) -> CloneBatchSnapshot:
        return CloneBatchSnapshot(
            state=self._state,
            selected_ids=tuple(self._selected_ids),
            drafts=(),
            loaded=self._loaded,
            total=self._total,
            error=self._error,
            first_invalid_issue_id=self._first_invalid_issue_id,
            first_invalid_field_id=self._first_invalid_field_id,
        )

    def draft_payloads(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._record_payload(item) for item in self._records)

    def reset(self) -> None:
        self._generation += 1
        self._state = "idle"
        self._selected_ids = []
        self._records = []
        self._loaded = 0
        self._total = 0
        self._error = ""
        self._first_invalid_issue_id = ""
        self._first_invalid_field_id = ""
        self._user_options = {}
        self._user_searches = {}

    def begin_selection(self) -> bool:
        if self._state != "idle":
            return False
        self._state = "selecting"
        self._selected_ids = []
        self._error = ""
        return True

    def toggle_selection(
        self, issue_id: str, selected: bool, rows: Iterable[dict[str, Any]]
    ) -> bool:
        if self._state != "selecting":
            return False
        issue_id = str(issue_id or "").strip()
        rows = list(rows)
        row = next(
            (
                item
                for item in rows
                if str(item.get("id") or item.get("key") or "") == issue_id
            ),
            None,
        )
        if row is None or row.get("cloneStatus") == "cloned":
            return False
        selected_ids = set(self._selected_ids)
        if selected:
            selected_ids.add(issue_id)
        else:
            selected_ids.discard(issue_id)
        self._selected_ids = [
            str(item.get("id") or item.get("key") or "")
            for item in rows
            if str(item.get("id") or item.get("key") or "") in selected_ids
        ]
        return True

    def cancel_selection(self) -> bool:
        if self._state != "selecting":
            return False
        self.reset()
        return True

    def reconcile_selection(self, rows: Iterable[dict[str, Any]]) -> bool:
        if self._state != "selecting":
            return False
        available_ids = {
            str(item.get("id") or item.get("key") or "")
            for item in rows
        }
        selected_ids = [
            issue_id for issue_id in self._selected_ids if issue_id in available_ids
        ]
        if selected_ids == self._selected_ids:
            return False
        self._selected_ids = selected_ids
        return True

    def start_prepare(self) -> CloneOperation | None:
        if self._state not in ("selecting", "prepare_failed") or not self._selected_ids:
            return None
        self._state = "loading"
        self._records = []
        self._loaded = 0
        self._total = len(self._selected_ids)
        self._error = ""
        awaitable = (
            self._prepare_records_override()
            if self._prepare_records_override is not None
            else self._prepare_records()
        )
        return self._operation("prepare", awaitable)

    def update_draft(self, issue_id: str, field_id: str, value: Any) -> bool:
        if self._state != "editing":
            return False
        record = self._record(issue_id)
        if record is None:
            return False
        try:
            record["draft"].update(str(field_id or ""), value)
        except (KeyError, TypeError, ValueError) as exc:
            record["error"] = str(exc)
            record["errorFieldId"] = str(field_id or "")
        else:
            record["error"] = ""
            record["errorFieldId"] = ""
        self._first_invalid_issue_id = ""
        self._first_invalid_field_id = ""
        return True

    def field_payload(
        self, issue_id: str, field_id: str
    ) -> dict[str, Any] | None:
        record = self._record(issue_id)
        if record is None:
            return None
        draft = record["draft"]
        field = draft._field(str(field_id or ""))
        return self._field_payload(draft, field) if field is not None else None

    def field_payloads(self, issue_id: str) -> dict[str, dict[str, Any]]:
        record = self._record(issue_id)
        if record is None:
            return {}
        draft = record["draft"]
        return {
            field.field_id: self._field_payload(draft, field)
            for field in draft.fields
        }

    def start_submit(self) -> CloneOperation | None:
        if self._state != "editing":
            return None
        self._state = "validating"
        self._first_invalid_issue_id = ""
        self._first_invalid_field_id = ""
        for record in self._records:
            if record["error"]:
                self._first_invalid_issue_id = record["draft"].source_id
                self._first_invalid_field_id = record["errorFieldId"]
                self._state = "editing"
                return None
            errors = record["draft"].errors
            if errors:
                self._first_invalid_issue_id = record["draft"].source_id
                self._first_invalid_field_id = errors[0].field_id
                self._state = "editing"
                return None
        return self._start_submit_records(self._records)

    def retry_failed(self) -> CloneOperation | None:
        if self._state != "partial_failed":
            return None
        failed = [
            item
            for item in self._records
            if item["result"] is not None
            and (
                item["result"].issue_state == "create_failed"
                or any(
                    result.retryable
                    for result in item["result"].attachment_results
                )
            )
        ]
        return self._start_submit_records(failed) if failed else None

    def close_batch(self) -> bool:
        if self._state == "submitting":
            return False
        selected_ids = list(self._selected_ids)
        completed = self._state == "completed"
        self.reset()
        if not completed:
            self._state = "selecting"
            self._selected_ids = selected_ids
        return True

    def start_user_search(
        self, issue_id: str, field_id: str, query: str
    ) -> CloneOperation | None:
        issue_id = str(issue_id or "")
        field_id = str(field_id or "")
        query = str(query or "").strip()
        key = (issue_id, field_id)
        if (
            self._state != "editing"
            or self._record(issue_id) is None
            or self._user_searches.get(key, ("", 0))[0] == query
        ):
            return None
        self._user_search_generation += 1
        generation = self._user_search_generation
        self._user_searches[key] = (query, generation)
        awaitable = (
            self._search_users_override(issue_id, field_id, query)
            if self._search_users_override is not None
            else self._search_users(issue_id, field_id, query)
        )
        return CloneOperation(generation, "users", awaitable)

    def accepts_result(self, generation: int, kind: str, result: Any) -> bool:
        if kind != "users":
            return generation == self._generation
        if isinstance(result, Exception):
            return any(
                active_generation == generation
                for _query, active_generation in self._user_searches.values()
            )
        issue_id, field_id, _users = result
        return self._user_searches.get(
            (str(issue_id), str(field_id)), ("", 0)
        )[1] == generation

    def apply_error(self, kind: str, error: Exception) -> None:
        self._error = str(error)
        if kind == "prepare":
            self._records = []
            self._loaded = 0
            self._state = "prepare_failed"
        elif kind == "submit":
            self._state = "partial_failed"

    def apply_result(self, kind: str, result: Any) -> None:
        if kind == "prepare":
            records, display_names = result
            self._records = list(records)
            for record in self._records:
                draft = record["draft"]
                for field in draft.fields:
                    account = (
                        str(field.value or "")
                        if field.schema.control.value == "user"
                        else ""
                    )
                    if account:
                        self._user_options[(draft.source_id, field.field_id)] = [
                            {
                                "value": account,
                                "label": display_names.get(account, account),
                                "avatarUrl": "",
                                "children": [],
                            }
                        ]
            self._loaded = len(self._records)
            self._state = "editing"
            return
        if kind == "users":
            issue_id, field_id, users = result
            existing = self._user_options.get((issue_id, field_id), [])
            fetched = [
                {
                    "value": str(item.get("account") or ""),
                    "label": str(item.get("display_name") or ""),
                    "avatarUrl": str(item.get("avatar_url") or ""),
                    "children": [],
                }
                for item in users
            ]
            fetched_values = {str(item.get("value") or "") for item in fetched}
            self._user_options[(issue_id, field_id)] = fetched + [
                item
                for item in existing
                if str(item.get("value") or "") not in fetched_values
            ]
            return
        if kind == "submit":
            self._apply_submit_result(result)

    def _operation(self, kind: str, awaitable: Awaitable[Any]) -> CloneOperation:
        self._generation += 1
        return CloneOperation(self._generation, kind, awaitable)

    def _start_submit_records(
        self, records: list[dict[str, Any]]
    ) -> CloneOperation:
        self._state = "submitting"
        self._loaded = 0
        self._total = len(records)
        self._error = ""
        awaitable = (
            self._submit_records_override(records)
            if self._submit_records_override is not None
            else self._submit_records(records)
        )
        return self._operation("submit", awaitable)

    async def _prepare_records(self):
        jira_client, _create_service, schema_service = self._jira_dependencies()
        if jira_client is None or schema_service is None:
            raise RuntimeError("Jira credentials are unavailable")
        account = str(self._account() or "").strip()
        current_user = jira_client.current_user()
        reporter = str(current_user.get("account") or "").strip()
        if not reporter:
            raise RuntimeError("Current Jira reporter identity is unavailable")
        department, display_names = self._identity(account, current_user)
        records = []
        schemas = {}
        for issue_id in self._selected_ids:
            source = self._issue_controller.source_for_issue(issue_id)
            project, item, detail = source.project, source.item, source.detail
            if project is None or item is None:
                raise RuntimeError(f"Redmine issue {issue_id} is unavailable")
            if detail is None:
                if self._load_detail is None:
                    raise RuntimeError(f"Redmine issue {issue_id} detail is unavailable")
                detail = await self._load_detail(source)
            issue_type = redmine_tracker_to_jira_type(detail.tracker)
            if issue_type not in schemas:
                schemas[issue_type] = schema_service.schema("SH", issue_type)
                attachment_fields = [
                    field
                    for field in schemas[issue_type]
                    if str(field.name or "").strip().casefold() == "attachment links"
                ]
                if len(attachment_fields) != 1:
                    raise RuntimeError(
                        "Jira create schema must contain exactly one Attachment links field"
                    )
            clone_draft = self._draft_service.build(
                issue=detail,
                project=project,
                schema=schemas[issue_type],
                account=reporter,
                assignee_account=_assignee_account(
                    detail.attr("Assignee")
                    or (detail.list_item.assignee if detail.list_item else ""),
                    reporter,
                    display_names,
                ),
                department=department,
                prepared_description=render_notes_description(detail.description),
            )
            records.append(_new_record(clone_draft))
        return records, display_names

    async def _submit_records(self, records: list[dict[str, Any]]):
        _client, create_service, _schema_service = self._jira_dependencies()
        if create_service is None:
            raise RuntimeError("Jira credentials are unavailable")
        results = []
        for record in records:
            clone_draft = record["draft"]
            try:
                payload = record.get("result")
                retrying_attachments = (
                    isinstance(payload, CreateIssueResult)
                    and payload.issue_state in {"created", "duplicate"}
                )
                if not retrying_attachments:
                    existing = create_service.check_issue_by_external_url(
                        project_key="SH", external_url=clone_draft.source_url
                    )
                    if existing:
                        payload = CreateIssueResult(
                            created=False,
                            issue_state="duplicate",
                            issue=existing,
                        )
                    else:
                        payload = create_service.create_issue(
                            clone_draft.to_command()
                        )
                if (
                    isinstance(payload, CreateIssueResult)
                    and payload.issue_state in {"created", "duplicate"}
                    and clone_draft.source_attachments
                ):
                    payload = await self._sync_source_attachments(
                        create_service,
                        clone_draft,
                        payload,
                        retry_only=retrying_attachments,
                    )
                if (
                    self._update_source_subject is not None
                    and not retrying_attachments
                    and isinstance(payload, CreateIssueResult)
                    and payload.created
                    and payload.issue_state == "created"
                    and payload.issue_key
                ):
                    try:
                        await self._update_source_subject(
                            clone_draft.source_id, payload.issue_key
                        )
                    except Exception:
                        smart_log(
                            "Redmine subject writeback failed",
                            domain="tool",
                            source="RedmineCloneController",
                            level="warning",
                            extra={
                                "issue_id": clone_draft.source_id,
                                "issue_key": payload.issue_key,
                            },
                            exc_info=True,
                        )
                results.append((clone_draft.source_id, payload))
            except Exception as exc:
                results.append(
                    (
                        clone_draft.source_id,
                        CreateIssueResult(
                            created=False,
                            issue_state="create_failed",
                            issue_error=str(exc) or type(exc).__name__,
                        ),
                    )
                )
        return results

    def _submit_blocking(self, label, work, *args, **kwargs):
        if self._task_manager is not None:
            return self._task_manager.submit(label, lambda _token, _progress: work(*args, **kwargs))
        from concurrent.futures import Future
        future = Future()
        try: future.set_result(work(*args, **kwargs))
        except Exception as error: future.set_exception(error)
        return future

    async def _run_blocking(self, label, work, *args, **kwargs):
        return await asyncio.wrap_future(self._submit_blocking(label, work, *args, **kwargs))

    async def _sync_source_attachments(
        self,
        create_service,
        clone_draft: CloneDraft,
        payload: CreateIssueResult,
        *,
        retry_only: bool,
    ) -> CreateIssueResult:
        issue_key = payload.issue_key or payload.existing_key
        previous = tuple(payload.attachment_results)
        all_sources = tuple(clone_draft.source_attachments)
        name_counts = Counter(item.filename for item in all_sources)
        duplicate_filenames = {
            filename for filename, count in name_counts.items() if count > 1
        }
        sources = all_sources
        if retry_only:
            retryable_ids = {
                item.source_id for item in previous if item.retryable
            }
            sources = tuple(
                item for item in sources if item.id in retryable_ids
            )
        if not sources:
            return payload
        if self._download_attachments is None:
            failed = tuple(
                AttachmentTransferResult(
                    source_id=item.id,
                    filename=item.filename,
                    size=None,
                    state="failed",
                    reason_code="source_downloader_unavailable",
                    retryable=True,
                )
                for item in sources
            )
            return _with_attachment_results(
                payload, _merge_attachment_results(previous, failed)
            )
        metadata = await self._run_blocking("jira-attachment-metadata", create_service.attachment_metadata)
        batch = None
        current: tuple[AttachmentTransferResult, ...] = ()
        executor = None
        deferred_cleanup = False
        try:
            batch = await self._download_attachments(
                sources,
                metadata,
                duplicate_filenames=duplicate_filenames,
            )
            cancellation = AttachmentCancellation()
            blocking = self._submit_blocking(
                "jira-attachment-upload", create_service.sync_attachments,
                issue_key, tuple(batch.attachments), metadata=metadata,
                prior_results=tuple(batch.results), cancellation=cancellation,
            )
            wrapped = asyncio.wrap_future(blocking)
            try:
                sync = await asyncio.shield(wrapped)
            except asyncio.CancelledError:
                cancellation.cancel()
                try:
                    await asyncio.wait_for(
                        asyncio.shield(wrapped),
                        timeout=self._attachment_cancel_wait,
                    )
                except asyncio.TimeoutError:
                    deferred_cleanup = True
                    blocking.add_done_callback(
                        lambda _done: _cleanup_staged_batch(
                            batch, clone_draft.source_id
                        )
                    )
                    smart_log(
                        "Jira attachment upload cancellation reached timeout",
                        domain="jira",
                        source="RedmineCloneController",
                        level="warning",
                        extra={
                            "issue_id": clone_draft.source_id,
                            "timeout_seconds": self._attachment_cancel_wait,
                        },
                    )
                except Exception:
                    pass
                raise
            current = sync.results
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            current = tuple(
                AttachmentTransferResult(
                    source_id=item.id,
                    filename=item.filename,
                    size=None,
                    state="failed",
                    reason_code="jira_sync_failed",
                    reason_args={
                        "detail": str(exc) or type(exc).__name__,
                        "error_type": type(exc).__name__,
                    },
                    retryable=True,
                )
                for item in sources
            )
        finally:
            if executor is not None:
                executor.shutdown(wait=False)
            if batch is not None and not deferred_cleanup:
                cleanup_result = _cleanup_staged_batch(
                    batch, clone_draft.source_id
                )
                if cleanup_result is not None:
                    current = tuple(current) + (cleanup_result,)
        return _with_attachment_results(
            payload,
            (
                _merge_attachment_results(previous, current)
                if retry_only
                else tuple(current)
            ),
        )

    async def _search_users(self, issue_id: str, field_id: str, query: str):
        jira_client, _create_service, _schema_service = self._jira_dependencies()
        users = (
            []
            if jira_client is None
            else jira_client.search_users(query, project_key="SH")
        )
        return issue_id, field_id, users

    def _apply_submit_result(self, result) -> None:
        resolved = {}
        for issue_id, payload in result:
            record = self._record(issue_id)
            if record is None:
                continue
            record["result"] = payload
            if payload.issue_state == "created":
                resolved[issue_id] = IssueRef(
                    key=payload.issue_key, web_url=payload.issue_url
                )
            elif payload.issue_state == "duplicate":
                resolved[issue_id] = IssueRef(
                    key=payload.existing_key or payload.issue_key,
                    web_url=payload.issue_url,
                )
        if resolved:
            self._issue_controller.record_clone_results(resolved)
        self._loaded = len(result)
        retryable_failure = any(
            item["result"] is not None
            and (
                item["result"].issue_state == "create_failed"
                or any(
                    attachment.retryable
                    for attachment in item["result"].attachment_results
                )
            )
            for item in self._records
        )
        self._state = "partial_failed" if retryable_failure else "completed"

    def _record(self, issue_id: str) -> dict[str, Any] | None:
        issue_id = str(issue_id or "")
        return next(
            (
                item
                for item in self._records
                if item["draft"].source_id == issue_id
            ),
            None,
        )

    def _record_payload(self, record: dict[str, Any]) -> dict[str, Any]:
        clone_draft = record["draft"]
        result = record["result"]
        visible_fields = []
        for field in clone_draft.fields:
            if (
                not field.schema.required
                and str(field.schema.name or "").strip().casefold()
                != "fae coworker"
                and str(field.schema.name or "").strip().casefold()
                != "assignee"
                and field.field_id != "priority"
                and str(field.schema.name or "").strip().casefold()
                != "attachment links"
            ):
                continue
            visible_fields.append(field)
        fields = [
            self._field_payload(clone_draft, field)
            for field in _order_people_fields(visible_fields)
        ]
        return {
            "issueId": clone_draft.source_id,
            "sourceUrl": clone_draft.source_url,
            "fields": fields,
            "errors": [
                {
                    "fieldId": item.field_id,
                    "message": item.message,
                    "blocking": item.blocking,
                }
                for item in clone_draft.errors
            ],
            "state": (
                "editing"
                if result is None
                else "failed"
                if result.issue_state == "create_failed"
                else result.issue_state
            ),
            "key": (
                result.issue_key or result.existing_key
                if result is not None
                else ""
            ),
            "url": result.issue_url if result is not None else "",
            "error": result.issue_error if result is not None else record["error"],
            "attachmentState": (
                result.attachment_state
                if result is not None
                else "none"
            ),
            "attachmentWarnings": (
                list(_attachment_warning_payloads(result.attachment_results))
                if result is not None
                else []
            ),
        }

    def _field_payload(self, clone_draft, field) -> dict[str, Any]:
        options = self._user_options.get(
            (clone_draft.source_id, field.field_id),
            [_option_payload(item) for item in field.schema.options],
        )
        return {
            "fieldId": field.field_id,
            "name": field.schema.name,
            "required": field.schema.required,
            "control": field.schema.control.value,
            "options": options,
            "value": field.value,
            "error": field.error,
        }


def _assignee_account(
    redmine_assignee: str,
    reporter_account: str,
    display_names: dict[str, str],
) -> str:
    wanted = " ".join(str(redmine_assignee or "").split()).casefold()
    if wanted:
        for account, display_name in display_names.items():
            normalized_account = " ".join(str(account or "").split()).casefold()
            normalized_name = " ".join(str(display_name or "").split()).casefold()
            if wanted in {normalized_account, normalized_name}:
                return str(account or "").strip()
    return str(reporter_account or "").strip()


def _order_people_fields(fields):
    names = {
        str(field.schema.name or "").strip().casefold(): field
        for field in fields
        if str(field.schema.name or "").strip().casefold()
        in {"reporter", "assignee", "manager"}
    }
    if set(names) != {"reporter", "assignee", "manager"}:
        return fields
    ordered = []
    inserted = False
    for field in fields:
        name = str(field.schema.name or "").strip().casefold()
        if name in names:
            if not inserted:
                ordered.extend(
                    names[item] for item in ("reporter", "assignee", "manager")
                )
                inserted = True
            continue
        ordered.append(field)
    return ordered


def _new_record(clone_draft: CloneDraft) -> dict[str, Any]:
    return {
        "draft": clone_draft,
        "error": "",
        "errorFieldId": "",
        "result": None,
    }


def _option_payload(option) -> dict[str, Any]:
    return {
        "value": option.value,
        "label": option.label,
        "avatarUrl": str(getattr(option, "avatar_url", "") or ""),
        "children": [_option_payload(child) for child in option.children],
    }


def _merge_attachment_results(
    previous: tuple[AttachmentTransferResult, ...],
    current: tuple[AttachmentTransferResult, ...],
) -> tuple[AttachmentTransferResult, ...]:
    current_ids = {item.source_id for item in current}
    return tuple(
        item for item in previous if item.source_id not in current_ids
    ) + tuple(current)


def _with_attachment_results(
    payload: CreateIssueResult,
    results: tuple[AttachmentTransferResult, ...],
) -> CreateIssueResult:
    if not results:
        state = "none"
    elif any(item.state == "failed" for item in results):
        state = "partial_failed"
    elif any(item.state == "oversized" for item in results):
        state = "oversized"
    else:
        state = "complete"
    return replace(
        payload,
        attachment_state=state,
        attachment_results=results,
    )


def _attachment_warning_payloads(
    results: tuple[AttachmentTransferResult, ...],
) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "sourceId": item.source_id,
            "filename": item.filename,
            "size": item.size,
            "state": item.state,
            "reasonCode": item.reason_code,
            "reasonArgs": dict(item.reason_args),
            "retryable": item.retryable,
        }
        for item in results
        if item.state not in {"uploaded", "already_present"}
    )


def _cleanup_staged_batch(
    batch,
    issue_id: str,
) -> AttachmentTransferResult | None:
    try:
        batch.close()
    except Exception as exc:
        smart_log(
            "Redmine attachment temp cleanup failed",
            domain="tool",
            source="RedmineCloneController",
            level="warning",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={
                "issue_id": issue_id,
                "error_type": type(exc).__name__,
            },
        )
        return AttachmentTransferResult(
            source_id=f"cleanup:{issue_id}",
            filename="",
            size=None,
            state="failed",
            reason_code="temp_cleanup_failed",
            reason_args={
                "detail": str(exc) or type(exc).__name__,
                "error_type": type(exc).__name__,
            },
            retryable=False,
        )
    return None
