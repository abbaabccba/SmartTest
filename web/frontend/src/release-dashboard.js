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
    const rows = root.querySelector('[data-release-rows]')
    rows.innerHTML = (payload.releases ?? []).map((row, index) => `<tr tabindex="0" data-release-row data-index="${index}">
      <td><strong>${escapeHtml(row.projectName)}</strong><span>${escapeHtml(row.projectId)} · ${escapeHtml(row.releaseName)}</span></td>
      <td>${escapeHtml(row.currentStage || '—')}</td><td>${escapeHtml(row.launchTime || '目标日期未填写')}<span>${row.daysToLaunch == null ? '' : `${row.daysToLaunch} days`}</span></td>
      <td>${row.issueCounts?.open ?? 0} open<span>P0 ${row.issueCounts?.p0 ?? 0} · P1 ${row.issueCounts?.p1 ?? 0} · pending ${row.issueCounts?.versionPending ?? 0}</span></td>
      <td>${escapeHtml(row.nextTarget || '—')}<span>${escapeHtml(row.nextTargetDate || '')}</span></td>
      <td>${escapeHtml(row.projectOwners || '—')}<span>${escapeHtml(row.majorFaeQa || '—')}</span></td>
      <td><span class="${healthClass(row.health?.state)}">${escapeHtml(healthLabel(row.health?.state))}</span><span>${row.health?.reasons?.length ?? 0} reasons</span></td></tr>`).join('')
    root.querySelector('[data-release-empty]').hidden = Boolean(payload.releases?.length)
    root.querySelector('[data-freshness]').textContent = `Confluence ${payload.sourceFreshness?.confluence || 'not cached'} · Jira ${payload.sourceFreshness?.jira || 'not cached'}`
    for (const row of rows.querySelectorAll('[data-release-row]')) {
      const select = () => renderDetail(payload.releases[Number(row.dataset.index)])
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
  return { start: () => load(), destroy() { disposed = true; tasks.dispose() } }
}
