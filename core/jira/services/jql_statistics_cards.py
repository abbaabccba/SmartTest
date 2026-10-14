from __future__ import annotations

from collections import defaultdict
from datetime import date
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable
from core.config.jsonTool import read_json

from core.jira.mapper import JiraIssueMapper
from core.jira.jql import CARD_BUSINESS_JQL, creator_group_jql
from core.jira.services.filter_service import compose_jql, jira_period_condition, jira_period_ranges
from core.product_lines import (
    CHINA_OPERATOR_BUSINESS,
    DASHBOARD_PRODUCT_LINES,
    GLOBAL_OPERATOR_STB_BUSINESS,
    PRODUCT_LINE_BY_JIRA_PROJECT,
    SMART_DEVICE_BUSINESS,
    TV_BUSINESS,
    WIRELESS_CONNECTION,
)


FAE_QA_GROUP_PRODUCT_LINES = (
    ("fae-wifi-qa", WIRELESS_CONNECTION),
    ("fae-SH-qa", SMART_DEVICE_BUSINESS),
    ("fae-stb-qa", GLOBAL_OPERATOR_STB_BUSINESS),
    ("fae-tv-qa", TV_BUSINESS),
    ("fae-iptv-qa", CHINA_OPERATOR_BUSINESS),
)
FAE_QA_GROUPS = tuple(group for group, _line in FAE_QA_GROUP_PRODUCT_LINES)


@dataclass(frozen=True)
class JiraStatisticsCardDefinition:
    card_key: str
    title: str
    first_metric_label: str
    custom_jql: str

    def fixed_jql(self, period: str) -> str:
        return f"({creator_group_jql(FAE_QA_GROUPS)}) AND ({self.custom_jql}) AND {jira_period_condition(period)}"

    @property
    def fingerprint(self) -> str:
        encoded = json.dumps({"definition": asdict(self), "groups": FAE_QA_GROUPS,
                              "aggregation": "qa-creators-components-summary-v5" if self.card_key == "customer-feedback"
                              else "qa-creators-basic-v1"}, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


JIRA_STATISTICS_CARDS = {
    definition.card_key: definition for definition in (
        JiraStatisticsCardDefinition(
            "self-test", "Product Lines Self Test Jiras Statistics", "Bugs",
            CARD_BUSINESS_JQL["self-test"],
        ),
        JiraStatisticsCardDefinition(
            "task", "Product Lines Task Jiras Statistics", "Tasks", CARD_BUSINESS_JQL["task"],
        ),
        JiraStatisticsCardDefinition(
            "customer-feedback", "Product Lines Customer Feedback Jiras Statistics", "Component/s", CARD_BUSINESS_JQL["customer-feedback"],
        ),
    )
}


def card_definition(card_key: str) -> JiraStatisticsCardDefinition:
    return JIRA_STATISTICS_CARDS[card_key]


def effective_card_jql_pair(card_key: str, user_jql: str, period: str, today=None) -> dict[str, dict[str, Any]]:
    definition = card_definition(card_key)
    ranges = jira_period_ranges(period, today)
    result = {}
    for key, value in ranges.items():
        fixed = definition.fixed_jql(period)
        fixed = fixed.replace(jira_period_condition(period), str(value["condition"]))
        result[key] = {**value, "jql": compose_jql(user_jql, fixed)}
    return result


def activity_card_jql_pair(card_key, user_jql, today=None):
    return {key: {**value, "jql": compose_jql(user_jql, compose_jql(value["condition"], card_definition(card_key).custom_jql))}
            for key, value in jira_period_ranges("year", today).items()}


@dataclass(frozen=True)
class QARoster:
    fingerprint: str
    assignments: tuple[tuple[str, tuple[str, ...]], ...] = ()


def statistics_accounts(issues) -> tuple[str, ...]:
    accounts = set()
    for issue in issues:
        fields = issue.get("fields") if isinstance(issue, dict) else None
        creator = fields.get("creator") if isinstance(fields, dict) else None
        if isinstance(creator, dict):
            account = str(creator.get("name") or creator.get("accountId") or creator.get("key") or "").strip().casefold()
            if account:
                accounts.add(account)
        if isinstance(fields, dict):
            for comment in _comments(fields):
                if comment.author:
                    account = (comment.author.account or comment.author.identity).strip().casefold()
                    if account:
                        accounts.add(account)
    return tuple(sorted(accounts))


def load_fae_qa_roster(gateway, accounts, *, groups_cache=None) -> QARoster:
    groups_cache = {} if groups_cache is None else groups_cache
    by_account: dict[str, set[str]] = defaultdict(set)
    product_line_by_group = dict(FAE_QA_GROUP_PRODUCT_LINES)
    for account in sorted({str(value or "").strip().casefold() for value in accounts if str(value or "").strip()}):
        if account not in groups_cache:
            groups_cache[account] = tuple(gateway.user_groups(account))
        by_account[account].update(
            product_line_by_group[group].name
            for group in groups_cache[account] if group in product_line_by_group
        )
    assignments = tuple((account, tuple(sorted(lines))) for account, lines in sorted(by_account.items()))
    fingerprint = hashlib.sha256(json.dumps(assignments, separators=(",", ":")).encode()).hexdigest()
    return QARoster(fingerprint, assignments)


@dataclass(frozen=True)
class JiraStatisticsPerson:
    identity: str
    displayName: str
    bugCount: int
    commentCount: int
    verifyCount: int
    invalidCount: int


@dataclass(frozen=True)
class JiraStatisticsProductLine:
    id: str
    label: str
    people: tuple[JiraStatisticsPerson, ...]
    components: tuple[dict[str, Any], ...] = ()
    issueCount: int = 0
    invalidCount: int = 0


@dataclass(frozen=True)
class JiraStatisticsOverview:
    teamTotal: int
    productLines: tuple[JiraStatisticsProductLine, ...]
    unassignedCount: int = 0
    unmappedCount: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "teamTotal": self.teamTotal,
            "unassignedCount": self.unassignedCount,
            "unmappedCount": self.unmappedCount,
            "productLines": [{
                "id": line.id,
                "label": line.label,
                "people": [asdict(person) for person in line.people],
                "components": list(line.components),
                "issueCount": line.issueCount,
                "invalidCount": line.invalidCount,
            } for line in self.productLines],
        }


def _name(value: Any) -> str:
    return str(value.get("name") or "") if isinstance(value, dict) else ""


def _comments(fields):
    return tuple(JiraIssueMapper.from_comment(item) for item in (fields.get("comment") or {}).get("comments", ()))


def _product_line(fields: dict[str, Any], assignments: set[str]):
    if WIRELESS_CONNECTION.name in assignments:
        return WIRELESS_CONNECTION.name
    project = fields.get("project")
    project_key = str(project.get("key") or "") if isinstance(project, dict) else ""
    line = PRODUCT_LINE_BY_JIRA_PROJECT.get(project_key)
    return line.name if line else ""


def aggregate_jira_statistics(
    issues: Iterable[dict[str, Any]], roster: QARoster,
) -> JiraStatisticsOverview:
    assignments = {account: set(lines) for account, lines in roster.assignments}
    people: dict[str, dict[str, dict[str, Any]]] = defaultdict(lambda: defaultdict(lambda: {
        "identity": "", "displayName": "", "bugCount": 0,
        "commentCount": 0, "verifyCount": 0, "invalidCount": 0,
    }))
    total = unassigned = unmapped = 0
    components = defaultdict(lambda: defaultdict(int))
    issue_counts = defaultdict(int)
    invalid_counts = defaultdict(int)
    seen = set()
    for issue in issues:
        issue_id = issue.get("id") or issue.get("key")
        if issue_id:
            if issue_id in seen:
                continue
            seen.add(issue_id)
        fields = issue.get("fields") if isinstance(issue, dict) else None
        total += 1
        creator = fields.get("creator") if isinstance(fields, dict) else None
        if not isinstance(fields, dict) or not isinstance(creator, dict):
            unassigned += 1
            continue
        identity = str(creator.get("name") or creator.get("accountId") or creator.get("key") or "").strip().casefold()
        if not identity:
            unassigned += 1
            continue
        if not assignments.get(identity):
            total -= 1
            continue
        comment_line = _product_line(fields, set())
        if comment_line:
            for comment in _comments(fields):
                author = comment.author
                if not author:
                    continue
                account = (author.account or author.identity).strip().casefold()
                if not assignments.get(account):
                    continue
                comment_row = people[comment_line][account]
                comment_row["identity"] = account
                comment_row["displayName"] = author.display_name or account
                comment_row["commentCount"] += 1
        product_line = _product_line(fields, assignments[identity])
        if not product_line:
            unmapped += 1
            continue
        issue_counts[product_line] += 1
        invalid_counts[product_line] += _name(fields.get("resolution")) == "Invalid"
        for name in {_name(component) for component in fields.get("components") or ()} - {""}:
            components[product_line][name] += 1
        row = people[product_line][identity]
        row["identity"] = identity
        row["displayName"] = str(creator.get("displayName") or identity)
        row["bugCount"] += 1
        row["invalidCount"] += _name(fields.get("resolution")) == "Invalid"

    product_lines = []
    for line in DASHBOARD_PRODUCT_LINES:
        rows = [JiraStatisticsPerson(**row) for row in people[line.name].values()]
        rows.sort(key=lambda row: (-row.bugCount, row.displayName.casefold(), row.identity))
        product_lines.append(JiraStatisticsProductLine(
            line.name, line.name, tuple(rows),
            tuple({"name": name, "count": count} for name, count in sorted(components[line.name].items())),
            issue_counts[line.name], invalid_counts[line.name],
        ))
    return JiraStatisticsOverview(total, tuple(product_lines), unassigned, unmapped)


def build_comparison_statistics(gateway, current_rows, previous_rows, ranges, *, groups_cache=None) -> dict[str, Any]:
    all_rows = [*current_rows, *previous_rows]
    roster = load_fae_qa_roster(gateway, statistics_accounts(all_rows), groups_cache=groups_cache)
    clean_ranges = {
        key: {name: value for name, value in period.items() if name in {"start", "end"}}
        for key, period in ranges.items()
    }
    current = aggregate_jira_statistics(current_rows, roster).to_payload()
    previous = aggregate_jira_statistics(previous_rows, roster).to_payload()
    return {
        **current,
        "ranges": clean_ranges,
        "current": current,
        "previous": previous,
    }


def load_activity_qa_roster() -> QARoster:
    personnel = read_json(Path(__file__).resolve().parents[2] / "config" / "personnel.json")
    employees = personnel["amlogic"]["departments"]["FAE-QA"]["employees"]
    assignments = tuple(sorted(
        (str(person["account"]).strip().casefold(), tuple(item["product_line_id"] for item in person["assignments"]))
        for person in employees if str(person.get("account") or "").strip()
    ))
    return QARoster(hashlib.sha256(json.dumps(assignments, separators=(",", ":")).encode()).hexdigest(), assignments)


def build_annual_facts(gateway, current_rows, previous_rows, *, groups_cache=None,
                       current_activities=None, previous_activities=None, activity_roster=None, activity_facts=None):
    rows = [*current_rows, *previous_rows]
    if current_activities is not None or activity_facts is not None:
        rows = [{**{key: value for key, value in row.items() if key != "changelog"},
                 "fields": {key: value for key, value in row.get("fields", {}).items() if key != "comment"}}
                for row in rows]
    roster = load_fae_qa_roster(gateway, statistics_accounts(rows), groups_cache=groups_cache)
    facts = []
    for issue in rows:
        statistics = aggregate_jira_statistics([issue], roster).to_payload()
        statistics["productLines"] = [line for line in statistics["productLines"] if line["people"]]
        fields = issue.get("fields", {})
        creator = fields.get("creator") or {}
        facts.append({"issueId": str(issue["id"]), "createdAt": str(fields.get("created") or ""),
                      "creator": {key: creator[key] for key in ("name", "accountId", "key", "displayName") if key in creator},
                      "projectKey": (fields.get("project") or {}).get("key", ""), "statistics": statistics})
    if current_activities is not None:
        activity_roster = activity_roster or load_activity_qa_roster()
        assignments = dict(activity_roster.assignments)
        by_id = {fact["issueId"]: fact for fact in facts}
        for issue in [*current_activities, *(previous_activities or [])]:
            identity = str(issue["id"])
            fields = issue.get("fields") or {}
            if identity not in by_id:
                fact = {"issueId": identity, "createdAt": str(fields.get("created") or ""),
                        "statistics": {"teamTotal": 0, "unassignedCount": 0, "unmappedCount": 0, "productLines": []}}
                by_id[identity] = fact
                facts.append(fact)
            statistics = by_id[identity]["statistics"]
            lines = {line["id"]: line for line in statistics["productLines"]}
            activities = [(comment.get("author") or {}, "commentCount")
                          for comment in (fields.get("comment") or {}).get("comments", [])]
            verification = JiraIssueMapper.last_verification(issue)
            if verification and verification.author:
                activities.append(({"name": verification.author.account, "displayName": verification.author.display_name}, "verifyCount"))
            for author, metric in activities:
                account = str(author.get("name") or "").strip().casefold()
                if account not in assignments:
                    continue
                if not assignments[account]:
                    unassigned_key = "unassignedCommentCount" if metric == "commentCount" else "unassignedVerifyCount"
                    statistics[unassigned_key] = statistics.get(unassigned_key, 0) + 1
                for name in assignments[account]:
                    if name not in lines:
                        line = {"id": name, "label": name, "people": []}
                        lines[name] = line
                        statistics["productLines"].append(line)
                    people = lines[name]["people"]
                    person = next((person for person in people if person["identity"] == account), None)
                    if person is None:
                        person = {"identity": account, "displayName": str(author.get("displayName") or account),
                                  "bugCount": 0, "commentCount": 0, "verifyCount": 0, "invalidCount": 0}
                        people.append(person)
                    person[metric] += 1
    if activity_facts is not None:
        merge_annual_activity_facts(facts, activity_facts)
    return {"_facts": facts, "roster": asdict(roster), "annual": True,
            **({"activityRoster": activity_roster.fingerprint} if current_activities is not None or activity_facts is not None else {})}


def merge_annual_activity_facts(facts, activity_facts):
    by_id = {fact["issueId"]: fact for fact in facts}
    for contribution in activity_facts:
        target = by_id.get(contribution["issueId"])
        if target is None:
            facts.append(contribution)
            by_id[contribution["issueId"]] = contribution
            continue
        statistics = target["statistics"]
        statistics["unassignedCommentCount"] = contribution["statistics"].get("unassignedCommentCount", 0)
        statistics["unassignedVerifyCount"] = contribution["statistics"].get("unassignedVerifyCount", 0)
        lines = {line["id"]: line for line in statistics["productLines"]}
        for line in contribution["statistics"]["productLines"]:
            if line["id"] not in lines:
                statistics["productLines"].append(line)
                continue
            people = lines[line["id"]]["people"]
            existing = {person["identity"]: person for person in people}
            for person in line["people"]:
                if person["identity"] in existing:
                    existing[person["identity"]]["commentCount"] = person["commentCount"]
                    existing[person["identity"]]["verifyCount"] = person["verifyCount"]
                else:
                    people.append(person)


def aggregate_annual_facts(facts, metadata, period, today=None):
    today = today or date.today()
    ranges = jira_period_ranges(period, today)
    output = {}
    availability = {}
    for key, window in ranges.items():
        start, end = window["start"], window["end"]
        years = (["previous"] if start < f"{today.year}-01-01" else [])
        if end is None or end > f"{today.year}-01-01":
            years.append("current")
        availability[key] = {metric: all(metadata["availability"][year][metric] for year in years)
                             for metric in (("basic", "activity") if "activityRoster" in metadata else ("basic", "verify"))}
        summary = aggregate_jira_statistics([], QARoster("", ())).to_payload()
        if "activityRoster" in metadata:
            summary["unassignedCommentCount"] = 0
            summary["unassignedVerifyCount"] = 0
        by_line = {line["id"]: {} for line in summary["productLines"]}
        summaries = {line["id"]: line for line in summary["productLines"]}
        components = defaultdict(lambda: defaultdict(int))
        for fact in facts:
            created = fact["createdAt"][:10]
            if not created or created < start or (end is not None and created >= end):
                continue
            contribution = fact["statistics"]
            if "activityRoster" in metadata:
                summary["unassignedCommentCount"] += contribution.get("unassignedCommentCount", 0)
                summary["unassignedVerifyCount"] += contribution.get("unassignedVerifyCount", 0)
            for count in ("teamTotal", "unassignedCount", "unmappedCount"):
                summary[count] += contribution[count]
            for line in contribution["productLines"]:
                for count in ("issueCount", "invalidCount"):
                    if count in line:
                        summaries[line["id"]][count] += line[count]
                for component in line.get("components", ()):
                    components[line["id"]][component["name"]] += component["count"]
                for person in line["people"]:
                    target = by_line[line["id"]].setdefault(person["identity"], {
                        "identity": person["identity"], "displayName": person["displayName"],
                        "bugCount": 0, "commentCount": 0, "verifyCount": 0, "invalidCount": 0})
                    for metric in ("bugCount", "commentCount", "verifyCount", "invalidCount"):
                        target[metric] += person[metric]
        for line in summary["productLines"]:
            line["components"] = [{"name": name, "count": count}
                                  for name, count in sorted(components[line["id"]].items())]
            line["people"] = sorted(by_line[line["id"]].values(),
                                    key=lambda person: (-person["bugCount"], person["displayName"].casefold(), person["identity"]))
            if not availability[key]["activity" if "activityRoster" in metadata else "verify"]:
                for person in line["people"]:
                    person.pop("verifyCount")
        output[key] = summary
    return {**output["current"], **output, "availability": availability,
            "ranges": {key: {name: value for name, value in window.items() if name != "condition"}
                       for key, window in ranges.items()}}
