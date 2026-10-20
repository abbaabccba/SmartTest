from __future__ import annotations

from sqlalchemy import select, update, delete, text
from .schema import jira_issues, jira_issue_links, jira_issue_attachments, jira_issue_comments, jira_issue_components, jira_issue_custom_fields, jira_issue_descriptions, jira_issue_detail_states, jira_issue_fix_versions, jira_issue_labels, jira_issue_release_facts, jira_release_field_metadata

from ..database import upsert, WebDatabase

from dataclasses import replace
from datetime import datetime, timezone
import json
from typing import Callable, Iterable

from core.domain.detail import DetailSection, DetailState
from core.domain.values import FieldBag, NamedValue, PersonRef, SourceRevision
from core.jira.domain import (
    Issue,
    IssueAttachment,
    IssueComment,
    IssueDetails,
    IssueIdentity,
    IssueLink,
    IssuePage,
    IssueRef,
    JiraProjectRef,
    RichText,
)



_SECTIONS = ("description", "comments", "attachments", "links", "custom_fields")


class JiraIssueRepository:
    def __init__(self, database: WebDatabase):
        self.database = database

    def get(self, issue_key: str, details: IssueDetails) -> Issue | None:
        with self.database.connect() as connection:
            row = connection.execute(
                text("SELECT * FROM jira_issues WHERE issue_key=:issue_key"), {"issue_key": issue_key}
            ).fetchone()
            if row is None:
                return None
            issue = self._issue_from_row(connection, row)
            changes = {
                section: self._load_section(connection, row[0], section)
                for section in details.sections()
            }
            return replace(issue, **changes)

    def list(self, query: str = "", page: int = 0, page_size: int = 100) -> IssuePage:
        with self.database.connect() as connection:
            total = int(connection.execute(text('SELECT count(*) FROM jira_issues')).fetchone()[0])
            rows = connection.execute(
                text("SELECT * FROM jira_issues ORDER BY issue_key LIMIT :limit OFFSET :offset"),
                {"limit": int(page_size), "offset": int(page) * int(page_size)},
            ).fetchall()
            issues = tuple(self._issue_from_row(connection, row) for row in rows)
        return IssuePage(issues, int(page), int(page_size), total)

    def save_core(self, issues: Iterable[Issue]) -> None:
        cached_at = _now()
        with self.database.transaction() as connection:
            batch = []
            identities = set()
            for issue in issues:
                # A repeated identity must observe earlier revisions and child writes.
                if len(batch) == 500 or issue.identity.id in identities:
                    self._save_core_batch(connection, batch, cached_at)
                    batch = []
                    identities.clear()
                batch.append(issue)
                identities.add(issue.identity.id)
            if batch:
                self._save_core_batch(connection, batch, cached_at)

    @staticmethod
    def _save_core_batch(connection, issues, cached_at):
        identities = [issue.identity.id for issue in issues]
        previous = dict(
            connection.execute(
                select(jira_issues.c.issue_id, jira_issues.c.source_revision).where(
                    jira_issues.c.issue_id.in_(identities)
                )
            ).all()
        )
        rows = [_issue_values(issue, cached_at) for issue in issues]
        upsert(
            connection,
            jira_issues,
            rows,
            conflict_columns=("issue_id",),
            updates=tuple(name for name in rows[0] if name != "issue_id"),
        )
        connection.execute(
            delete(jira_issue_labels).where(
                jira_issue_labels.c.issue_id.in_(identities)
            )
        )
        labels = [
            {"issue_id": issue.identity.id, "label": label}
            for issue in issues
            for label in issue.labels
        ]
        if labels:
            connection.execute(jira_issue_labels.insert(), labels)
        connection.execute(
            delete(jira_issue_components).where(
                jira_issue_components.c.issue_id.in_(identities)
            )
        )
        components = [
            {
                "issue_id": issue.identity.id,
                "component_id": component.id,
                "component_name": component.name,
            }
            for issue in issues
            for component in issue.components
        ]
        if components:
            connection.execute(jira_issue_components.insert(), components)
        # Read current rows after the parent upsert, even under REPEATABLE READ.
        existing_sections = set(
            connection.execute(
                select(
                    jira_issue_detail_states.c.issue_id,
                    jira_issue_detail_states.c.section_name,
                )
                .where(
                    jira_issue_detail_states.c.issue_id.in_(identities),
                    jira_issue_detail_states.c.section_name.in_(_SECTIONS),
                )
                .with_for_update()
            ).tuples()
        )
        missing_sections = [
            {
                "issue_id": issue.identity.id,
                "section_name": section,
                "cached_at": cached_at,
                "state": "unloaded",
                "source_revision": "",
                "error_code": "",
            }
            for issue in issues
            for section in _SECTIONS
            if (issue.identity.id, section) not in existing_sections
        ]
        if missing_sections:
            upsert(
                connection,
                jira_issue_detail_states,
                missing_sections,
                conflict_columns=("issue_id", "section_name"),
                updates=(),
            )
        changed = [
            issue.identity.id
            for issue in issues
            if issue.identity.id in previous
            and str(previous[issue.identity.id]) != issue.revision.value
        ]
        if changed:
            connection.execute(
                update(jira_issue_detail_states)
                .where(
                    jira_issue_detail_states.c.issue_id.in_(changed),
                    jira_issue_detail_states.c.state == "loaded",
                )
                .values(state="stale", cached_at=cached_at)
            )

    def replace_description(self, issue_key: str, section: DetailSection[RichText]) -> None:
        def write(connection, issue_id):
            connection.execute(
                text("DELETE FROM jira_issue_descriptions WHERE issue_id=:issue_id"), {"issue_id": issue_id}
            )
            if section.value is not None:
                encoded = json.dumps(section.value.value, ensure_ascii=False)
                connection.execute(
                    jira_issue_descriptions.insert().values({"issue_id": issue_id, "content_json": encoded})
                )
        self._replace(issue_key, "description", section, write)

    def replace_comments(self, issue_key: str, section: DetailSection[tuple[IssueComment, ...]]) -> None:
        def write(connection, issue_id):
            connection.execute(
                text("DELETE FROM jira_issue_comments WHERE issue_id=:issue_id"), {"issue_id": issue_id}
            )
            for item in section.value or ():
                connection.execute(
                    jira_issue_comments.insert().values(
                        {
                            "issue_id": issue_id,
                            "comment_id": item.id,
                            "body_json": json.dumps(item.body, ensure_ascii=False),
                            "author_identity": item.author.identity if item.author else None,
                            "author_account": item.author.account if item.author else None,
                            "author_display_name": item.author.display_name if item.author else None,
                            "created_at": _time(item.created_at),
                            "updated_at": _time(item.updated_at),
                        }
                    )
                )
        self._replace(issue_key, "comments", section, write)

    def replace_attachments(self, issue_key: str, section: DetailSection[tuple[IssueAttachment, ...]]) -> None:
        def write(connection, issue_id):
            connection.execute(
                text("DELETE FROM jira_issue_attachments WHERE issue_id=:issue_id"), {"issue_id": issue_id}
            )
            for item in section.value or ():
                connection.execute(
                    jira_issue_attachments.insert().values(
                        {
                            "issue_id": issue_id,
                            "attachment_id": item.id,
                            "filename": item.filename,
                            "url": item.url,
                            "size": item.size,
                            "author_identity": item.author.identity if item.author else None,
                            "author_account": item.author.account if item.author else None,
                            "author_display_name": item.author.display_name if item.author else None,
                        }
                    )
                )
        self._replace(issue_key, "attachments", section, write)

    def replace_links(self, issue_key: str, section: DetailSection[tuple[IssueLink, ...]]) -> None:
        def write(connection, issue_id):
            connection.execute(
                text("DELETE FROM jira_issue_links WHERE issue_id=:issue_id"), {"issue_id": issue_id}
            )
            for item in section.value or ():
                connection.execute(jira_issue_links.insert().values(issue_id=issue_id, link_id=item.id,
                    link_type=item.link_type, direction=item.direction, target_id=item.issue.id,
                    target_key=item.issue.key, target_web_url=item.issue.web_url, target_summary=item.issue.summary))
        self._replace(issue_key, "links", section, write)

    def replace_custom_fields(self, issue_key: str, section: DetailSection[FieldBag]) -> None:
        def write(connection, issue_id):
            connection.execute(
                text("DELETE FROM jira_issue_custom_fields WHERE issue_id=:issue_id"), {"issue_id": issue_id}
            )
            for key, value in section.value.values if section.value is not None else ():
                connection.execute(
                    jira_issue_custom_fields.insert().values(
                        {
                            "issue_id": issue_id,
                            "field_key": key,
                            "value_json": json.dumps(value, ensure_ascii=False),
                        }
                    )
                )
        self._replace(issue_key, "custom_fields", section, write)

    def replace_release_fields(self, issue_key: str, fields: dict, field_metadata: dict) -> None:
        """Project stable release facts by Jira field names, never hard-coded custom IDs."""
        names = {str(name).strip().casefold(): str(key) for key, name in field_metadata.items()}
        cached_at = _now()
        with self.database.transaction() as connection:
            row = connection.execute(
                text("SELECT issue_id FROM jira_issues WHERE issue_key=:issue_key"), {"issue_key": issue_key}
            ).fetchone()
            if row is None:
                raise KeyError(issue_key)
            issue_id = row[0]
            def named(name):
                return fields.get(names.get(name.casefold(), ""))
            upsert(
                connection,
                jira_issue_release_facts,
                {
                    "issue_id": issue_id,
                    "project_business_id": _display_value(named("Project ID")),
                    "software_release": _display_value(named("Software Release")),
                    "severity": _display_value(named("Severity")),
                    "compare_status": _display_value(named("Compare Status")),
                    "qa_assignee_identity": _identity_value(named("QA Assignee")),
                    "manager_identity": _identity_value(named("Manager")),
                    "resolved_at": str(fields.get("resolutiondate") or ""),
                },
                conflict_columns=("issue_id",),
                updates=(
                    "project_business_id",
                    "software_release",
                    "severity",
                    "compare_status",
                    "qa_assignee_identity",
                    "manager_identity",
                    "resolved_at",
                ),
            )
            connection.execute(
                text("DELETE FROM jira_issue_fix_versions WHERE issue_id=:issue_id"), {"issue_id": issue_id}
            )
            for version in fields.get("fixVersions") or ():
                if not isinstance(version, dict):
                    continue
                connection.execute(
                    jira_issue_fix_versions.insert().values(
                        {
                            "issue_id": issue_id,
                            "version_id": str(version.get("id") or ""),
                            "version_name": str(version.get("name") or ""),
                            "released": (
                                int(version["released"]) if isinstance(version.get("released"), bool) else None
                            ),
                            "release_date": str(version.get("releaseDate") or ""),
                        }
                    )
                )
            for key, name in field_metadata.items():
                if str(name).strip().casefold() in {
                    "project id", "software release", "severity", "compare status", "qa assignee", "manager"
                }:
                    upsert(
                        connection,
                        jira_release_field_metadata,
                        {"field_name": str(name).strip(), "field_key": str(key), "cached_at": cached_at},
                        conflict_columns=("field_name",),
                        updates=("field_key", "cached_at"),
                    )

    def mark_details_stale(self, issue_key, sections):
        names = tuple(name for name in sections if name in _SECTIONS)
        if not names:
            return
        with self.database.transaction() as connection:
            issue_id = select(jira_issues.c.issue_id).where(jira_issues.c.issue_key == issue_key).scalar_subquery()
            connection.execute(update(jira_issue_detail_states).where(jira_issue_detail_states.c.issue_id == issue_id,
                jira_issue_detail_states.c.section_name.in_(names), jira_issue_detail_states.c.state == 'loaded').values(state='stale', cached_at=_now()))

    def delete(self, issue_key: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                text("DELETE FROM jira_issues WHERE issue_key=:issue_key"), {"issue_key": issue_key}
            )

    def clear(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(text('DELETE FROM jira_issues'))
            connection.execute(text('DELETE FROM jira_sync_state'))

    def _replace(self, issue_key: str, name: str, section: DetailSection, writer: Callable) -> None:
        # These replaceable detail writes must not retain DELETE range gap locks
        # across another issue's first INSERT. Read snapshots keep their default isolation.
        with self.database.transaction(isolation_level='READ COMMITTED') as connection:
            row = connection.execute(
                text("SELECT issue_id FROM jira_issues WHERE issue_key=:issue_key"), {"issue_key": issue_key}
            ).fetchone()
            if row is None:
                raise KeyError(issue_key)
            writer(connection, row[0])
            upsert(
                connection,
                jira_issue_detail_states,
                {
                    "issue_id": row[0],
                    "section_name": name,
                    "state": section.state.value,
                    "source_revision": section.source_revision,
                    "error_code": section.error_code,
                    "has_value": int(section.value is not None),
                    "cached_at": _now(),
                },
                conflict_columns=("issue_id", "section_name"),
                updates=("state", "source_revision", "error_code", "has_value", "cached_at"),
            )

    def _issue_from_row(self, connection, row) -> Issue:
        labels = tuple(
            (
                item[0]
                for item in connection.execute(
                    text("SELECT label FROM jira_issue_labels WHERE issue_id=:issue_id ORDER BY label"),
                    {"issue_id": row[0]},
                )
            )
        )
        components = tuple(
            (
                NamedValue(item[0], item[1])
                for item in connection.execute(
                    text(
                        "SELECT component_id,component_name FROM jira_issue_components WHERE issue_id=:issue_id ORDER BY component_id,component_name"
                    ),
                    {"issue_id": row[0]},
                )
            )
        )
        return Issue(
            IssueIdentity(row[0], row[1], row[2]), row[3],
            JiraProjectRef(row[5], row[4], row[6]),
            NamedValue(row[7], row[8]), NamedValue(row[9], row[10]),
            _named(row[11], row[12]), _person(row[13:16]), _person(row[16:19]),
            _datetime(row[19]), _datetime(row[20]), labels, SourceRevision(row[21]),
            _person(row[23:26]), components, _named(row[26], row[27]),
        )

    def _load_section(self, connection, issue_id: str, name: str) -> DetailSection:
        state = connection.execute(
            text(
                "SELECT state,source_revision,error_code,has_value FROM jira_issue_detail_states\n            WHERE issue_id=:issue_id AND section_name=:section_name"
            ),
            {"issue_id": issue_id, "section_name": name},
        ).fetchone()
        if state is None:
            return DetailSection()
        detail_state = DetailState(state[0])
        value = self._load_value(connection, issue_id, name) if bool(state[3]) else None
        return DetailSection(detail_state, value, state[1], state[2])

    @staticmethod
    def _load_value(connection, issue_id: str, name: str):
        if name == "description":
            row = connection.execute(
                text("SELECT content_json FROM jira_issue_descriptions WHERE issue_id=:issue_id"),
                {"issue_id": issue_id},
            ).fetchone()
            return RichText(json.loads(row[0])) if row else None
        if name == "comments":
            return tuple(
                (
                    IssueComment(
                        row[0], json.loads(row[1]), _person(row[2:5]), _datetime(row[5]), _datetime(row[6])
                    )
                    for row in connection.execute(
                        text(
                            "SELECT comment_id,body_json,author_identity,author_account,author_display_name,created_at,updated_at FROM jira_issue_comments WHERE issue_id=:issue_id ORDER BY comment_id"
                        ),
                        {"issue_id": issue_id},
                    )
                )
            )
        if name == "attachments":
            return tuple(
                (
                    IssueAttachment(row[0], row[1], row[2], row[3], _person(row[4:7]))
                    for row in connection.execute(
                        text(
                            "SELECT attachment_id,filename,url,size,author_identity,author_account,author_display_name FROM jira_issue_attachments WHERE issue_id=:issue_id ORDER BY attachment_id"
                        ),
                        {"issue_id": issue_id},
                    )
                )
            )
        if name == "links":
            return tuple(
                (
                    IssueLink(row[0], row[1], row[2], IssueRef(row[3], row[4], row[5], row[6]))
                    for row in connection.execute(
                        text(
                            "SELECT link_id,link_type,direction,target_id,target_key,target_web_url,target_summary FROM jira_issue_links WHERE issue_id=:issue_id ORDER BY link_id"
                        ),
                        {"issue_id": issue_id},
                    )
                )
            )
        return FieldBag(
            tuple(
                (
                    (row[0], json.loads(row[1]))
                    for row in connection.execute(
                        text(
                            "SELECT field_key,value_json FROM jira_issue_custom_fields WHERE issue_id=:issue_id ORDER BY field_key"
                        ),
                        {"issue_id": issue_id},
                    )
                )
            )
        )


def _issue_values(issue: Issue, cached_at: str) -> dict:
    return {
        "issue_id": issue.identity.id,
        "issue_key": issue.identity.key,
        "web_url": issue.identity.web_url,
        "summary": issue.summary,
        "project_id": issue.project.id,
        "project_key": issue.project.key,
        "project_name": issue.project.name,
        "status_id": issue.status.id,
        "status_name": issue.status.name,
        "issue_type_id": issue.issue_type.id,
        "issue_type_name": issue.issue_type.name,
        "priority_id": issue.priority.id if issue.priority else None,
        "priority_name": issue.priority.name if issue.priority else None,
        "assignee_identity": issue.assignee.identity if issue.assignee else None,
        "assignee_account": issue.assignee.account if issue.assignee else None,
        "assignee_display_name": issue.assignee.display_name if issue.assignee else None,
        "reporter_identity": issue.reporter.identity if issue.reporter else None,
        "reporter_account": issue.reporter.account if issue.reporter else None,
        "reporter_display_name": issue.reporter.display_name if issue.reporter else None,
        "created_at": _time(issue.created_at),
        "updated_at": _time(issue.updated_at),
        "source_revision": issue.revision.value,
        "cached_at": cached_at,
        "creator_identity": issue.creator.identity if issue.creator else None,
        "creator_account": issue.creator.account if issue.creator else None,
        "creator_display_name": issue.creator.display_name if issue.creator else None,
        "resolution_id": issue.resolution.id if issue.resolution else None,
        "resolution_name": issue.resolution.name if issue.resolution else None,
    }




def _person(values) -> PersonRef | None:
    return PersonRef(str(values[0]), str(values[1] or ""), str(values[2] or "")) if values[0] is not None else None


def _named(identity, name) -> NamedValue | None:
    return NamedValue(str(identity or ""), str(name or "")) if identity is not None or name is not None else None


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _display_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("value") or value.get("name") or value.get("displayName") or "")
    if isinstance(value, (list, tuple)):
        return ", ".join(filter(None, (_display_value(item) for item in value)))
    return str(value)


def _identity_value(value) -> str:
    if isinstance(value, dict):
        return str(value.get("accountId") or value.get("key") or value.get("name") or value.get("displayName") or "")
    return _display_value(value)
