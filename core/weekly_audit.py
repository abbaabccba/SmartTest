from __future__ import annotations

from datetime import datetime, time, timedelta
import re
from zoneinfo import ZoneInfo
from core.product_lines import PRODUCT_LINES
from core.confluence.audit.product_line_rules import PRODUCT_LINE_AUDIT_RULES


_SHANGHAI = ZoneInfo("Asia/Shanghai")
_JIRA_SCOPE = "project in ({},RK) AND issuetype in (Bug, Sub-bug)".format(
    ", ".join(project for line in PRODUCT_LINES for project in line.jira_project_keys),
)


def fixed_weekly_audit_scope(trigger_at: datetime) -> dict:
    """Return the fixed weekly-email scope frozen for one actual trigger."""
    trigger = (trigger_at.replace(tzinfo=_SHANGHAI) if trigger_at.tzinfo is None
               else trigger_at.astimezone(_SHANGHAI))
    this_monday = trigger.date() - timedelta(days=trigger.weekday())
    end = datetime.combine(this_monday + timedelta(days=4), time(18), _SHANGHAI)
    start = end - timedelta(days=7)
    start_text, end_text = start.isoformat(), end.isoformat()
    jira_start = start.strftime("%Y-%m-%d %H:%M")
    jira_end = end.strftime("%Y-%m-%d %H:%M")
    return {
        "startDate": start_text,
        "endDate": end_text,
        "previousPeriod": {
            "startDate": (start - timedelta(days=7)).isoformat(),
            "endDate": (end - timedelta(days=7)).isoformat(),
        },
        "jira": {
            "filters": {},
            "jql": f'{_JIRA_SCOPE} AND created >= "{jira_start}" AND created < "{jira_end}" order by updated DESC',
        },
        "confluence": {
            "manualProjectUrls": [url for rules in PRODUCT_LINE_AUDIT_RULES.values()
                                  for url in rules.get('manual_project_urls', ())],
            "filters": {
                "product space": [line.confluence_space_key for line in PRODUCT_LINES
                                  if not PRODUCT_LINE_AUDIT_RULES.get(line.name, {}).get('manual_project_urls')],
                "date of commercial approval": [str(trigger.year - 1), str(trigger.year)],
                "support mode": ["A", "B"],
                "project status": ["NORMAL"],
            },
            "search": "",
            "excludeCurrentStageAtOrAbove": 4,
            "excludeSupportModeBProductLines": [line.confluence_space_key for line in PRODUCT_LINES
                                                if PRODUCT_LINE_AUDIT_RULES.get(line.name, {}).get('exclude_support_mode_b')],
        },
    }


def weekly_audit_project_in_scope(project, commercial_years, stage_limit, support_mode_b_exclusions=()) -> bool:
    facts = dict(project.facts.value.values) if project.facts.value is not None else {}
    year = re.search(r"\b(20\d{2})\b", str(facts.get("date of commercial approval", "")))
    stage = re.match(r"^\s*(\d+)", project.stage.name if project.stage else "")
    return (
        (not commercial_years or bool(year and year.group(1) in commercial_years))
        and (stage_limit is None or not stage or int(stage.group(1)) < int(stage_limit))
        and not (
            project.product_space.key in support_mode_b_exclusions
            and project.support_mode is not None
            and project.support_mode.name.strip().casefold() == "b"
        )
    )
