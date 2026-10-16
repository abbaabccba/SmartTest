from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select, update, bindparam, or_, text, func

from ..database import upsert, WebDatabase
from .schema import confluence_project_detail_states, confluence_project_evidence, confluence_project_fields, confluence_project_milestones, confluence_project_owners, confluence_project_role_people, confluence_project_roles, confluence_projects, project_current_releases

from dataclasses import replace
import json
from time import perf_counter
from typing import Callable, Iterable

from core.confluence.project import (
    ConfluencePageRef,
    ProductSpaceRef,
    Project,
    ProjectDetails,
    ProjectIdentity,
    ProjectMilestones,
    ProjectPage,
    ProjectQuery,
    ProjectRole,
    SourceEvidence,
)
from core.domain.detail import DetailSection, DetailState
from core.domain.values import FieldBag, NamedValue, PersonRef, SourceRevision
from core.logging import smart_log
from core.product_lines import product_line_name, confluence_space_key



_SECTIONS = ("roles", "milestones", "hardware", "software", "facts", "evidence")


class ConfluenceProjectRepository:
    def __init__(self, database: WebDatabase):
        self.database = database

    def get(self, project_id: str, details: ProjectDetails) -> Project | None:
        with self.database.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT * FROM confluence_projects\n                WHERE confluence_id=:confluence_id OR project_id=:project_id\n                ORDER BY CASE WHEN confluence_id=:confluence_id_p2 THEN 0 ELSE 1 END, product_space_key\n                LIMIT 1"
                ),
                {"confluence_id": project_id, "project_id": project_id, "confluence_id_p2": project_id},
            ).fetchone()
            if row is None:
                return None
            project = self._project_from_row(connection, row)
            changes = {
                section: self._load_section(connection, row[0], section)
                for section in details.sections()
            }
            return replace(project, **changes)

    def list(self, query: ProjectQuery, page=0, page_size=100, *, visible_ids=None):
        conditions = []
        table = confluence_projects
        if visible_ids is not None:
            identifiers = tuple(visible_ids)
            conditions.append(or_(table.c.confluence_id.in_(identifiers), table.c.project_id.in_(identifiers)))
        if query.search.strip():
            needle = f"%{query.search.strip()}%"
            conditions.append(or_(table.c.project_id.ilike(needle), table.c.name.ilike(needle), table.c.customer_summary.ilike(needle)))
        columns = {'project status': 'status_name', 'status': 'status_name', 'current stage': 'stage_name',
                   'stage': 'stage_name', 'support mode': 'support_mode_name', 'product space': 'product_space_key', 'product line': 'product_space_key'}
        for key, values in query.filters:
            column = columns.get(key.casefold())
            if column and values:
                values = tuple(product_line_name(value) if column == 'product_space_key' else value for value in values)
                conditions.append(table.c[column].in_(values))
        with self.database.connect() as connection:
            total = connection.execute(select(func.count()).select_from(table).where(*conditions)).scalar_one()
            rows = connection.execute(select(table).where(*conditions).order_by(table.c.project_id, table.c.product_space_key)
                                      .limit(int(page_size)).offset(int(page) * int(page_size))).fetchall()
            owners = self._owners_by_project(connection, tuple(row[0] for row in rows))
            projects = tuple(self._project_from_row(connection, row, owners.get(row[0], ())) for row in rows)
        return ProjectPage(projects, int(page), int(page_size), total)

    def facts_version(self) -> str:
        with self.database.connect() as connection:
            row = connection.execute(text('SELECT max(cached_at) FROM (\n                SELECT cached_at FROM confluence_projects\n                UNION ALL SELECT cached_at FROM confluence_project_detail_states\n            ) AS freshness')).fetchone()
        return str(row[0] or "")

    def load_many(self, projects: Iterable[Project], details: ProjectDetails) -> tuple[Project, ...]:
        """Load presentation detail sections for a project page in bounded SQL batches."""
        projects = tuple(projects)
        identities = tuple(project.identity.confluence_id for project in projects)
        if not identities or not details.sections():
            return projects
        roles_by_project: dict[str, list[ProjectRole]] = {}
        facts_by_project: dict[str, FieldBag] = {}
        states = {}
        with self.database.connect() as connection:
            for identity, name, state, revision, error, has_value in connection.execute(text("SELECT confluence_id,section_name,state,source_revision,error_code,has_value\n                FROM confluence_project_detail_states\n                WHERE section_name IN ('roles','facts') AND confluence_id IN :identifiers").bindparams(bindparam("identifiers", expanding=True)), {"identifiers": identities}):
                states[(identity, name)] = (DetailState(state), revision, error, bool(has_value))
            if details.roles:
                role_people: dict[tuple[str, str, str], list[PersonRef]] = {}
                for row in connection.execute(text('SELECT r.confluence_id,r.role_id,r.role_name,p.identity,p.account,p.display_name\n                    FROM confluence_project_roles r\n                    LEFT JOIN confluence_project_role_people p\n                    ON p.confluence_id=r.confluence_id AND p.role_id=r.role_id\n                    WHERE r.confluence_id IN :identifiers\n                    ORDER BY r.confluence_id,r.role_id,p.identity').bindparams(bindparam("identifiers", expanding=True)), {"identifiers": identities}):
                    key = (row[0], row[1], row[2])
                    people = role_people.setdefault(key, [])
                    if row[3] is not None:
                        people.append(PersonRef(row[3], row[4], row[5]))
                for (identity, role_id, role_name), people in role_people.items():
                    roles_by_project.setdefault(identity, []).append(
                        ProjectRole(NamedValue(role_id, role_name), tuple(people)),
                    )
            if details.facts:
                values: dict[str, list[tuple[str, object]]] = {}
                for identity, key, value in connection.execute(text("SELECT confluence_id,field_key,value_json FROM confluence_project_fields\n                    WHERE section_name='facts' AND confluence_id IN :identifiers\n                    ORDER BY confluence_id,field_key").bindparams(bindparam("identifiers", expanding=True)), {"identifiers": identities}):
                    values.setdefault(identity, []).append((key, json.loads(value)))
                facts_by_project = {identity: FieldBag(tuple(rows)) for identity, rows in values.items()}
        def section(identity, name, value):
            state = states.get((identity, name))
            if state is None:
                return DetailSection()
            detail_state, revision, error, has_value = state
            if has_value and value is None:
                value = _empty_value(name)
            return DetailSection(detail_state, value if has_value else None, revision, error)
        return tuple(replace(
            project,
            **({"roles": section(project.identity.confluence_id, "roles", tuple(roles_by_project.get(project.identity.confluence_id, ())))}
               if details.roles else {}),
            **({"facts": section(project.identity.confluence_id, "facts", facts_by_project.get(project.identity.confluence_id))}
               if details.facts else {}),
        ) for project in projects)

    def save_core(self, projects: Iterable[Project]) -> None:
        projects = tuple(projects)
        cached_at = _now()
        started = perf_counter()
        smart_log("Confluence catalog database timing", platform="web", domain="framework", source="confluence_repository", emit_runtime_event=False,
                  extra={"stage": "filter.database_write_begin", "duration_ms": 0, "project_count": len(projects)})
        with self.database.transaction() as connection:
            for project in projects:
                old = connection.execute(
                    text("SELECT source_revision FROM confluence_projects WHERE confluence_id=:confluence_id"),
                    {"confluence_id": project.identity.confluence_id},
                ).fetchone()
                upsert(
                    connection,
                    confluence_projects,
                    _project_values(project, cached_at),
                    conflict_columns=("confluence_id",),
                    updates=(
                        "project_id",
                        "name",
                        "product_space_key",
                        "product_space_name",
                        "product_space_url",
                        "catalog_page_id",
                        "catalog_page_title",
                        "catalog_page_url",
                        "catalog_page_version",
                        "status_id",
                        "status_name",
                        "stage_id",
                        "stage_name",
                        "support_mode_id",
                        "support_mode_name",
                        "customer_summary",
                        "source_revision",
                        "cached_at",
                    ),
                )
                connection.execute(
                    text("DELETE FROM confluence_project_owners WHERE confluence_id=:confluence_id"),
                    {"confluence_id": project.identity.confluence_id},
                )
                _batch = [
                    {
                        "confluence_id": project.identity.confluence_id,
                        "identity": person.identity if person else None,
                        "account": person.account if person else None,
                        "display_name": person.display_name if person else None,
                    }
                    for person in dict.fromkeys(project.owner_summary)
                ]
                if _batch:
                    connection.execute(confluence_project_owners.insert(), _batch)
                for section in _SECTIONS:
                    upsert(
                        connection,
                        confluence_project_detail_states,
                        {
                            **{
                                "confluence_id": project.identity.confluence_id,
                                "section_name": section,
                                "cached_at": cached_at,
                            },
                            "state": "unloaded",
                            "source_revision": "",
                            "error_code": "",
                        },
                        conflict_columns=("confluence_id", "section_name"),
                        updates=(),
                    )
                if old is not None and str(old[0]) != project.revision.value:
                    connection.execute(
                        text(
                            "UPDATE confluence_project_detail_states SET state='stale',cached_at=:cached_at\n                        WHERE confluence_id=:confluence_id AND state='loaded'"
                        ),
                        {"cached_at": cached_at, "confluence_id": project.identity.confluence_id},
                    )
        smart_log("Confluence catalog database timing", platform="web", domain="framework", source="confluence_repository", emit_runtime_event=False,
                  extra={"stage": "filter.database_write", "duration_ms": round((perf_counter() - started) * 1000, 3),
                         "project_count": len(projects), "outcome": "success"})

    def replace_roles(self, project_id: str, section: DetailSection[tuple[ProjectRole, ...]]) -> None:
        def write(connection, confluence_id):
            connection.execute(
                text("DELETE FROM confluence_project_roles WHERE confluence_id=:confluence_id"),
                {"confluence_id": confluence_id},
            )
            for role in section.value or ():
                role_id = role.role.id or role.role.name
                connection.execute(
                    confluence_project_roles.insert().values(
                        {"confluence_id": confluence_id, "role_id": role_id, "role_name": role.role.name}
                    )
                )
                _batch = [
                    {
                        "confluence_id": confluence_id,
                        "role_id": role_id,
                        "identity": person.identity if person else None,
                        "account": person.account if person else None,
                        "display_name": person.display_name if person else None,
                    }
                    for person in role.people
                ]
                if _batch:
                    connection.execute(confluence_project_role_people.insert(), _batch)
        self._replace(project_id, "roles", section, write)

    def replace_milestones(self, project_id: str, section: DetailSection[ProjectMilestones]) -> None:
        def write(connection, confluence_id):
            connection.execute(
                text("DELETE FROM confluence_project_milestones WHERE confluence_id=:confluence_id"),
                {"confluence_id": confluence_id},
            )
            _batch = [
                {"confluence_id": confluence_id, "milestone_key": key, "milestone_value": value}
                for (key, value) in (section.value.values if section.value else ())
            ]
            if _batch:
                connection.execute(confluence_project_milestones.insert(), _batch)
            self._rebuild_current_release(connection, confluence_id, section.source_revision)
        self._replace(project_id, "milestones", section, write)

    def replace_hardware(self, project_id: str, section: DetailSection[FieldBag]) -> None:
        self._replace_fields(project_id, "hardware", section)

    def replace_software(self, project_id: str, section: DetailSection[FieldBag]) -> None:
        self._replace_fields(project_id, "software", section)

    def replace_facts(self, project_id: str, section: DetailSection[FieldBag]) -> None:
        self._replace_fields(project_id, "facts", section)

    def merge_catalog_facts(self, project_id: str, section: DetailSection[FieldBag]) -> None:
        current = self.get(project_id, ProjectDetails(facts=True))
        if current is None or current.facts.value is None:
            self.replace_facts(project_id, section)
            return
        values = dict(current.facts.value.values)
        values.update(dict(section.value.values if section.value else ()))
        merged = replace(current.facts, value=FieldBag.from_mapping(values))
        self.replace_facts(project_id, merged)

    def replace_evidence(self, project_id: str, section: DetailSection[tuple[SourceEvidence, ...]]) -> None:
        def write(connection, confluence_id):
            connection.execute(
                text("DELETE FROM confluence_project_evidence WHERE confluence_id=:confluence_id"),
                {"confluence_id": confluence_id},
            )
            _batch = [
                {
                    "confluence_id": confluence_id,
                    "source": item.source,
                    "page_id": item.page.page_id,
                    "page_title": item.page.title,
                    "page_url": item.page.url,
                    "page_version": item.page.version,
                }
                for item in section.value or ()
            ]
            if _batch:
                connection.execute(confluence_project_evidence.insert(), _batch)
        self._replace(project_id, "evidence", section, write)

    def mark_details_stale(self, project_id, sections):
        names = tuple(name for name in sections if name in _SECTIONS)
        if not names:
            return
        with self.database.transaction() as connection:
            project = connection.execute(
                select(confluence_projects.c.confluence_id)
                .where(confluence_projects.c.project_id == project_id).limit(1)
            ).scalar_one_or_none()
            connection.execute(update(confluence_project_detail_states).where(
                confluence_project_detail_states.c.confluence_id == project,
                confluence_project_detail_states.c.section_name.in_(names), confluence_project_detail_states.c.state == 'loaded'
            ).values(state='stale', cached_at=_now()))

    def delete(self, project_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                text("DELETE FROM confluence_projects WHERE project_id=:project_id"), {"project_id": project_id}
            )

    def clear(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(text('DELETE FROM confluence_projects'))
            connection.execute(text('DELETE FROM confluence_sync_state'))

    def _replace_fields(self, project_id: str, name: str, section: DetailSection[FieldBag]) -> None:
        def write(connection, confluence_id):
            connection.execute(
                text(
                    "DELETE FROM confluence_project_fields WHERE confluence_id=:confluence_id AND section_name=:section_name"
                ),
                {"confluence_id": confluence_id, "section_name": name},
            )
            for key, value in section.value.values if section.value is not None else ():
                connection.execute(
                    confluence_project_fields.insert().values(
                        {
                            "confluence_id": confluence_id,
                            "section_name": name,
                            "field_key": key,
                            "value_json": json.dumps(value, ensure_ascii=False),
                        }
                    )
                )
            if name == "facts":
                self._rebuild_current_release(connection, confluence_id, section.source_revision)
        self._replace(project_id, name, section, write)

    @staticmethod
    def _rebuild_current_release(connection, confluence_id: str, source_revision: str) -> None:
        fields = {}
        for key, value_json in connection.execute(text("SELECT field_key,value_json FROM confluence_project_fields\n            WHERE confluence_id=:confluence_id AND section_name='facts'"), {"confluence_id": confluence_id}):
            fields[_normalized_field_name(key)] = _field_display_value(json.loads(value_json))
        for key, value in connection.execute(text('SELECT milestone_key,milestone_value FROM confluence_project_milestones WHERE confluence_id=:confluence_id'), {"confluence_id": confluence_id}):
            fields[_normalized_field_name(key)] = str(value or "")
        project = connection.execute(
            text(
                "SELECT project_id,source_revision FROM confluence_projects WHERE confluence_id=:confluence_id"
            ),
            {"confluence_id": confluence_id},
        ).fetchone()
        if project is None:
            return
        upsert(
            connection,
            project_current_releases,
            {
                "confluence_id": confluence_id,
                "project_id": project[0],
                "release_name": fields.get("launch os", ""),
                "launch_time": fields.get("launch time", ""),
                "mp_time": fields.get("mp time", ""),
                "next_target": fields.get("next target", ""),
                "next_target_date": fields.get("next target date", ""),
                "current_hw_stage": fields.get("current hw stage", ""),
                "status_summary": fields.get("status summary", ""),
                "source_revision": str(source_revision or project[1] or ""),
                "cached_at": _now(),
            },
            conflict_columns=("confluence_id",),
            updates=(
                "project_id",
                "release_name",
                "launch_time",
                "mp_time",
                "next_target",
                "next_target_date",
                "current_hw_stage",
                "status_summary",
                "source_revision",
                "cached_at",
            ),
        )

    def _replace(self, project_id: str, name: str, section: DetailSection, writer: Callable) -> None:
        with self.database.transaction() as connection:
            row = connection.execute(
                text(
                    "SELECT confluence_id FROM confluence_projects\n                WHERE confluence_id=:confluence_id OR project_id=:project_id\n                ORDER BY CASE WHEN confluence_id=:confluence_id_p2 THEN 0 ELSE 1 END, product_space_key\n                LIMIT 1"
                ),
                {"confluence_id": project_id, "project_id": project_id, "confluence_id_p2": project_id},
            ).fetchone()
            if row is None:
                raise KeyError(project_id)
            writer(connection, row[0])
            upsert(
                connection,
                confluence_project_detail_states,
                {
                    "confluence_id": row[0],
                    "section_name": name,
                    "state": section.state.value,
                    "source_revision": section.source_revision,
                    "error_code": section.error_code,
                    "has_value": int(section.value is not None),
                    "cached_at": _now(),
                },
                conflict_columns=("confluence_id", "section_name"),
                updates=("state", "source_revision", "error_code", "has_value", "cached_at"),
            )

    @staticmethod
    def _owners_by_project(connection, identities) -> dict[str, tuple[PersonRef, ...]]:
        if not identities:
            return {}
        grouped: dict[str, list[PersonRef]] = {}
        for confluence_id, identity, account, display_name in connection.execute(text('SELECT confluence_id,identity,account,display_name\n            FROM confluence_project_owners WHERE confluence_id IN :identifiers\n            ORDER BY confluence_id,identity').bindparams(bindparam("identifiers", expanding=True)), {"identifiers": identities}):
            grouped.setdefault(confluence_id, []).append(PersonRef(identity, account, display_name))
        return {identity: tuple(people) for identity, people in grouped.items()}

    def _project_from_row(self, connection, row, owners=None) -> Project:
        if owners is None:
            owners = tuple(
                (
                    PersonRef(item[0], item[1], item[2])
                    for item in connection.execute(
                        text(
                            "SELECT identity,account,display_name FROM confluence_project_owners WHERE confluence_id=:confluence_id ORDER BY identity"
                        ),
                        {"confluence_id": row[0]},
                    )
                )
            )
        return Project(
            ProjectIdentity(row[0], row[1]), row[2], ProductSpaceRef(confluence_space_key(row[3]), confluence_space_key(row[4]), row[5]),
            ConfluencePageRef(row[6], row[7], row[8], int(row[9])),
            named(row[10], row[11]), named(row[12], row[13]), named(row[14], row[15]),
            row[16], owners, SourceRevision(row[17]),
        )

    def _load_section(self, connection, confluence_id: str, name: str) -> DetailSection:
        state = connection.execute(
            text(
                "SELECT state,source_revision,error_code,has_value FROM confluence_project_detail_states\n            WHERE confluence_id=:confluence_id AND section_name=:section_name"
            ),
            {"confluence_id": confluence_id, "section_name": name},
        ).fetchone()
        if state is None:
            return DetailSection()
        detail_state = DetailState(state[0])
        value = self._load_value(connection, confluence_id, name) if bool(state[3]) else None
        if value is None and bool(state[3]):
            value = _empty_value(name)
        return DetailSection(detail_state, value, state[1], state[2])

    @staticmethod
    def _load_value(connection, confluence_id: str, name: str):
        if name == "roles":
            roles = []
            for role_id, role_name in connection.execute(text('SELECT role_id,role_name FROM confluence_project_roles WHERE confluence_id=:confluence_id ORDER BY role_id'), {"confluence_id": confluence_id}):
                people = tuple(
                    (
                        PersonRef(*row)
                        for row in connection.execute(
                            text(
                                "SELECT identity,account,display_name FROM confluence_project_role_people WHERE confluence_id=:confluence_id AND role_id=:role_id ORDER BY identity"
                            ),
                            {"confluence_id": confluence_id, "role_id": role_id},
                        )
                    )
                )
                roles.append(ProjectRole(NamedValue(role_id, role_name), people))
            return tuple(roles) if roles else None
        if name == "milestones":
            rows = tuple(
                connection.execute(
                    text(
                        "SELECT milestone_key,milestone_value FROM confluence_project_milestones WHERE confluence_id=:confluence_id ORDER BY milestone_key"
                    ),
                    {"confluence_id": confluence_id},
                )
            )
            return ProjectMilestones(rows) if rows else None
        if name in {"hardware", "software", "facts"}:
            rows = tuple(
                connection.execute(
                    text(
                        "SELECT field_key,value_json FROM confluence_project_fields WHERE confluence_id=:confluence_id AND section_name=:section_name ORDER BY field_key"
                    ),
                    {"confluence_id": confluence_id, "section_name": name},
                )
            )
            return FieldBag(tuple((key, json.loads(value)) for key, value in rows)) if rows else None
        rows = tuple(
            connection.execute(
                text(
                    "SELECT source,page_id,page_title,page_url,page_version FROM confluence_project_evidence WHERE confluence_id=:confluence_id ORDER BY source,page_id"
                ),
                {"confluence_id": confluence_id},
            )
        )
        return tuple(SourceEvidence(row[0], ConfluencePageRef(row[1], row[2], row[3], int(row[4]))) for row in rows) if rows else None


def _project_values(project: Project, cached_at: str) -> dict:
    return {
        "confluence_id": project.identity.confluence_id,
        "project_id": project.identity.project_id,
        "name": project.name,
        "product_space_key": product_line_name(project.product_space.key),
        "product_space_name": product_line_name(project.product_space.name),
        "product_space_url": project.product_space.url,
        "catalog_page_id": project.catalog_page.page_id,
        "catalog_page_title": project.catalog_page.title,
        "catalog_page_url": project.catalog_page.url,
        "catalog_page_version": project.catalog_page.version,
        "status_id": project.status.id if project.status else None,
        "status_name": project.status.name if project.status else None,
        "stage_id": project.stage.id if project.stage else None,
        "stage_name": project.stage.name if project.stage else None,
        "support_mode_id": project.support_mode.id if project.support_mode else None,
        "support_mode_name": project.support_mode.name if project.support_mode else None,
        "customer_summary": project.customer_summary,
        "source_revision": project.revision.value,
        "cached_at": cached_at,
    }


def named(identity, name) -> NamedValue | None:
    return NamedValue(str(identity or ""), str(name or "")) if identity is not None or name is not None else None


def _empty_value(name: str):
    if name in {"roles", "evidence"}:
        return ()
    if name == "milestones":
        return ProjectMilestones()
    return FieldBag()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_field_name(value) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _field_display_value(value) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return str(value.get("value") or value.get("name") or value.get("displayName") or "")
    if isinstance(value, (list, tuple)):
        return ", ".join(filter(None, (_field_display_value(item) for item in value)))
    return str(value)
