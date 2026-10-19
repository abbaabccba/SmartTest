"""Jira page business predicates; syntax composition stays in filter_service."""
from core.product_lines import (
    CHINA_OPERATOR_BUSINESS, SMART_DEVICE_BUSINESS, TV_BUSINESS,
    GLOBAL_OPERATOR_STB_BUSINESS, DASHBOARD_PRODUCT_LINES, PRODUCT_LINE_BY_JIRA_PROJECT, WIRELESS_CONNECTION,
)


CUSTOMER_WIRELESS_LABELS = (
    "Customer_W1", "Customer_W1U", "Customer_W2L", "customer_w2",
    "Customer_w1u", "customer-w2", "customer-w2L", "customer_w1d",
)
CUSTOMER_WIRELESS_EXCLUDED_RESOLUTIONS = (
    "Invalid Case", "Cannot Reproduce", "HW Fix", "Won't Fix", "Won't Do",
)
CUSTOMER_WIRELESS_EXCLUDED_PROJECTS = ("RD SW Platform", "Wireless Project")


def jql_string(value):
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"')


def _quoted(values):
    return ", ".join(f'"{jql_string(value)}"' for value in values)


def creator_group_jql(groups):
    return " OR ".join(f'creator IN membersOf("{group}")' for group in groups)


# Keep the established Wireless-first attribution order; scopes are the one query authority.
JIRA_PRODUCT_LINE_SCOPES = (
    ("fae-wifi-qa", WIRELESS_CONNECTION, ("IPTV", "TV", "SH", "OTT")),
    ("fae-SH-qa", SMART_DEVICE_BUSINESS, SMART_DEVICE_BUSINESS.jira_project_keys),
    ("fae-stb-qa", GLOBAL_OPERATOR_STB_BUSINESS, GLOBAL_OPERATOR_STB_BUSINESS.jira_project_keys),
    ("fae-tv-qa", TV_BUSINESS, TV_BUSINESS.jira_project_keys),
    ("fae-iptv-qa", CHINA_OPERATOR_BUSINESS, CHINA_OPERATOR_BUSINESS.jira_project_keys),
)
FAE_QA_GROUP_PRODUCT_LINES = tuple((group, line) for group, line, _projects in JIRA_PRODUCT_LINE_SCOPES)
FAE_QA_GROUPS = tuple(group for group, _line in FAE_QA_GROUP_PRODUCT_LINES)


def product_line_jql(line, *, card_key=""):
    group, _line, projects = next(scope for scope in JIRA_PRODUCT_LINE_SCOPES if scope[1] is line)
    project = f'project IN ({_quoted(projects)})'
    return project if card_key == "customer-feedback" else f'{creator_group_jql((group,))} AND {project}'


def creator_product_line(project_key, assignments, *, card_key="", labels=(), resolution="", project_name=""):
    for _group, line, projects in JIRA_PRODUCT_LINE_SCOPES:
        if project_key not in projects or (card_key != "customer-feedback" and line.name not in assignments):
            continue
        if card_key == "customer-feedback" and line is WIRELESS_CONNECTION:
            if (not set(labels).intersection(CUSTOMER_WIRELESS_LABELS)
                    or resolution in CUSTOMER_WIRELESS_EXCLUDED_RESOLUTIONS
                    or project_key in CUSTOMER_WIRELESS_EXCLUDED_PROJECTS
                    or project_name in CUSTOMER_WIRELESS_EXCLUDED_PROJECTS):
                continue
        return line.name
    return ""


def customer_feedback_jql(product_line=None):
    source = 'issuetype = Bug AND "Channel of Reporter" = "Customer-Feedback"'
    if product_line is not None and product_line is not WIRELESS_CONNECTION:
        return source
    wireless = (
        f"labels IN ({_quoted(CUSTOMER_WIRELESS_LABELS)}) AND (resolution is EMPTY OR resolution NOT IN "
        f"({_quoted(CUSTOMER_WIRELESS_EXCLUDED_RESOLUTIONS)})) AND project NOT IN "
        f"({_quoted(CUSTOMER_WIRELESS_EXCLUDED_PROJECTS)})"
    )
    if product_line is WIRELESS_CONNECTION:
        return f'{source} AND ({wireless})'
    return f'{source} AND (({wireless}) OR (project IN ({_quoted(PRODUCT_LINE_BY_JIRA_PROJECT)})))'


def card_product_jql(card_key, source, product_line=None):
    lines = (product_line,) if product_line is not None else DASHBOARD_PRODUCT_LINES
    branches = []
    for line in lines:
        business = customer_feedback_jql(line) if card_key == "customer-feedback" else source
        branches.append(f'({product_line_jql(line, card_key=card_key)}) AND ({business})')
    return ' OR '.join(f'({branch})' for branch in branches)


CARD_BUSINESS_JQL = {
    "self-test": 'issuetype = Bug AND "Channel of Reporter" = "Self-Test"',
    "task": "issuetype = Task",
    "customer-feedback": customer_feedback_jql(),
}


def jira_filter_jql(filters, jql=""):
    def quoted(value):
        return '"' + jql_string(value) + '"'

    def clause(field, values):
        terms = [f"{field} = {quoted(value)}" for value in values]
        return terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")"

    clauses = []
    for key, field in (("project", "project"), ("type", "issuetype"), ("status", "status")):
        if filters.get(key): clauses.append(clause(field, filters[key]))
    if filters.get("currentUser"): clauses.append("assignee = currentUser()")
    resolutions = filters.get("resolution", ())
    if resolutions:
        terms = ["resolution IS EMPTY" if value == "Unresolved" else f"resolution = {quoted(value)}"
                 for value in resolutions]
        clauses.append(terms[0] if len(terms) == 1 else "(" + " OR ".join(terms) + ")")
    if str(jql).strip(): clauses.append(f"({str(jql).strip()})")
    return " AND ".join(clauses)
