from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from core.issues.create_schema import (
    CreateFieldControl,
    CreateFieldSchema,
)
from core.jira.commands import CreateIssueCommand
from core.jira.create_draft import (
    JiraCreateDraft, JiraCreateDraftField, find_create_option, validate_create_value,
)
from core.tools.SmartHome.redmine.jira_mapper import RedmineToJiraMapper
from core.issues.third_party_bug import ThirdPartyBugAttachment
from core.tools.SmartHome.redmine.mapping import redmine_tracker_to_jira_type
from core.tools.SmartHome.redmine.models import RedmineIssueDetail, RedmineProject


@dataclass
class CloneDraft(JiraCreateDraft):
    source_id: str
    source_url: str
    source_attachments: tuple[ThirdPartyBugAttachment, ...] = ()

    @property
    def id(self) -> str:
        return self.source_id

    def to_command(self) -> CreateIssueCommand:
        controls = {
            field.field_id: (
                field.schema.payload_control or field.schema.control
            ).value
            for field in self.fields
            if field.value not in (None, "", [], {})
        }
        standard_ids = {
            "project",
            "issuetype",
            "summary",
            "description",
            "priority",
            "components",
        }
        extra_fields = {
            field.field_id: field.value
            for field in self.fields
            if field.field_id not in standard_ids and field.value not in (None, "", [], {})
        }
        return RedmineToJiraMapper().to_create_command(
            project_key=str(self.value("project") or ""),
            issue_type=str(self.value("issuetype") or ""),
            summary=str(self.value("summary") or ""),
            description=str(self.value("description") or ""),
            priority=str(self.value("priority") or ""),
            components=tuple(self.value("components") or ()),
            source_id=self.source_id,
            source_url=self.source_url,
            description_includes_source_identity=True,
            extra_fields=extra_fields,
            field_controls=controls,
        )


class RedmineCloneDraftService:
    def build(
        self,
        issue: RedmineIssueDetail,
        project: RedmineProject,
        schema: Iterable[CreateFieldSchema],
        account: str,
        department: str,
        prepared_description: str,
        assignee_account: str = "",
    ) -> CloneDraft:
        fields = tuple(
            _draft_field(
                item,
                issue=issue,
                project=project,
                account=str(account or "").strip(),
                department=str(department or "").strip(),
                assignee_account=str(assignee_account or "").strip(),
                prepared_description=str(prepared_description or ""),
            )
            for item in schema
        )
        return CloneDraft(
            source_id=issue.id,
            source_url=issue.url,
            fields=fields,
            source_attachments=issue.attachments,
        )


def _draft_field(
    schema: CreateFieldSchema,
    *,
    issue: RedmineIssueDetail,
    project: RedmineProject,
    account: str,
    department: str,
    assignee_account: str,
    prepared_description: str,
) -> JiraCreateDraftField:
    initial = _initial_value(
        schema,
        issue=issue,
        project=project,
        account=account,
        department=department,
        assignee_account=assignee_account,
        prepared_description=prepared_description,
    )
    value, option_error = _resolve_options(schema, initial)
    error = option_error or validate_create_value(schema, value)
    return JiraCreateDraftField(schema=schema, value=value, error=error)


def _initial_value(
    schema: CreateFieldSchema,
    *,
    issue: RedmineIssueDetail,
    project: RedmineProject,
    account: str,
    department: str,
    assignee_account: str,
    prepared_description: str,
) -> Any:
    field_id = schema.field_id
    name = schema.name.strip().casefold()
    if field_id == "project" or name == "project":
        return "SH"
    if field_id == "issuetype" or name == "issue type":
        return redmine_tracker_to_jira_type(issue.tracker)
    if field_id == "summary" or name == "summary":
        return issue.subject
    if field_id == "description" or name == "description":
        return prepared_description
    if name == "attachment links":
        return issue.url
    if field_id == "priority" or name == "priority":
        return "P2"
    if field_id == "components" or name in {"component/s", "components"}:
        return ["Customization"]
    if name == "channel of reporter":
        return {"parent": "Customer-Feedback", "child": ""}
    if name == "severity":
        return "Major"
    if name == "product":
        return ["BDS Reference"]
    if name == "project id":
        return [project.project_id] if project.project_id else []
    if field_id == "reporter" or name == "reporter":
        return account
    if field_id == "assignee" or name == "assignee":
        return assignee_account or account
    if name in {"manager", "fae manager"}:
        return "fred.chen"
    if name == "fae coworker":
        return account if department == "FAE-SW" else ""
    if schema.value is not None:
        return schema.value
    if schema.control == CreateFieldControl.MULTI:
        return []
    if schema.control == CreateFieldControl.CASCADE:
        return {}
    return ""


def _resolve_options(schema: CreateFieldSchema, initial: Any) -> tuple[Any, str]:
    if not schema.options or initial in (None, "", [], {}):
        return initial, ""
    if schema.control == CreateFieldControl.CASCADE:
        parent_label = str((initial or {}).get("parent") or "")
        child_label = str((initial or {}).get("child") or "")
        parent = find_create_option(schema.options, parent_label)
        child = find_create_option(parent.children, child_label) if parent else None
        if parent is None or (child_label and child is None):
            return {}, f"Configured option is unavailable for {schema.name}"
        return {
            "parent": parent.value,
            "child": child.value if child else "",
        }, ""
    if schema.control == CreateFieldControl.MULTI:
        resolved = [find_create_option(schema.options, str(label)) for label in initial]
        if any(item is None for item in resolved):
            return [], f"Configured option is unavailable for {schema.name}"
        return [item.value for item in resolved if item is not None], ""
    if schema.control == CreateFieldControl.SINGLE:
        resolved = find_create_option(schema.options, str(initial))
        if resolved is None:
            return "", f"Configured option is unavailable for {schema.name}"
        return resolved.value, ""
    return initial, ""
