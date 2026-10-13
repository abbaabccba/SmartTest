"""Semantic second review for every deterministic Jira violation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
import json
from time import perf_counter

from core.ai import (
    AIChatMessage, AIConfigurationError, AIResponseError, AITransportError,
    create_chat_client,
)
from core.logging import current_platform, smart_log

from .models import AIReviewStatus, AuditViolation, IssueAuditResult
from .rules import active_rules


_MAX_REVIEW_WORKERS = 6


def _public_deepseek_client():
    return create_chat_client("public-deepseek")


def review_failed_issues(results, *, client_factory=None, cancellation, progress):
    failed_indexes = [index for index, result in enumerate(results) if result.violations]
    if not failed_indexes:
        progress("ai_reviewing", 0, 0)
        return results
    factory = client_factory or _public_deepseek_client
    try:
        client = factory()
    except AIConfigurationError:
        return _mark_all(results, failed_indexes, AIReviewStatus.UNCONFIGURED, "configuration", progress)
    except Exception:
        return _mark_all(results, failed_indexes, AIReviewStatus.FAILED, "unexpected", progress)
    with ThreadPoolExecutor(
        max_workers=min(_MAX_REVIEW_WORKERS, len(failed_indexes)),
        thread_name_prefix="jira-ai-review",
    ) as executor:
        futures = {
            executor.submit(review_issue, results[index], client): index
            for index in failed_indexes
        }
        for completed, future in enumerate(as_completed(futures), 1):
            cancellation.raise_if_cancelled()
            results[futures[future]] = future.result()
            progress("ai_reviewing", completed, len(failed_indexes))
    return results


def review_issue(result: IssueAuditResult, client) -> IssueAuditResult:
    started = perf_counter()
    diagnostics = getattr(client, 'request_diagnostics', {})
    fields = {'issue_key': result.key, **diagnostics}
    smart_log('Jira AI review request', platform=current_platform(), domain='audit',
              source='jira_ai_review', emit_runtime_event=False,
              extra={**fields, 'stage': 'request_started'})
    http_status, detail = None, ''
    try:
        response = client.chat_completion(
            [
                AIChatMessage("system", "只输出 Jira 规范复审 JSON，不展示推理过程，不新增规则。"),
                AIChatMessage("user", _prompt(result)),
            ],
            response_format={"type": "json_object"}, temperature=0, max_tokens=2400,
        )
        try:
            returned = json.loads(response.content)
            key_present = isinstance(returned, dict) and 'issue_key' in returned
            key_matches = key_present and returned['issue_key'] == result.key
        except (TypeError, ValueError):
            key_present, key_matches = False, False
        fields.update(returned_issue_key_present=key_present, returned_issue_key_matches=bool(key_matches))
        decisions = _parse_response(
            response.content, issue_key=result.key,
            requested_rule_ids={item.rule_id for item in result.violations},
        )
        reviewed = _merge(result, decisions)
    except AIConfigurationError:
        reviewed = _review_failure(result, AIReviewStatus.UNCONFIGURED, "configuration")
    except TimeoutError:
        reviewed = _review_failure(result, AIReviewStatus.FAILED, "timeout")
    except AITransportError as error:
        category = "timeout" if error.category == "timeout" else "transport"
        http_status = error.status_code
        detail = error.category
        reviewed = _review_failure(result, AIReviewStatus.FAILED, category)
    except AIResponseError:
        detail = 'response_structure'
        reviewed = _review_failure(result, AIReviewStatus.FAILED, "invalid_response")
    except (TypeError, ValueError) as error:
        detail = {'invalid AI issue key': 'issue_key_mismatch',
                  'invalid AI rule decision': 'rule_id_mismatch',
                  'incomplete AI decisions': 'missing_rule_decisions',
                  'invalid AI JSON': 'invalid_json'}.get(str(error), 'response_structure')
        reviewed = _review_failure(result, AIReviewStatus.FAILED, "invalid_response")
    except Exception:
        reviewed = _review_failure(result, AIReviewStatus.FAILED, "unexpected")
    smart_log('Jira AI review request finished', platform=current_platform(), domain='audit',
              source='jira_ai_review', emit_runtime_event=False,
              extra={**fields, 'stage': 'request_finished',
                     'duration_ms': round((perf_counter() - started) * 1000, 3),
                     'status': reviewed.ai_review_status.value, 'category': reviewed.ai_failure_category or '',
                     'failure_detail': detail, 'http_status': http_status,
                     'passed_rule_count': reviewed.ai_passed_count, 'failed_rule_count': reviewed.ai_failed_count})
    return reviewed


def _prompt(result):
    rules = {rule.rule_id: rule for rule in active_rules()}
    payload = {
        "issue_key": result.key,
        "summary": result.summary,
        "description": result.description,
        "violations": [
            {
                "rule_id": item.rule_id,
                "field": item.field,
                "requirement": rules[item.rule_id].requirement,
                "initial_reason": item.reason,
                **({"observed": item.observed} if item.observed != result.description else {}),
            }
            for item in result.violations
        ],
    }
    return (
        "复核输入的全部 Jira 违规，按完整自然语义判断，不新增任何规则。"
        "人类能够清楚理解且语义满足规范时判定 PASS；确实缺少必需信息时判定 FAIL。"
        "应结合全部自然语言判断，不要求信息必须出现在初筛指定位置；"
        "只要 Jira 整体已经明确表达所需信息就判定 PASS。"
        "返回一个 JSON 对象，issue_key 必须与输入相同；decisions 必须为每个输入规则"
        "返回且只返回一次。每项包含 rule_id、result（PASS 或 FAIL）、reason 和 guidance；"
        "FAIL 必须提供非空 reason，guidance 可为空。\nJSON 输出示例：\n"
        + json.dumps({'issue_key': result.key, 'decisions': [
            {'rule_id': item.rule_id, 'result': 'PASS', 'reason': '', 'guidance': ''}
            for item in result.violations]}, ensure_ascii=False)
        + '\n输入：\n'
        + json.dumps(payload, ensure_ascii=False)
    )


def _parse_response(content, *, issue_key, requested_rule_ids):
    try:
        payload = json.loads(str(content or "").strip())
    except (json.JSONDecodeError, TypeError):
        raise ValueError("invalid AI JSON") from None
    if not isinstance(payload, dict) or payload.get("issue_key") != issue_key:
        raise ValueError("invalid AI issue key")
    decisions = payload.get("decisions")
    if not isinstance(decisions, list):
        raise ValueError("invalid AI decisions")
    normalized = {}
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("invalid AI decision")
        rule_id = decision.get("rule_id")
        if rule_id not in requested_rule_ids or rule_id in normalized:
            raise ValueError("invalid AI rule decision")
        outcome = decision.get("result")
        reason, guidance = decision.get("reason", ""), decision.get("guidance", "")
        if outcome not in {"PASS", "FAIL"} or not isinstance(reason, str) or not isinstance(guidance, str):
            raise ValueError("invalid AI decision")
        if outcome == "FAIL" and not reason.strip():
            raise ValueError("AI failure reason is required")
        normalized[rule_id] = (outcome, reason.strip(), guidance.strip())
    if set(normalized) != requested_rule_ids:
        raise ValueError("incomplete AI decisions")
    return normalized


def _merge(result, decisions):
    final: list[AuditViolation] = []
    passed_count = failed_count = 0
    for violation in result.violations:
        if violation.rule_id == 'TEST_CASE_ID.REQUIRED':
            final.append(violation)
            failed_count += 1
            continue
        outcome, reason, guidance = decisions[violation.rule_id]
        if outcome == "PASS":
            passed_count += 1
        else:
            failed_count += 1
            final.append(replace(violation, reason=reason, guidance=guidance or violation.guidance))
    return replace(
        result, passed=not final, violations=tuple(final),
        ai_review_status=AIReviewStatus.COMPLETED, ai_failure_category=None,
        ai_passed_count=passed_count, ai_failed_count=failed_count,
    )


def _review_failure(result, status, category):
    return replace(result, ai_review_status=status, ai_failure_category=category)


def _mark_all(results, indexes, status, category, progress):
    for completed, index in enumerate(indexes, 1):
        results[index] = _review_failure(results[index], status, category)
        progress("ai_reviewing", completed, len(indexes))
    return results
