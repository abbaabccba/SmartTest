"""Weekly audit email bodies; delivery stays with the existing Outlook owner."""

from html import escape
from datetime import datetime
from zoneinfo import ZoneInfo

from core.product_lines import PRODUCT_LINES
from core.confluence.audit.rules import UPDATE_MATRIX_POINTS


REMEDIATION_HEADERS = ('资源', '原规则', '原问题', '复查状态', '复查证据', '链接')
_UPDATE_STATUSES = ('updated', 'not_updated', 'invalid_format', 'failed', 'unknown')


def validate_confluence_summary(summary):
    names = {line.name for line in PRODUCT_LINES}
    metadata = {'counts', 'scope', 'projects', 'generatedAt'}
    if not isinstance(summary, dict) or set(summary) - metadata != names:
        raise ValueError('invalid_confluence_summary')
    statuses = set(_UPDATE_STATUSES)
    if 'counts' in summary and set(summary['counts']) != names:
        raise ValueError('invalid_confluence_counts')
    for name in names:
        pair = summary[name]
        if not isinstance(pair, list) or len(pair) != 2 or any(type(value) is not int or value < 0 for value in pair) or pair[0] > pair[1]:
            raise ValueError('invalid_confluence_summary')
        if 'counts' in summary:
            counts = summary['counts'][name]
            if set(counts) != statuses or any(type(value) is not int or value < 0 for value in counts.values()):
                raise ValueError('invalid_confluence_counts')
            if pair != [counts['updated'], sum(counts.values())]:
                raise ValueError('inconsistent_confluence_counts')
    for project in summary.get('projects', []):
        if project['productLine'] not in names:
            raise ValueError('invalid_confluence_product_line')


def remediation_table(remediation):
    """One explanation and row set shared by the email and XLSX report."""
    if remediation['state'] == 'no_baseline':
        reasons = {
            'no_adjacent_report': '相邻上一审查周期没有已完成审查；同期报告不能替代上期。',
            'missing_filter': '上期审查未保存有效过滤条件，无法重新执行原审查。',
        }
        explanation = '暂无可复核基线：' + reasons.get(remediation.get('reason'),
                         '上期审查缺少原结果或结构化问题明细。')
        return explanation, [(explanation, '', '', '不可比较', '', '')]
    findings = remediation['findings']
    if not findings:
        explanation = ('上期未保存结构化原规则明细，无法逐项比较；本次重审明细见附件。'
                       if remediation.get('missingOriginalFindings') else '上期无待整改问题。')
        return explanation, [(explanation, '', '', '无法逐项比较' if remediation.get('missingOriginalFindings') else '无需整改', '', '')]
    labels = {'fixed': '已修复', 'unfixed': '未修复', 'unverifiable': '无法核验'}
    counts = {state: sum(item['state'] == state for item in findings) for state in labels}
    explanation = (f"原规则问题总数：{len(findings)}；已修复：{counts['fixed']}；"
                   f"未修复：{counts['unfixed']}；无法核验：{counts['unverifiable']}。")
    rows = [(item['resourceId'], item['ruleId'], item.get('reason', ''),
             labels[item['state']], item.get('currentReason') or item.get('error', ''),
             item.get('url', '')) for item in findings]
    return explanation, rows


def compare_jira_audits(original, report):
    rules = {rule.rule_id for rule in report.rules}
    issues = {issue.key: issue for issue in report.issues}
    results = []
    for finding in original:
        issue = issues.get(finding['resourceId'])
        violation = next((item for item in issue.violations if item.rule_id == finding['ruleId']), None) if issue else None
        if issue is None:
            state, reason = 'unverifiable', '当前原过滤条件审查未返回原资源，无法确认是否修复。'
        elif finding['ruleId'] not in rules:
            state, reason = 'unverifiable', '当前审查原规则不可用。'
        elif violation:
            state, reason = 'unfixed', violation.reason
        else:
            state, reason = 'fixed', '当前完整审查原规则通过。'
        results.append({**finding, 'state': state, 'currentReason': reason})
    return results


def summarize_audit(kind, report):
    if kind == "jira":
        issues = {issue.key: issue for issue in report.issues}
        passed = sum(issue.passed for issue in issues.values())
        return {
            "total": len(issues),
            "passed": passed,
            "failed": len(issues) - passed,
            "rate": f"{passed / len(issues) * 100:.2f}%" if issues else "—",
            "issues": [
                {
                    "key": issue.key,
                    "url": issue.url,
                    "creator": issue.creator,
                    "passed": issue.passed,
                    "summary": issue.summary,
                    "aiReviewStatus": issue.ai_review_status.value,
                    "aiFailureCategory": issue.ai_failure_category,
                    "aiPassedCount": issue.ai_passed_count,
                    "aiFailedCount": issue.ai_failed_count,
                    "violations": [
                        {"ruleId": item.rule_id, "section": item.section, "field": item.field,
                         "reason": item.reason, "guidance": item.guidance, "observed": item.observed}
                        for item in issue.violations
                    ],
                }
                for issue in issues.values()
            ],
            "scope": report.resolved.jql,
            "generatedAt": report.generated_at.isoformat(),
            "rules": [{"ruleId": rule.rule_id, "section": rule.section, "field": rule.field,
                       "requirement": rule.requirement, "guidance": rule.guidance}
                      for rule in report.rules],
            "findings": [
                {"resourceId": issue.key, "ruleId": violation.rule_id,
                 "reason": violation.reason, "url": issue.url}
                for issue in issues.values() for violation in issue.violations
            ],
        }
    points = {point.rule_id for point in UPDATE_MATRIX_POINTS}
    result = {line.name: [0, 0] for line in PRODUCT_LINES}
    counts = {
        line.name: {
            status: 0
            for status in _UPDATE_STATUSES
        }
        for line in PRODUCT_LINES
    }
    projects = []
    for audit in report.projects:
        line = next(line for line in PRODUCT_LINES
                    if audit.project.product_space.key in (line.name, line.confluence_space_key))
        projects.append({"projectId": audit.project.identity.project_id,
                         "confluenceId": audit.project.identity.confluence_id,
                         "name": audit.project.name, "productLine": line.name,
                         "owners": list(audit.owners),
                         "rules": [{"ruleId": finding.rule_id, "pageTitle": finding.page_title,
                                    "status": finding.status.value, "reason": finding.reason,
                                    "pageUrl": finding.page_url} for finding in audit.findings]})
        for finding in audit.findings:
            if finding.rule_id in points:
                counts[line.name][finding.status.value] += 1
    for key, values in counts.items():
        result[key] = [values["updated"], sum(values.values())]
    result["counts"] = counts
    result["projects"] = projects
    result["generatedAt"] = report.created_at.isoformat()
    result["scope"] = (
        f"{report.period.start.isoformat()} ≤ 更新时间 < {report.period.end.isoformat()}"
    )
    return result


def _summary_table(labels, rows, old_background='#ffffff'):
    border = "border:1px solid #bcc9da;padding:10px;text-align:left;"
    table = (
        '<table style="border-collapse:collapse;width:100%;font-size:14px"><thead><tr>'
    )
    table += f'<th scope="col" style="{border}background:#dbeafe">指标 / 产品线</th>'
    for index, label in enumerate(labels):
        color = "#fff2cc" if index == 0 else "#dbeafe"
        table += (
            f'<th scope="col" style="{border}background:{color}">{escape(label)}</th>'
        )
    table += "</tr></thead><tbody>"
    for label, values in rows:
        table += f'<tr><th scope="row" style="{border}">{escape(label)}</th>'
        for index, value in enumerate(values):
            color = "#fff2cc" if index == 0 else old_background
            table += f'<td style="{border}background:{color}">{escape(str(value))}</td>'
        table += "</tr>"
    table += "</tbody></table>"
    return table


def render_history_report(kind: str, records: list[dict], *, current=False, remediation=None) -> dict:
    """Render only supplied, dated summaries; never promote previews to audits."""
    records = records[:4]
    if not records:
        return {"state": "unavailable", "html": "", "sourceIds": []}
    title = (
        "FAE QA JIRA描写不符合规范反馈"
        if kind == "jira"
        else "Confluence信息更新检查结果"
    )
    labels = [row["label"] for row in records]
    if kind == "jira":
        rows = [
            (label, [row["summary"][key] for row in records])
            for label, key in (
                ("问题总数", "total"),
                ("通过 Jira 数", "passed"),
                ("不通过 Jira 数", "failed"),
                ("通过率", "rate"),
            )
        ]
        note = (
            "Jira 年份未确认，保留截图月日标签。历史违规人员、Jira 明细及附件未提供。"
        )
    else:
        product_names = tuple(line.name for line in PRODUCT_LINES)
        for row in records:
            validate_confluence_summary(row['summary'])
        if current and ('counts' not in records[0]['summary'] or 'scope' not in records[0]['summary']):
            raise ValueError('missing_confluence_audit_details')
        rows = [
            (
                product_name,
                [
                    f"{row['summary'][product_name][0]} / {row['summary'][product_name][1]}"
                    for row in records
                ],
            )
            for product_name in product_names
        ]
        note = "表内为截图原值：已更新点 / 需要更新点。历史明细及附件未提供。"
    border = "border:1px solid #bcc9da;padding:10px;text-align:left;"
    table = _summary_table(labels, rows)
    details = ""
    if current:
        summary = records[0]["summary"]
        note = f"实际审查范围：{summary['scope']}。历史列采用已保存记录；仅截图来源的记录不含原审查明细。"
        if kind == "jira":
            from urllib.parse import urlsplit

            groups = {}
            for issue in summary["issues"]:
                if not issue["passed"]:
                    groups.setdefault(issue["creator"] or "未知创建人", []).append(
                        issue
                    )
            details = '<h3>当期违规 Jira</h3><table style="border-collapse:collapse;width:100%"><tr>'
            details += (
                "".join(
                    f'<th style="{border}background:#dbeafe">{label}</th>'
                    for label in ("创建人", "违规 Jira 数量", "违规 Jira 号")
                )
                + "</tr>"
            )
            for creator, issues in sorted(groups.items()):
                links = []
                for issue in issues:
                    key, url = escape(issue["key"]), issue["url"]
                    links.append(
                        f'<a href="{escape(url, quote=True)}">{key}</a>'
                        if urlsplit(url).scheme in {"http", "https"}
                        else key
                    )
                details += f'<tr><td style="{border}">{escape(creator)}</td><td style="{border}">{len(issues)}</td><td style="{border}">{", ".join(links)}</td></tr>'
            details += "</table>" if groups else "</table><p>本次无违规 Jira。</p>"
        else:
            note += " 需要更新点为实际受审查的更新点总数，包含已更新、未更新、格式有误、失败和未知。"
            details = '<h3>当期实际状态计数</h3><table style="border-collapse:collapse;width:100%"><tr>'
            details += (
                "".join(
                    f'<th style="{border}background:#dbeafe">{label}</th>'
                    for label in ("产品线", "已更新", "未更新", "失败", "未知")
                )
                + "</tr>"
            )
            for line in PRODUCT_LINES:
                values = summary["counts"][line.name]
                displayed = [
                    values[key]
                    for key in ("updated", "not_updated", "failed", "unknown")
                ]
                details += (
                    "<tr>"
                    + "".join(
                        f'<td style="{border}">{escape(str(value))}</td>'
                        for value in [line.name, *displayed]
                    )
                    + "</tr>"
                )
            details += "</table>"
    subject = f"{title} {labels[0]}" + ("" if current else "（历史预览）")
    introduction = (
        (
            "下面是本周confluence信息更新检查结果，请未更新的项目owner尽快去补充未完成的部分。"
            if kind == "confluence"
            else "下面是针对大家本周创建的bug进行的规范检查，针对还不满足规范的部分，大家需要尽快改善。"
        )
        if current
        else "历史预览：以下数据来自已提供的截图汇总，本次未重新执行审查，未发送邮件。"
    )
    body_title = title if current else subject
    attachment_notice = (
        '<p><strong>详细审查结果及问题明细请查阅附件，请相关负责人逐项核对并及时处理。</strong></p>'
        if current else ''
    )
    body = (
        '<!doctype html><html lang="zh-CN"><meta charset="utf-8">'
        '<body style="font-family:Arial,Microsoft YaHei,sans-serif;color:#172b4d;background:white;padding:20px">'
        f"<h2>{escape(body_title)}</h2><p>Hi all,</p>"
        f"<p>{introduction}</p>{attachment_notice}{table}<p>{escape(note)}</p>{details}</body></html>"
    )
    if kind == 'jira' and remediation is not None:
        section = '<h3>上期问题整改复查</h3>'
        if remediation.get('jql'):
            section += '<p>复查保存的 JQL：' + escape(remediation['jql']) + '</p>'
        if remediation.get('currentSummary') is not None:
            section += '<p>以下为同一保存 JQL 的上次原审查与本次重新审查汇总；逐规则整改明细见 Excel 附件。</p>'
            comparison_rows = [(label, [remediation['currentSummary'][key], remediation['originalSummary'][key]])
                               for label, key in (('问题总数', 'total'), ('通过 Jira 数', 'passed'),
                                                  ('不通过 Jira 数', 'failed'), ('通过率', 'rate'))]
            section += _summary_table(['本次重审 ' + _beijing_time(remediation['reviewedAt']),
                                       '上次原审查 ' + _beijing_time(remediation['originalAt'])],
                                      comparison_rows, old_background='#dbeafe')
        else:
            explanation = (remediation_table(remediation)[0] if remediation['state'] == 'no_baseline'
                           else '本次原过滤条件复查失败，无法生成同 JQL 汇总；无法核验明细见 Excel 附件。')
            section += '<p>' + escape(explanation) + '</p>'
            section += f'<table style="border-collapse:collapse;width:100%"><tr><th style="{border}background:#dbeafe">说明</th></tr>'
            section += f'<tr><td style="{border}">' + escape(explanation) + '</td></tr></table>'
        body = body.replace('</body>', section + '</body>')
    return {
        "state": "completed" if current else "historical_preview",
        "subject": subject,
        "html": body,
        "sourceIds": [row["id"] for row in records],
    }


def _beijing_time(value):
    if 'T' not in value:
        return value
    return datetime.fromisoformat(value).astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S 北京时间')
