from __future__ import annotations

import json
import re
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any, NamedTuple

from core.jira.domain import Issue, RichText

from .models import (
    AuditRule,
    AuditViolation,
    IssueAuditResult,
)

_PERSONNEL_PATH = Path(__file__).resolve().parents[2] / "config" / "personnel.json"


@cache
def creator_names() -> frozenset[str]:
    personnel = json.loads(_PERSONNEL_PATH.read_text(encoding="utf-8"))
    employees = personnel["amlogic"]["departments"]["FAE-QA"]["employees"]
    return frozenset(
        str(employee.get("display_name") or "").strip()
        for employee in employees
        if employee.get("active") is not False
        and str(employee.get("display_name") or "").strip()
    )

_RULE_DATA = (
    ("SUMMARY.FORMAT", "Summary", "Summary", "Summary 必须包含 4–6 个方括号分组，最后四组依次为客户、CHIP、系统版本和模块，冒号后填写非空问题描述。", "使用“[客户][CHIP][版本][模块]: 问题描述”；前面可选增加公共 Jira ID 和客户 Bug ID。"),
    ("COMPONENT.REQUIRED", "Component", "Component", "至少填写一个 Component。", "填写与问题对应的 Component。"),
    ("TEST_CASE_ID.REQUIRED", "Test Case ID", "Test Case ID", "Channel of Reporter 为 Self-Test 时，Test Case ID 必须为三段非空英文字母，以统一的连字符或下划线分隔，或填写 NONE。", "填写“产品线-分类-用例号”或“产品线_分类_用例号”（每段仅英文字母），也可填写 NONE。"),
    ("DESCRIPTION.STEPS_TO_REPRODUCE", "Description", "Description", "Description 必须包含非空的 Steps to reproduce。", "补充可执行的复现步骤。"),
    ("DESCRIPTION.ACTUAL_RESULTS", "Description", "Description", "Description 必须包含非空的 Actual results。", "补充实际发生的结果。"),
    ("DESCRIPTION.EXPECTED_RESULTS", "Description", "Description", "Description 必须包含非空的 Expected results。", "补充预期结果。"),
    ("DESCRIPTION.COMPARISON", "Description", "Description", "Description 必须包含非空的 Comparison。", "补充版本对比信息。"),
    ("DESCRIPTION.NOTES", "Description", "Description", "Description 必须包含非空的 Notes。", "补充包含软硬件信息的备注。"),
    ("DESCRIPTION.RATE_FORMAT", "Description", "Description.Reproducibility rate", "复现概率必须是百分比、分数或明确的文字次数，例如 50%、1/2 或出现一次。", "改为百分比、分数或“出现/复现 + 数字 + 次”；文字次数后可补充一组半角或全角括号说明。"),
    ("DESCRIPTION.NOTES_HW", "Description", "Description.Notes", "Notes 必须包含“HW info: ...”。", "补充 HW info。"),
    ("DESCRIPTION.NOTES_SW", "Description", "Description.Notes", "Notes 必须包含“SW info: ...”。", "补充 SW info。"),
    ("DESCRIPTION.TABLE_REQUIRED_VALUE", "Description", "Description.测试信息", "第二套 Description 表格中每一行的测试信息均不能为空。", "在对应行的测试信息列填写内容；允许填写“无”或“-”。"),
)
_RULES = tuple(AuditRule(*row) for row in _RULE_DATA)

_TEXTUAL_OCCURRENCE_RATE = (
    r"(?:出现|复现)\s*(?:\d+|[零〇一二两三四五六七八九十百千万]+)\s*次"
)
_STANDARD_SUMMARY_RATE_AT_END = re.compile(
    rf"(?P<rate>(?:100|\d{{1,2}})(?:\.\d+)?%|\d+\s*/\s*\d+|"
    rf"{_TEXTUAL_OCCURRENCE_RATE})"
    r"\s*[.。]?\s*$"
)
_DESCRIPTION_RULES = (
    ("steps to reproduce", "DESCRIPTION.STEPS_TO_REPRODUCE"),
    ("actual results", "DESCRIPTION.ACTUAL_RESULTS"),
    ("expected results", "DESCRIPTION.EXPECTED_RESULTS"),
    ("comparison", "DESCRIPTION.COMPARISON"),
    ("notes", "DESCRIPTION.NOTES"),
)
_DESCRIPTION_SECTION_ALIASES = {
    "steps to reproduce": (
        "Steps to reproduce",
        "Reproduction steps",
        "Test steps",
        "操作步骤",
        "复现步骤",
        "测试步骤",
    ),
    "actual results": (
        "Actual result",
        "Actual results",
        "Observed result",
        "Observed results",
        "实际结果",
        "实际现象",
        "问题现象",
    ),
    "expected results": (
        "Expected result",
        "Expected results",
        "Expected behavior",
        "Expected behaviour",
        "预期结果",
        "期望结果",
        "预期现象",
    ),
    "reproducibility rate": (
        "Reproducibility rate",
        "Reproduction rate",
        "Occurrence rate",
        "复现概率",
        "复现率",
        "出现概率",
        "概率",
    ),
    "comparison": (
        "Compare info",
        "Comparison",
        "Comparison info",
        "Version comparison",
        "Third Comparison",
        "Third-party Comparison",
        "Third party comparison",
        "对比信息",
        "版本对比",
        "第三方对比",
        "对比结果",
        "比较结果",
        "对比情况",
        "比较情况",
    ),
    "notes": (
        "Note",
        "Notes",
        "Remark",
        "Remarks",
        "Additional information",
        "备注",
        "补充信息",
    ),
}
_NOTES_INFO_ALIASES = {
    "hw": (
        "HW info",
        "HW information",
        "Hardware info",
        "Hardware information",
        "硬件信息",
    ),
    "sw": (
        "SW info",
        "SW information",
        "Software info",
        "Software information",
        "软件信息",
        "软件版本",
    ),
}
_Failure = Callable[[str, Any, str], None]


class _Issue(NamedTuple):
    key: str
    url: str
    summary: str
    description: str
    creator: str
    components: tuple[str, ...]


def active_rules() -> tuple[AuditRule, ...]:
    return _RULES


def is_audit_eligible(issue: Issue) -> bool:
    match_name = " ".join(_creator_name(issue).split()).casefold()
    return any(match_name == name.casefold() for name in creator_names())


def _creator_name(issue: Issue) -> str:
    creator = issue.creator
    if creator is None:
        return ""
    display_name = " ".join(str(creator.display_name or "").split())
    return display_name or _name_from_username(creator.account or creator.identity)


def _normalize_username(username: Any) -> str:
    value = str(username or "").strip().lower()
    if "\\" in value:
        value = value.rsplit("\\", 1)[-1]
    return value.split("@", 1)[0]


def _name_from_username(username: Any) -> str:
    return " ".join(
        part.capitalize()
        for part in _normalize_username(username).split(".")
        if part
    )


def audit_issue(issue: Issue) -> IssueAuditResult:
    normalized = _normalize_issue(issue)
    selected = {rule.rule_id: rule for rule in _RULES}
    violations: list[AuditViolation] = []

    def fail(rule_id: str, observed: Any, reason: str) -> None:
        rule = selected.get(rule_id)
        if rule:
            violations.append(
                AuditViolation(
                    rule.rule_id, rule.section, rule.field,
                    str(observed or ""), reason, rule.guidance,
                )
            )

    _audit_summary(normalized.summary, fail)
    if not normalized.components:
        fail("COMPONENT.REQUIRED", "[]", "Component 为空。")
    custom_fields = dict(issue.custom_fields.value.values) if issue.custom_fields.value is not None else {}
    channel = _item_text(custom_fields.get('Channel of Reporter'), 'value')
    if channel == 'Self-Test':
        test_case_id = _item_text(custom_fields.get('Test Case ID'))
        if test_case_id != 'NONE' and not re.fullmatch(r'[A-Za-z]+([-_])[A-Za-z]+\1[A-Za-z]+', test_case_id):
            reason = ('Channel of Reporter 为 Self-Test，缺少 Test Case ID。' if not test_case_id
                      else 'Test Case ID 格式不符合要求：须为三段英文字母，以统一的连字符或下划线分隔，或填写 NONE。')
            fail('TEST_CASE_ID.REQUIRED', test_case_id, reason)
    _audit_description(normalized.description, fail)

    return IssueAuditResult(
        key=normalized.key,
        url=normalized.url,
        summary=normalized.summary,
        creator=normalized.creator,
        passed=not violations,
        violations=tuple(violations),
        description=normalized.description,
    )


def _normalize_issue(issue: Issue) -> _Issue:
    return _Issue(
        issue.identity.key,
        issue.identity.web_url,
        issue.summary,
        issue_description(issue),
        _creator_name(issue),
        tuple(component.name for component in issue.components if component.name),
    )


def issue_description(issue: Issue) -> str:
    value = issue.description.value
    return _plain_text(value.value if isinstance(value, RichText) else value)


def _audit_summary(summary: str, fail: _Failure) -> None:
    _groups, _description, errors = _parse_summary_format(summary)
    if errors:
        fail(
            "SUMMARY.FORMAT",
            summary,
            "；".join(errors) + "。",
        )
        return


def _parse_summary_format(
    value: str,
) -> tuple[list[str], str, list[str]]:
    text = str(value or "").strip()
    groups: list[str] = []
    errors: list[str] = []
    position = 0
    bracket_pairs = {"[": "]", "【": "】"}
    while position < len(text):
        while position < len(text) and text[position].isspace():
            position += 1
        opening = text[position] if position < len(text) else ""
        if opening not in bracket_pairs:
            break
        closing = bracket_pairs[opening]
        closing_position = text.find(closing, position + 1)
        if closing_position < 0:
            errors.append(
                f"第 {len(groups) + 1} 个字段缺少右方括号“{closing}”"
            )
            return groups, "", errors
        groups.append(text[position + 1:closing_position].strip())
        position = closing_position + 1

    if not 4 <= len(groups) <= 6:
        errors.append(f"方括号字段数量为 {len(groups)}，要求 4–6 个")
    empty_groups = [
        str(index) for index, group in enumerate(groups, 1)
        if not group
    ]
    if empty_groups:
        errors.append(f"第 {', '.join(empty_groups)} 个方括号字段为空")

    while position < len(text) and text[position].isspace():
        position += 1
    if position >= len(text) or text[position] not in (":", "："):
        errors.append("方括号字段后缺少必需的冒号（允许“:”或“：”）")
        return groups, text[position:].strip(), errors

    body = text[position + 1:].strip()
    rate_match = _STANDARD_SUMMARY_RATE_AT_END.search(body)
    prefix = body[:rate_match.start()].rstrip() if rate_match else body
    if (
        rate_match
        and re.fullmatch(_TEXTUAL_OCCURRENCE_RATE, rate_match.group("rate"))
        and prefix
        and prefix[-1:] not in ",，;；"
    ):
        rate_match = None
        prefix = body
    description = prefix.rstrip(",，;；").rstrip() if rate_match else body
    if not description:
        errors.append("冒号后的问题描述为空")
    return groups, description, errors


def _audit_description(description: str, fail: _Failure) -> dict[str, str]:
    table_rows = _second_description_table_rows(description)
    if table_rows is not None:
        for row_number, field_name, value in table_rows:
            if value.strip():
                continue
            row_label = field_name.strip() or f"第 {row_number} 行"
            fail(
                "DESCRIPTION.TABLE_REQUIRED_VALUE",
                description,
                f"Description 表格中“{row_label}”行的测试信息为空。",
            )
        return {}

    sections = _description_sections(description)
    has_hw_info = _notes_have_info(description, "hw")
    has_sw_info = _notes_have_info(description, "sw")
    for heading, rule_id in _DESCRIPTION_RULES:
        if heading == "notes" and has_hw_info and has_sw_info:
            continue
        if not sections.get(heading, "").strip():
            fail(rule_id, description, f"{heading.title()} 章节缺失或为空。")

    rate = sections.get("reproducibility rate", "").splitlines()
    rate_value = rate[0].strip() if rate else ""
    rate_note = re.fullmatch(
        r"(?P<rate>.+?)\s*(?:\([^()]+\)|（[^（）]+）)",
        rate_value,
    )
    if (
        rate_note
        and re.fullmatch(
            _TEXTUAL_OCCURRENCE_RATE,
            rate_note.group("rate").strip(),
        )
    ):
        rate_value = rate_note.group("rate").strip()
    if rate and not _valid_rate(rate_value):
        fail(
            "DESCRIPTION.RATE_FORMAT",
            description,
            f"Description 中的复现概率“{rate[0].strip()}”格式无效。",
        )
    for info_kind, rule_id, label in (
        ("hw", "DESCRIPTION.NOTES_HW", "硬件"),
        ("sw", "DESCRIPTION.NOTES_SW", "软件"),
    ):
        if not (has_hw_info if info_kind == "hw" else has_sw_info):
            marker = "HW info" if info_kind == "hw" else "SW info"
            fail(
                rule_id,
                description,
                f"Notes 缺少已填写的{label}信息“{marker}: ...”。",
            )
    return sections


def _second_description_table_rows(
    description: str,
) -> list[tuple[int, str, str]] | None:
    header = ("模块", "需要填写信息", "测试信息")
    normalized_header = tuple(_normalize_label(cell) for cell in header)
    lines = description.splitlines()
    for header_index, line in enumerate(lines):
        cells = tuple(
            _normalize_label(cell)
            for cell in re.split(r"\|+", line.strip().strip("|"))[:3]
        )
        if cells != normalized_header:
            continue
        rows: list[tuple[int, str, str]] = []
        for row_number, row_line in enumerate(lines[header_index + 1:], 1):
            row_text = row_line.strip()
            if not row_text.startswith("|") or not row_text.endswith("|"):
                continue
            cells = row_text[1:-1].split("|")
            if len(cells) < 3:
                continue
            rows.append((row_number, cells[1], cells[2]))
        return rows
    return None


def _description_sections(description: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current = ""
    aliases = {
        alias: section
        for section, section_aliases in _DESCRIPTION_SECTION_ALIASES.items()
        for alias in section_aliases
    }
    for line in description.splitlines():
        matched = _match_label_text(line, aliases)
        if matched:
            current, inline_value = matched
            sections.setdefault(current, [])
            if inline_value:
                sections[current].append(inline_value)
        elif current:
            sections[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in sections.items()}


def _notes_have_info(notes: str, info_kind: str) -> bool:
    aliases = {
        alias: info_kind
        for alias in _NOTES_INFO_ALIASES.get(info_kind, ())
    }
    known_labels = {
        alias: alias
        for alias_group in (
            *_DESCRIPTION_SECTION_ALIASES.values(),
            *_NOTES_INFO_ALIASES.values(),
        )
        for alias in alias_group
    }
    lines = notes.splitlines()
    for index, line in enumerate(lines):
        matched = _match_label_text(line, aliases)
        if not matched:
            continue
        if matched[1]:
            return True
        for following in lines[index + 1:]:
            if not following.strip():
                continue
            return _match_label_text(following, known_labels) is None
    return False


def _match_label_text(line: str, aliases: dict[str, str]) -> tuple[str, str] | None:
    for alias in sorted(aliases, key=len, reverse=True):
        match = re.search(re.escape(alias), line, re.IGNORECASE)
        if match:
            value = re.sub(r"^[\s\[\]():;：；*_.#-]+", "", line[match.end():])
            return aliases[alias], value.strip("*_ ")
    return None


def _normalize_label(value: str) -> str:
    return " ".join(value.strip().strip("*_").split()).casefold()


def _valid_rate(value: str) -> bool:
    text = value.strip().rstrip(".")
    if re.fullmatch(_TEXTUAL_OCCURRENCE_RATE, text):
        return True
    if re.fullmatch(r"(?:100|\d{1,2})(?:\.\d+)?%", text):
        return True
    fraction = re.fullmatch(r"(\d+)\s*/\s*(\d+)", text)
    return bool(
        fraction
        and int(fraction.group(2)) > 0
        and int(fraction.group(1)) <= int(fraction.group(2))
    )


def _item_text(item: Any, key: str | None = None) -> str:
    value = item.get(key, "") if key and isinstance(item, dict) else item
    return str(value or "").strip()


def _plain_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _adf_text(value)
    return str(value or "")


def _adf_text(value: Any) -> str:
    if isinstance(value, dict):
        parts = [str(value.get("text", "") or "")]
        parts.extend(_adf_text(item) for item in value.get("content") or ())
        return "\n".join(part for part in parts if part)
    if isinstance(value, list):
        return "\n".join(_adf_text(item) for item in value)
    return ""
