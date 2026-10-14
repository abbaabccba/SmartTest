"""Jira page business predicates; syntax composition stays in filter_service."""
from core.product_lines import PRODUCT_LINE_BY_JIRA_PROJECT


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


def _customer_jql():
    labels = _quoted(CUSTOMER_WIRELESS_LABELS)
    wireless = (
        f"labels IN ({labels}) AND (resolution is EMPTY OR resolution NOT IN "
        f"({_quoted(CUSTOMER_WIRELESS_EXCLUDED_RESOLUTIONS)})) AND project NOT IN "
        f"({_quoted(CUSTOMER_WIRELESS_EXCLUDED_PROJECTS)})"
    )
    standard = f"project IN ({_quoted(PRODUCT_LINE_BY_JIRA_PROJECT)}) AND (labels is EMPTY OR labels NOT IN ({labels}))"
    return f'issuetype = Bug AND "Channel of Reporter" = "Customer-Feedback" AND (({wireless}) OR ({standard}))'


CARD_BUSINESS_JQL = {
    "self-test": 'issuetype = Bug AND "Channel of Reporter" = "Self-Test"',
    "task": "issuetype = Task",
    "customer-feedback": _customer_jql(),
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
