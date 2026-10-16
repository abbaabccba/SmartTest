from __future__ import annotations

from sqlalchemy import select, or_, text, func
from .confluence.schema import confluence_projects, project_current_releases, confluence_project_owners, confluence_project_roles, confluence_project_role_people
from .jira.schema import jira_issue_components, jira_issue_fix_versions


from collections import defaultdict
from datetime import date, datetime
import re
import unicodedata

from core.product_lines import PRODUCT_LINES
from core.confluence.project_rules import MAJOR_QA_ROLE_ID

from .database import WebDatabase


def normalize_release_value(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", " ", text).casefold()


class ProjectReleaseQueryService:
    """Local presentation query for current project releases and Jira issues."""

    def __init__(self, database: WebDatabase, *, today=date.today):
        self.database = database
        self._today = today

    def dashboard(self, *, visible_ids=(), project_ids=(), filters=None) -> dict:
        projects = self._projects(visible_ids, project_ids, filters or {})
        issue_rows = self._issue_rows(tuple(row[1] for row in projects))
        grouped = self._group_issues(projects, issue_rows)
        releases = [self._release_payload(row, grouped.get(row[1], ())) for row in projects]
        return {
            "state": "ready" if releases else "no_snapshot",
            "facets": self._dashboard_facets(releases),
            "summary": {
                "currentReleases": len(releases),
                "block": sum(row["health"]["state"] == "BLOCK" for row in releases),
                "warning": sum(row["health"]["state"] == "WARNING" for row in releases),
                "openP0P1": sum(row["issueCounts"]["p0"] + row["issueCounts"]["p1"] for row in releases),
                "dataIncomplete": sum(row["health"]["dataIncomplete"] for row in releases),
            },
            "releases": releases,
            "sourceFreshness": self._freshness(projects, issue_rows),
        }

    def issues(self, *, visible_ids=(), project_ids=(), filters=None, page=0, page_size=50) -> dict:
        project_scope = {"_scopeRelease": (filters or {}).get("_scopeRelease", ())}
        projects = self._projects(visible_ids, project_ids, project_scope)
        issues = []
        by_project = {normalize_release_value(row[1]): row for row in projects}
        issue_rows = self._issue_rows(tuple(item[1] for item in projects))
        for row in issue_rows:
            project = by_project.get(normalize_release_value(row[19]))
            if project is None:
                continue
            payload = self._issue_payload(row, project[3])
            payload.update({"productLine": project[10], "project": project[1], "projectId": project[1], "release": project[3] or ""})
            if self._issue_filter(payload, filters or {}):
                issues.append(payload)
        issues.sort(key=lambda row: (_priority_rank(row["priority"]), _descending_time_key(row["updatedAt"]), row["key"]))
        total = len(issues)
        start = max(0, int(page)) * max(1, int(page_size))
        selected = [self._public_issue(row) for row in issues[start:start + max(1, int(page_size))]]
        return {
            "state": "ready" if projects else "no_snapshot",
            "selectedRelease": self._selected_release(projects),
            "facets": self._issue_facets(issues),
            "issues": selected,
            "counts": {
                "exact": sum(row["releaseAssociation"] == "exact" for row in issues),
                "versionPending": sum(row["releaseAssociation"] == "version_pending" for row in issues),
            },
            "pagination": {"page": int(page), "pageSize": int(page_size), "total": total},
            "sourceFreshness": self._freshness(projects, issue_rows),
        }

    def jira_cache_version(self) -> str:
        with self.database.connect() as connection:
            row = connection.execute(text('SELECT max(cached_at) FROM jira_issues')).fetchone()
        return str(row[0] or "")

    def issue_detail(self, issue_key: str, *, visible_ids=(), project_ids=(), filters=None) -> dict | None:
        result = self.issues(
            visible_ids=visible_ids, project_ids=project_ids, filters=filters, page_size=100000,
        )
        return next((row for row in result["issues"] if row["key"] == issue_key), None)

    def _projects(self, visible_ids, project_ids, filters):
        visible = tuple(dict.fromkeys(str(value) for value in visible_ids if str(value)))
        if not visible:
            return []
        p, r = confluence_projects.alias('p'), project_current_releases.alias('r')
        owners = confluence_project_owners.alias('o')
        roles, people = confluence_project_roles.alias('rr'), confluence_project_role_people.alias('rp')
        conditions = [or_(p.c.confluence_id.in_(visible), p.c.project_id.in_(visible))]
        selected = tuple(dict.fromkeys(str(value) for value in project_ids if str(value)))
        if selected:
            conditions.append(p.c.project_id.in_(selected))
        for key in ('owner', 'qa'):
            values = tuple(str(value) for value in filters.get(key, ()) if str(value))
            if not values:
                continue
            if key == 'owner':
                conditions.append(select(owners.c.identity).where(owners.c.confluence_id == p.c.confluence_id, owners.c.display_name.in_(values)).exists())
            else:
                conditions.append(select(people.c.identity).where(people.c.confluence_id == p.c.confluence_id,
                    people.c.role_id == MAJOR_QA_ROLE_ID, people.c.display_name.in_(values)).exists())
        columns = {'productLine': p.c.product_space_key, 'stage': p.c.stage_name, 'project': p.c.project_id,
                   'release': r.c.release_name, 'status': p.c.status_name, '_scopeRelease': func.coalesce(r.c.release_name, '')}
        for key, values in filters.items():
            raw_values = values if isinstance(values, (list, tuple)) else (values,)
            values = tuple(str(value) for value in raw_values if key == '_scopeRelease' or str(value))
            if key in columns and values:
                conditions.append(columns[key].in_(values))
        statement = select(p.c.confluence_id,p.c.project_id,p.c.name,r.c.release_name,r.c.launch_time,r.c.mp_time,
            r.c.next_target,r.c.next_target_date,r.c.current_hw_stage,r.c.status_summary,
            p.c.product_space_key,p.c.status_name,p.c.stage_name,p.c.catalog_page_url,
            p.c.source_revision,p.c.cached_at,r.c.source_revision,r.c.cached_at).select_from(
                p.outerjoin(r, r.c.confluence_id == p.c.confluence_id)).where(*conditions).order_by(p.c.project_id,p.c.product_space_key)
        with self.database.connect() as connection:
            rows = connection.execute(statement).fetchall()
            identities = tuple(row[0] for row in rows)
            owner_names, qa_names = defaultdict(list), defaultdict(list)
            if identities:
                for identity, name in connection.execute(select(owners.c.confluence_id, owners.c.display_name)
                        .where(owners.c.confluence_id.in_(identities))):
                    if name is not None:
                        owner_names[identity].append(name)
                for identity, name in connection.execute(select(people.c.confluence_id, people.c.display_name)
                        .select_from(roles.join(people, (people.c.confluence_id == roles.c.confluence_id)
                            & (people.c.role_id == roles.c.role_id)))
                        .where(roles.c.confluence_id.in_(identities), roles.c.role_id == MAJOR_QA_ROLE_ID)):
                    if name is not None:
                        qa_names[identity].append(name)
            return [(*row, ', '.join(owner_names[row[0]]), ', '.join(qa_names[row[0]])) for row in rows]

    def _issue_rows(self, project_ids):
        normalized = {normalize_release_value(value) for value in project_ids if value}
        if not normalized:
            return []
        with self.database.connect() as connection:
            rows = connection.execute(text('SELECT i.issue_id,i.issue_key,i.web_url,i.summary,i.status_name,i.resolution_name,\n                i.priority_name,i.assignee_identity,i.assignee_display_name,i.created_at,i.updated_at,\n                f.software_release,f.severity,f.compare_status,f.qa_assignee_identity,f.manager_identity,\n                f.resolved_at,i.cached_at,i.source_revision,f.project_business_id,i.project_key,i.issue_type_name\n                FROM jira_issues i JOIN jira_issue_release_facts f ON f.issue_id=i.issue_id')).fetchall()
            rows = [row for row in rows if normalize_release_value(row[19]) in normalized]
            if not rows:
                return []
            issue_ids = tuple(row[0] for row in rows)
            components = defaultdict(list)
            for issue_id, name in connection.execute(select(jira_issue_components.c.issue_id, jira_issue_components.c.component_name).where(jira_issue_components.c.issue_id.in_(issue_ids))):
                components[issue_id].append(name)
            fix_versions = defaultdict(list)
            for issue_id, name in connection.execute(select(jira_issue_fix_versions.c.issue_id, jira_issue_fix_versions.c.version_name).where(jira_issue_fix_versions.c.issue_id.in_(issue_ids))):
                fix_versions[issue_id].append(name)
        return [
            (*row, tuple(components[row[0]]), tuple(fix_versions[row[0]]))
            for row in rows
        ]

    @staticmethod
    def _group_issues(projects, issue_rows):
        grouped = {row[1]: [] for row in projects}
        normalized = {normalize_release_value(key): key for key in grouped}
        for issue in issue_rows:
            key = normalized.get(normalize_release_value(issue[19]))
            if key is not None:
                grouped[key].append(issue)
        return grouped

    def _release_payload(self, row, issues):
        release_name = row[3] or ""
        issue_payloads = [self._issue_payload(issue, release_name) for issue in issues]
        open_issues = [item for item in issue_payloads if not item["resolution"]]
        p0 = [item for item in open_issues if normalize_release_value(item["priority"]) == "p0"]
        p1 = [item for item in open_issues if normalize_release_value(item["priority"]) == "p1"]
        pending = [item for item in issue_payloads if item["releaseAssociation"] == "version_pending"]
        reasons = []
        if normalize_release_value(row[11]) == "block": reasons.append("Confluence 项目状态为 BLOCK")
        if p0: reasons.append(f"{len(p0)} 个未解决 P0")
        if normalize_release_value(row[11]) == "warning": reasons.append("Confluence 项目状态为 WARNING")
        if _past(row[4], self._today()): reasons.append("目标发布日期已过期")
        if _past(row[7], self._today()): reasons.append("当前目标日期已过期")
        if p1: reasons.append(f"{len(p1)} 个未解决 P1")
        risky_pending = [item for item in pending if not item["resolution"] and normalize_release_value(item["priority"]) in {"p0", "p1"}]
        if risky_pending: reasons.append(f"{len(risky_pending)} 个 P0/P1 版本待确认")
        incomplete = []
        if not row[1]: incomplete.append("Project ID 缺失")
        if not release_name: incomplete.append("版本名缺失")
        if not row[4]: incomplete.append("目标日期缺失")
        metadata_ready = self._metadata_ready()
        if not metadata_ready: incomplete.append("Jira 必要字段元数据不可用")
        reasons.extend(incomplete)
        if normalize_release_value(row[11]) == "block" or p0:
            state = "BLOCK"
        elif (normalize_release_value(row[11]) == "warning" or _past(row[4], self._today())
              or _past(row[7], self._today()) or p1 or risky_pending):
            state = "WARNING"
        elif incomplete:
            state = "DATA INCOMPLETE"
        else:
            state = "NORMAL"
        return {
            "confluenceId": row[0], "projectId": row[1], "projectName": row[2],
            "releaseName": release_name or "版本未填写", "launchTime": row[4] or "",
            "daysToLaunch": _days(row[4], self._today()), "mpTime": row[5] or "",
            "nextTarget": row[6] or "", "nextTargetDate": row[7] or "",
            "currentHwStage": row[8] or "", "statusSummary": row[9] or "",
            "productLine": row[10] or "", "projectStatus": row[11] or "",
            "currentStage": row[12] or "", "confluenceUrl": row[13] or "",
            "projectOwners": row[18] or "", "majorFaeQa": row[19] or "",
            "issueCounts": {"open": len(open_issues), "p0": len(p0), "p1": len(p1),
                            "exact": len(issue_payloads) - len(pending), "versionPending": len(pending)},
            "health": {"state": state, "reasons": reasons, "dataIncomplete": bool(incomplete)},
            "cachedAt": max(str(row[15] or ""), str(row[17] or "")),
        }

    @staticmethod
    def _issue_payload(row, release_name):
        candidates = [row[11], *row[23]]
        exact = bool(release_name) and any(
            normalize_release_value(item) == normalize_release_value(release_name)
            for item in candidates if item
        )
        return {
            "id": row[0], "key": row[1], "webUrl": row[2], "summary": row[3],
            "status": row[4], "resolution": row[5] or "", "priority": row[6] or "",
            "assignee": row[8] or row[7] or "", "createdAt": row[9] or "", "updatedAt": row[10] or "",
            "softwareRelease": row[11] or "", "severity": row[12] or "",
            "compareStatus": row[13] or "", "qaAssignee": row[14] or "", "manager": row[15] or "",
            "resolvedAt": row[16] or "", "projectId": row[19] or "",
            "jiraProject": row[20] or "", "issueType": row[21] or "",
            "components": ", ".join(row[22]), "fixVersions": ", ".join(row[23]),
            "_componentValues": row[22], "_fixVersionValues": row[23],
            "releaseAssociation": "exact" if exact else "version_pending",
            "associationReason": "版本字段与当前交付版本一致" if exact else "Project ID 一致，但版本字段为空或不匹配",
            "sourceRevision": row[18] or "", "cachedAt": row[17] or "",
        }

    def _metadata_ready(self):
        with self.database.connect() as connection:
            names = {row[0].casefold() for row in connection.execute(text('SELECT field_name FROM jira_release_field_metadata'))}
        required = {"project id", "software release", "severity", "compare status", "qa assignee", "manager"}
        return required <= names

    @staticmethod
    def _issue_filter(issue, filters):
        mapping = {
            "fixVersion": "fixVersions", "softwareRelease": "softwareRelease",
            "status": "status", "resolution": "resolution", "priority": "priority",
            "severity": "severity", "component": "components", "assignee": "assignee",
            "qaAssignee": "qaAssignee", "association": "releaseAssociation",
            "project": "jiraProject", "type": "issueType", "currentUser": "assignee",
        }
        if filters.get("_openOnly") and issue["resolution"]:
            return False
        for key, target in mapping.items():
            selected = filters.get(key)
            selected = selected if isinstance(selected, (list, tuple)) else (selected,) if selected else ()
            wanted = {normalize_release_value(value) for value in selected}
            if key == "component":
                actual = {normalize_release_value(value) for value in issue["_componentValues"]}
            elif key == "fixVersion":
                actual = {normalize_release_value(value) for value in issue["_fixVersionValues"]}
            else:
                actual = {normalize_release_value(issue[target])}
            if wanted and not wanted.intersection(actual):
                return False
        return True

    @staticmethod
    def _public_issue(issue):
        return {key: value for key, value in issue.items() if not key.startswith("_")}

    @staticmethod
    def _dashboard_facets(rows):
        def facet(key, label):
            values = sorted({str(row.get(key) or "") for row in rows if row.get(key)}, key=str.casefold)
            return {"key": key, "label": label, "options": [{"value": value, "label": value} for value in values]}
        product_options = [{"value": line.name, "label": line.name} for line in PRODUCT_LINES]
        return [{"key": "productLine", "label": "Product Line", "options": product_options},
                facet("currentStage", "Current Stage"), facet("projectId", "Project"),
                facet("releaseName", "Current Release"), facet("projectOwners", "Project Owner"),
                facet("majorFaeQa", "Major FAE QA"), facet("projectStatus", "Project Status")]

    @staticmethod
    def _issue_facets(rows):
        keys = {
            "productLine": "productLine", "project": "project", "release": "release",
            "fixVersion": "fixVersions", "softwareRelease": "softwareRelease", "status": "status",
            "resolution": "resolution", "priority": "priority", "severity": "severity",
            "component": "components", "assignee": "assignee", "qaAssignee": "qaAssignee",
            "association": "releaseAssociation",
        }
        facets = []
        for key, target in keys.items():
            if key == "component":
                values = {value for row in rows for value in row["_componentValues"] if value}
            elif key == "fixVersion":
                values = {value for row in rows for value in row["_fixVersionValues"] if value}
            else:
                values = {row[target] for row in rows if row[target]}
            facets.append({"key": key, "options": sorted(values, key=str.casefold)})
        return facets

    @staticmethod
    def _selected_release(projects):
        if len(projects) != 1:
            return None
        row = projects[0]
        return {"projectId": row[1], "projectName": row[2], "releaseName": row[3] or "版本未填写"}

    @staticmethod
    def _freshness(projects, issues):
        confluence = max((str(row[15] or "") for row in projects), default="")
        jira = max((str(row[17] or "") for row in issues), default="")
        return {"confluence": confluence, "jira": jira}


def _past(value, today):
    parsed = _date(value)
    return parsed is not None and parsed < today


def _days(value, today):
    parsed = _date(value)
    return (parsed - today).days if parsed is not None else None


def _date(value):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _priority_rank(value):
    normalized = normalize_release_value(value)
    if normalized == "p0": return 0
    if normalized == "p1": return 1
    if normalized == "p2": return 2
    if normalized == "p3": return 3
    return 10


def _descending_time_key(value):
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        timestamp = 0
    return -timestamp
