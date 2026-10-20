import { createDataDetails, bindDetailsTrigger, issueColumns } from './data-details.js'
import { healthClass, healthLabel } from './release-health.js'
import { createTaskController } from './task-polling.js'
import { createAsyncFeedback } from './async-feedback.js'
import { escapeHtml } from './dom.js'

export function createReleaseDashboard({ root, api }) {
  root.innerHTML = `<section class="release-page">
    <header class="report-page-head"><div><div class="eyebrow">Delivery · Current releases</div>
      <h1>Project Release Dashboard</h1><p>See delivery risk across the current release for every accessible project.</p></div>
      <div class="filter-actions"><button class="button button-secondary" data-sync>Sync</button></div>
    </header>
    <div class="release-summary" data-summary-grid></div>
    <div class="release-dashboard-grid"><section class="card release-table-card">
      <div class="card-header"><div><h2 class="card-title">Current release health</h2><p class="card-subtitle" data-freshness></p></div></div>
      <div class="report-table-wrap"><table class="report-table"><thead><tr><th>Project / Release</th><th>Stage</th><th>Launch</th><th>Issues</th><th>Next target</th><th>Owner / QA</th><th>Health</th></tr></thead><tbody data-release-rows></tbody></table></div>
      <div class="release-empty" data-release-empty hidden>No current release data in this scope.</div>
    </section><aside class="card release-detail" data-release-detail><h2>Release details</h2><p>Select a project release to inspect its risk reasons.</p></aside></div>
    <div class="async-feedback" data-release-feedback></div>
  </section>`
  const feedback = createAsyncFeedback({ root: root.querySelector('[data-release-feedback]') })
  const details = createDataDetails()
  let disposed = false
  const tasks = createTaskController()

  function setBusy(value, message = '') {
    for (const button of root.querySelectorAll('button')) button.disabled = value
    feedback.update({ state: value ? 'running' : (message ? 'failed' : 'idle'), message })
  }

  function renderSummary(summary = {}) {
    const items = [
      ['currentReleases', 'Current releases'], ['block', 'Blocked'], ['warning', 'Attention'],
      ['openP0P1', 'Open P0 / P1'], ['dataIncomplete', 'Data incomplete'],
    ]
    root.querySelector('[data-summary-grid]').innerHTML = items.map(([key, label]) =>
      `<article class="stat-card"><div class="stat-label">${label}</div><div class="stat-value" data-summary="${key}">${summary[key] ?? 0}</div></article>`
    ).join('')
  }

  function renderDetail(row) {
    const detail = root.querySelector('[data-release-detail]')
    detail.innerHTML = `<div class="eyebrow">${escapeHtml(row.productLine)} · ${escapeHtml(row.currentStage)}</div>
      <h2>${escapeHtml(row.projectName)} <span class="release-id">${escapeHtml(row.projectId)}</span></h2>
      <p class="release-name">${escapeHtml(row.releaseName)}</p>
      <dl class="release-facts"><div><dt>Launch</dt><dd>${escapeHtml(row.launchTime || '目标日期未填写')}</dd></div>
      <div><dt>Current HW Stage</dt><dd>${escapeHtml(row.currentHwStage || '—')}</dd></div>
      <div><dt>Next Target</dt><dd>${escapeHtml(row.nextTarget || '—')} ${escapeHtml(row.nextTargetDate || '')}</dd></div>
      <div><dt>Owner / QA</dt><dd>${escapeHtml(row.projectOwners || '—')} / ${escapeHtml(row.majorFaeQa || '—')}</dd></div></dl>
      <p>${escapeHtml(row.statusSummary || '')}</p><h3>Health reasons</h3>
      <ul class="release-reasons">${(row.health?.reasons ?? []).map(reason => `<li>${escapeHtml(reason)}</li>`).join('') || '<li>No risk trigger.</li>'}</ul>
      <div class="filter-actions"><a class="button button-primary" data-jira-drilldown href="/jira.html?snapshot=dashboard&amp;projectId=${encodeURIComponent(row.projectId)}">View Jira issues</a>
      ${row.confluenceUrl ? `<a class="button button-secondary" href="${escapeHtml(row.confluenceUrl)}" target="_blank" rel="noreferrer">Open Confluence</a>` : ''}</div>`
  }

  function render(payload) {
    renderSummary(payload.summary)
    const releaseRows = payload.releases ?? []
    const releaseColumns = [{ key: 'projectName', label: 'Project', link: 'confluenceUrl' },
      { key: 'projectId', label: 'Project ID' }, { key: 'releaseName', label: 'Release' },
      { key: 'productLine', label: 'Product line' }, { key: 'currentStage', label: 'Stage' },
      { key: 'healthState', label: 'Health' }]
    const issueRows = (releases, kind) => releases.flatMap(release => (release.issues ?? [])
      .filter(issue => kind === 'versionPending' ? issue.releaseAssociation === 'version_pending'
        : !issue.resolution && (kind === 'open' || (kind === 'openP0P1'
          ? ['p0', 'p1'].includes(String(issue.priority).trim().toLowerCase())
          : String(issue.priority).trim().toLowerCase() === kind)))
      .map(issue => ({ ...issue, person: issue.assignee, count: 1 })))
    const openIssues = (releases, kind, value, trigger) => details.open({
      title: `Jira issues · ${kind}`, scope: releases.length === 1 ? `${releases[0].projectName} · ${releases[0].releaseName}` : 'Current release results',
      value, rows: releases.some(release => !release.issues) ? [] : issueRows(releases, kind), columns: issueColumns, trigger,
      error: releases.some(release => !release.issues) ? 'Details are unavailable for this displayed result.' : '',
      updatedAt: payload.sourceFreshness?.jira,
    })
    for (const target of root.querySelectorAll('[data-summary]')) {
      const key = target.dataset.summary
      bindDetailsTrigger(target, trigger => {
        if (key === 'openP0P1') { openIssues(releaseRows, key, payload.summary?.[key], trigger); return }
        const selected = releaseRows.filter(row => key === 'currentReleases' || (key === 'dataIncomplete'
          ? row.health?.dataIncomplete : row.health?.state === (key === 'block' ? 'BLOCK' : 'WARNING')))
        details.open({ title: target.previousElementSibling.textContent, scope: 'Current release results',
          value: payload.summary?.[key], rows: selected.map(row => ({ ...row, healthState: row.health?.state })),
          columns: releaseColumns, trigger, updatedAt: payload.sourceFreshness?.confluence })
      })
    }
    const rows = root.querySelector('[data-release-rows]')
    rows.innerHTML = (payload.releases ?? []).map((row, index) => `<tr tabindex="0" data-release-row data-index="${index}">
      <td><strong>${escapeHtml(row.projectName)}</strong><span>${escapeHtml(row.projectId)} · ${escapeHtml(row.releaseName)}</span></td>
      <td>${escapeHtml(row.currentStage || '—')}</td><td>${escapeHtml(row.launchTime || '目标日期未填写')}<span>${row.daysToLaunch == null ? '' : `${row.daysToLaunch} days`}</span></td>
      <td><strong data-issue-count="open">${row.issueCounts?.open ?? 0} open</strong><span><span data-issue-count="p0">P0 ${row.issueCounts?.p0 ?? 0}</span> · <span data-issue-count="p1">P1 ${row.issueCounts?.p1 ?? 0}</span> · <span data-issue-count="versionPending">pending ${row.issueCounts?.versionPending ?? 0}</span></span></td>
      <td>${escapeHtml(row.nextTarget || '—')}<span>${escapeHtml(row.nextTargetDate || '')}</span></td>
      <td>${escapeHtml(row.projectOwners || '—')}<span>${escapeHtml(row.majorFaeQa || '—')}</span></td>
      <td><span class="${healthClass(row.health?.state)}">${escapeHtml(healthLabel(row.health?.state))}</span><span>${row.health?.reasons?.length ?? 0} reasons</span></td></tr>`).join('')
    root.querySelector('[data-release-empty]').hidden = Boolean(payload.releases?.length)
    root.querySelector('[data-freshness]').textContent = `Confluence ${payload.sourceFreshness?.confluence || 'not cached'} · Jira ${payload.sourceFreshness?.jira || 'not cached'}`
    for (const row of rows.querySelectorAll('[data-release-row]')) {
      const release = payload.releases[Number(row.dataset.index)]
      for (const target of row.querySelectorAll('[data-issue-count]')) bindDetailsTrigger(target, trigger => openIssues([release], target.dataset.issueCount, release.issueCounts?.[target.dataset.issueCount], trigger))
      const select = () => renderDetail(release)
      row.addEventListener('click', select)
      row.addEventListener('keydown', event => { if (event.key === 'Enter') select() })
    }
    if (payload.releases?.length) renderDetail(payload.releases[0])
  }

  async function load() {
    setBusy(true, 'Loading cached releases…')
    try { const payload = await api.getDashboardReleases(); if (!disposed) { render(payload); setBusy(false) } }
    catch { if (!disposed) setBusy(false, 'Release Dashboard unavailable.') }
  }

  root.querySelector('[data-sync]').addEventListener('click', async () => {
    setBusy(true, 'Syncing current server scope…')
    try {
      let payload = await api.syncDashboardReleases()
      if (payload.taskId) {
        const task = await tasks.run(api.getDashboardReleaseSync, payload.taskId)
        if (task) payload = { ...payload, syncState: task.syncState }
      }
      if (!disposed) {
        render(payload)
        setBusy(false)
        if (['failed', 'cancelled', 'invalid_credentials'].includes(payload.syncState)) {
          feedback.update({ state: payload.syncState, dataState: payload.dataState })
        }
      }
    }
    catch { if (!disposed) setBusy(false, 'Release sync failed.') }
  })
  return { start: () => load(), destroy() { details.destroy(); disposed = true; tasks.dispose() } }
}
