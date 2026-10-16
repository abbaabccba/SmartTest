import { createTaskController } from './task-polling.js'

const states = { queued: '等待执行', running: '执行中', completed: '已完成', partial: '部分完成', failed: '失败', historical_seed: '历史记录', historical_preview: '历史预览' }

const sources = { manual: '立即触发', scheduled: '每周任务', screenshot: '截图历史' }
const recorded = value => value == null ? '未记录' : String(value)

function historyMarkup(kind) {
  const title = kind === 'jira' ? 'Jira' : 'Confluence'
  const headers = kind === 'jira' ? ['审查日期', '来源', '总数', '修改后通过数', '修改后通过率', '通过', '未通过', '通过率'] : ['审查日期']
  return `<section class="card audit-history" data-audit-history="${kind}"><h2>${title} 历史数据</h2>
    <form class="audit-history-filters" data-preference-region data-preference-scope="audit-email/history/${kind}">
      <label>开始日期<input class="form-control" type="date" name="start_date"></label>
      <label>结束日期<input class="form-control" type="date" name="end_date"></label>
      <button class="button button-secondary" type="submit">查询</button>
    </form>
    <p class="audit-history-note">${kind === 'jira' ? '总数、通过、未通过与通过率为本期新建审查；修改后通过数与修改后通过率来自上期原范围的本次复审，不按修改时间筛选。' : '通过 / 未通过为更新检查点数，不代表项目数；项目数来自已保存项目明细。'} 日期未记录的期次不参与日期范围查询。</p>
    <p data-history-status role="status" aria-live="polite"></p>
    <div class="report-table-wrap"><table class="report-table"><thead><tr>${headers.map(label => `<th scope="col">${label}</th>`).join('')}</tr></thead><tbody data-history-rows></tbody></table></div>
    <div class="filter-actions"><button class="button button-secondary" data-history-prev>上一页</button><span data-history-count></span><button class="button button-secondary" data-history-next>下一页</button></div>
    <div class="audit-history-detail" data-history-detail hidden></div></section>`
}

function appendTable(host, headers, rows) {
  const wrap = document.createElement('div'); wrap.className = 'report-table-wrap'
  const table = document.createElement('table'); table.className = 'report-table'
  const head = table.createTHead().insertRow()
  for (const label of headers) { const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = label; head.append(cell) }
  const body = table.createTBody()
  for (const values of rows) {
    const tr = body.insertRow()
    for (const value of values) tr.insertCell().textContent = recorded(value)
  }
  wrap.append(table); host.append(wrap)
}

function createHistoryBrowser({ root, kind, api, isDestroyed }) {
  let offset = 0
  let revision = 0
  const form = root.querySelector('form')
  let applied = { start_date: '', end_date: '' }
  const status = root.querySelector('[data-history-status]')
  const detail = root.querySelector('[data-history-detail]')
  const prev = root.querySelector('[data-history-prev]')
  const next = root.querySelector('[data-history-next]')
  async function show(row, version) {
    status.textContent = '加载已保存明细…'
    try {
      const result = await api.get(row.id)
      if (isDestroyed() || version !== revision) return
      detail.replaceChildren(); detail.hidden = false
      const heading = document.createElement('h3')
      heading.textContent = `${row.date || row.label}${row.productLine ? ` · ${row.productLine}` : ''} · 已保存明细`
      detail.append(heading)
      if (kind === 'jira') {
        const remediation = result.reports?.jira?.remediation
        for (const [title, summary, timestamp] of [
          ['本期新建审查', result.summary?.jira, result.summary?.jira?.generatedAt],
          ['上期本次复审', remediation?.currentSummary, remediation?.reviewedAt],
        ]) {
          const subheading = document.createElement('h4')
          subheading.textContent = title + (timestamp ? ` · ${new Date(timestamp).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })}` : '')
          detail.append(subheading)
          if (summary) appendTable(detail, ['总数', '通过', '未通过', '通过率'], [[summary.total, summary.passed, summary.failed, summary.rate]])
          const issues = summary?.issues
          if (!issues) { const note = document.createElement('p'); note.textContent = '此期明细未记录。'; detail.append(note) }
          else if (!issues.length) { const note = document.createElement('p'); note.textContent = '已保存明细为空。'; detail.append(note) }
          else appendTable(detail, ['Jira', '标题', '创建人', '结果', '问题明细'], issues.map(issue => [issue.key, issue.summary, issue.creator,
            issue.passed == null ? null : issue.passed ? '通过' : '未通过', issue.violations?.map(finding => `${finding.ruleId}: ${finding.reason}`).join('\n')]))
        }
      } else {
        for (const line of row.lines) {
          const subheading = document.createElement('h4'); subheading.textContent = line.productLine; detail.append(subheading)
          const items = result.summary?.confluence?.projects?.filter(project => project.productLine === line.productLine)
          if (!items) { const note = document.createElement('p'); note.textContent = '此期明细未记录。'; detail.append(note) }
          else if (!items.length) { const note = document.createElement('p'); note.textContent = '已保存明细为空。'; detail.append(note) }
          else appendTable(detail, ['项目', '项目 ID', '负责人', '检查点明细'], items.map(project => [project.name, project.projectId, project.owners?.join('、'),
            project.rules?.map(rule => `${rule.pageTitle || rule.ruleId} · ${rule.status} · ${rule.reason}`).join('\n')]))
        }
      }
      status.textContent = '仅展示已保存的历史数据。'
    } catch (error) { if (!isDestroyed() && version === revision) status.textContent = error.message }
  }
  async function load() {
    const version = ++revision
    status.textContent = '加载历史数据…'; detail.hidden = true
    const body = root.querySelector('[data-history-rows]'); body.replaceChildren()
    prev.disabled = true; next.disabled = true
    try {
      const result = await api.history(kind, { offset, ...applied })
      if (isDestroyed() || version !== revision) return
      if (kind === 'confluence') {
        const head = root.querySelector('thead'); head.replaceChildren()
        const groups = head.insertRow(); const date = document.createElement('th')
        date.rowSpan = 2; date.scope = 'col'; date.textContent = '审查日期'; groups.append(date)
        const metrics = head.insertRow()
        for (const name of result.productLines) {
          const group = document.createElement('th'); group.colSpan = 3; group.scope = 'colgroup'; group.textContent = name; groups.append(group)
          for (const label of ['项目数', '通过', '未通过']) {
            const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = label; metrics.append(cell)
          }
        }
      }
      for (const row of result.rows) {
        const tr = document.createElement('tr')
        const date = document.createElement('td'); const button = document.createElement('button')
        button.type = 'button'; button.className = 'audit-history-view'; button.dataset.historyView = ''
        button.textContent = row.date || `${row.label}（年份未记录）`
        tr.addEventListener('click', () => { void show(row, ++revision) })
        date.append(button); tr.append(date)
        const values = kind === 'jira' ? [sources[row.source] || row.source, row.total, row.modifiedPassed, row.modifiedRate, row.passed, row.failed, row.rate] : row.lines.flatMap(line => [line.projectCount, line.passed, line.failed])
        for (const value of values) { const cell = document.createElement('td'); cell.textContent = recorded(value); tr.append(cell) }
        if (kind === 'confluence' && row.source === 'screenshot') button.textContent += ' · 截图历史'
        body.append(tr)
      }
      root.querySelector('[data-history-count]').textContent = result.total ? `${offset + 1}–${offset + result.rows.length} / ${result.total} · 每页 10 条` : '暂无历史数据'
      prev.disabled = offset === 0; next.disabled = offset + 10 >= result.total
      status.textContent = result.total ? '点击审查日期查看已保存明细。' : '当前条件下没有已保存历史数据。'
    } catch (error) { if (!isDestroyed() && version === revision) status.textContent = error.message }
  }
  form.addEventListener('submit', event => {
    event.preventDefault(); offset = 0
    applied = { start_date: form.elements.start_date.value, end_date: form.elements.end_date.value }
    void load()
  })
  prev.addEventListener('click', () => { offset -= 10; void load() })
  next.addEventListener('click', () => { offset += 10; void load() })
  return { load }
}

export function createAuditEmailPage({ root, api, pollDelay = () => new Promise(resolve => setTimeout(resolve, 1000)) }) {
  let destroyed = false
  let offset = 0
  const tasks = createTaskController({ delay: pollDelay, interval: 0 })
  root.innerHTML = `<section class="page-stack">
    <section class="card"><h2>审查与报告</h2><p>发件人：fae-qa-auto@amlogic.com</p><p>立即触发与每周五北京时间 18:00 的 Windows 任务执行相同的本周周一到周五审查，并将 Jira 与 Confluence 邮件发送至 fae.qa@amlogic.com。</p>
    <button class="button button-primary" data-trigger>立即触发</button><p data-status role="status" aria-live="polite">加载执行历史…</p></section>
    ${historyMarkup('jira')}${historyMarkup('confluence')}
    <section class="card"><h2>执行历史</h2><p>每次执行独立保存。</p><div style="overflow-x:auto"><table class="report-table" style="width:100%"><thead><tr><th>时间</th><th>来源</th><th>结果</th><th>操作</th></tr></thead><tbody data-history></tbody></table></div><div class="filter-actions"><button class="button button-secondary" data-prev>较新记录</button><span data-count></span><button class="button button-secondary" data-next>更早记录</button></div></section>
    <section class="card" data-detail hidden><h2>报告详情</h2><div data-reports></div><label>运行信息（可选择复制）<textarea class="form-control" data-evidence readonly rows="8" style="width:100%"></textarea></label></section></section>`
  const browsers = ['jira', 'confluence'].map(kind => createHistoryBrowser({ root: root.querySelector(`[data-audit-history="${kind}"]`), kind, api, isDestroyed: () => destroyed }))
  const status = root.querySelector('[data-status]')
  const trigger = root.querySelector('[data-trigger]')
  async function listing() {
    const result = await api.list(offset)
    if (destroyed) return
    const body = root.querySelector('[data-history]'); body.replaceChildren()
    for (const row of result.runs) {
      const tr = document.createElement('tr')
      const label = row.label.includes('T') ? new Date(row.label).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : row.label
      for (const value of [label, { manual: '立即触发', scheduled: '每周任务', screenshot: '截图' }[row.source] || row.source, states[row.state] || row.state]) {
        const td = document.createElement('td'); td.textContent = value; tr.append(td)
      }
      const td = document.createElement('td'); const button = document.createElement('button')
      button.type = 'button'; button.className = 'button button-secondary'; button.textContent = '查看'; button.dataset.view = ''
      button.addEventListener('click', () => { void perform(async () => follow(await api.get(row.id))) })
      td.append(button); tr.append(td); body.append(tr)
    }
    root.querySelector('[data-count]').textContent = result.total ? `${offset + 1}–${offset + result.runs.length} / ${result.total}` : '暂无记录'
    root.querySelector('[data-prev]').disabled = offset === 0
    root.querySelector('[data-next]').disabled = offset + 4 >= result.total
  }
  function show(detail) {
    if (destroyed) return
    status.textContent = `${states[detail.state] || detail.state} · ${detail.id}`
    root.querySelector('[data-detail]').hidden = false
    root.querySelector('[data-evidence]').value = detail.evidence || ''
    const reports = root.querySelector('[data-reports]'); reports.replaceChildren()
    for (const [kind, title] of [['jira', 'Jira'], ['confluence', 'Confluence']]) {
      const heading = document.createElement('h3'); heading.textContent = `${title} 报告`; reports.append(heading)
      const report = detail.reports[kind]
      if (report?.html) {
        const frame = document.createElement('iframe'); frame.title = `${title} 报告预览`; frame.setAttribute('sandbox', '')
        frame.style.cssText = 'width:100%;height:490px;border:1px solid var(--border-color);background:white;margin-bottom:20px'
        frame.srcdoc = report.html; reports.append(frame)
      } else { const text = document.createElement('p'); text.textContent = report?.error || '此期没有可用报告。'; reports.append(text) }
      for (const name of report?.attachments || []) {
        const link = document.createElement('a'); link.textContent = name; link.className = 'button button-secondary'
        link.href = api.attachmentUrl(detail.id, kind, name); reports.append(link)
      }
    }
  }
  async function follow(detail) {
    let initial = detail
    const result = await tasks.run(async () => {
      if (initial) { const value = initial; initial = null; return value }
      return api.get(detail.id)
    }, detail.id, show)
    if (result && !destroyed) await Promise.all([listing(), ...browsers.map(browser => browser.load())])
  }
  async function perform(action) { try { await action() } catch (error) { if (!destroyed) status.textContent = error.message } }
  trigger.addEventListener('click', () => perform(async () => {
    trigger.disabled = true; status.textContent = '正在触发…'
    try { const detail = await api.trigger(); if (destroyed) return; offset = 0; await listing(); await follow(detail) }
    finally { if (!destroyed) trigger.disabled = false }
  }))
  for (const [selector, delta] of [['[data-prev]', -4], ['[data-next]', 4]]) root.querySelector(selector).addEventListener('click', () => perform(async () => { offset += delta; await listing() }))
  return { async start() { await perform(async () => { await Promise.all([listing(), ...browsers.map(browser => browser.load())]); if (!destroyed) status.textContent = '可查看历史，或立即触发本周 Jira 与 Confluence 审查并发送邮件。' }) }, destroy() { destroyed = true; tasks.dispose(); root.replaceChildren() } }
}
