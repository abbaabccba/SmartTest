import { createJiraManualAudit } from './manual-audits.js'
import { createTaskController } from './task-polling.js'
import { createAsyncFeedback } from './async-feedback.js'
import { escapeHtml } from './dom.js'

const FILTERS = ['productLine', 'project', 'release', 'fixVersion', 'softwareRelease', 'status', 'resolution', 'priority', 'severity', 'component', 'assignee', 'qaAssignee', 'association']

export function createJiraWorkbench({ root, api, snapshot = '', projectId = '' }) {
  root.innerHTML = `<section class="release-page"><header class="report-page-head"><div><div class="eyebrow">Delivery · Release issues</div>
    <h1>Jira Release Workbench</h1><p data-selected-release>Locate the issues and owners behind current release risk.</p></div>
    <button class="button button-secondary" data-sync>Sync</button></header>
    <div class="jira-workbench"><aside class="card jira-filter-panel"><h2>Filters</h2>
      <details data-advanced-review><summary>Advanced JQL / Review</summary><div data-review-host></div></details>
      <form data-jira-filters><div data-jira-filter-fields></div>
      <div class="filter-actions"><button class="button button-primary" type="submit">Apply</button><button class="button button-secondary" type="button" data-reset>Reset</button></div></form></aside>
      <section class="card jira-issue-panel"><div class="card-header"><div><h2 class="card-title">Issues</h2><p class="card-subtitle" data-issue-counts></p></div></div>
      <div class="jira-issue-list" data-issue-list></div><div class="jira-pagination"><button class="button button-secondary" data-prev>Previous</button><span data-page></span><button class="button button-secondary" data-next>Next</button></div></section>
      <aside class="card jira-detail-panel" data-issue-detail><h2>Issue details</h2><p>Select an issue to inspect its release association.</p></aside></div>
      <div class="async-feedback" data-jira-feedback></div></section>`
  const auditApi = {
    createJiraAudit: async () => { throw { status: 503 } }, getJiraAudit: async () => ({ status: 'failed' }),
    cancelJiraAudit: async () => ({}), exportJiraAudit: async () => ({}), downloadUrl: id => `/api/downloads/${id}`,
    ...api,
  }
  const audit = createJiraManualAudit({ root: root.querySelector('[data-review-host]'), api: auditApi })
  const form = root.querySelector('[data-jira-filters]')
  let page = 0; const pageSize = 50; let current; let disposed = false
  const tasks = createTaskController()
  const feedback = createAsyncFeedback({ root: root.querySelector('[data-jira-feedback]') })

  function renderFilters(facets) {
    const available = new Map((facets ?? []).map(facet => [facet.key, facet.options ?? []]))
    const labels = { productLine: 'Product Line', project: 'Project / Project ID', release: 'Current Release', fixVersion: 'Fix Version', softwareRelease: 'Software Release', status: 'Status', resolution: 'Resolution', priority: 'Priority', severity: 'Severity', component: 'Component', assignee: 'Assignee', qaAssignee: 'QA Assignee', association: 'Release association' }
    root.querySelector('[data-jira-filter-fields]').innerHTML = FILTERS.map(key => `<label>${labels[key]}<select class="form-control" name="${key}"><option value="">All</option>${(available.get(key) ?? []).map(value => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join('')}</select></label>`).join('')
  }

  function selectedFilters() {
    return Object.fromEntries(FILTERS.map(key => [key, form.elements[key]?.value ? [form.elements[key].value] : []]).filter(([, value]) => value.length))
  }

  function render(payload) {
    if (!current) renderFilters(payload.facets)
    current = payload
    const release = payload.selectedRelease
    root.querySelector('[data-selected-release]').textContent = release ? `${release.projectName} · ${release.projectId} · ${release.releaseName}` : 'All accessible current releases'
    root.querySelector('[data-issue-counts]').textContent = `${payload.pagination?.total ?? 0} issues · ${payload.counts?.exact ?? 0} exact · ${payload.counts?.versionPending ?? 0} version pending`
    const list = root.querySelector('[data-issue-list]')
    list.innerHTML = (payload.issues ?? []).map(row => `<button type="button" class="jira-issue-row" data-issue-row data-key="${escapeHtml(row.key)}">
      <span class="jira-issue-key">${escapeHtml(row.key)}</span><strong>${escapeHtml(row.summary)}</strong>
      <span>${escapeHtml(row.priority || '—')} · ${escapeHtml(row.severity || '—')} · ${escapeHtml(row.status)}</span>
      <span>${escapeHtml(row.assignee || 'Unassigned')} · ${escapeHtml(row.components || 'No component')}</span>
      <span>${escapeHtml(row.softwareRelease || row.fixVersions || 'Version pending')} · ${row.releaseAssociation === 'exact' ? 'Exact' : 'Version pending'}</span></button>`).join('') || '<p class="release-empty">No issues in this release scope.</p>'
    for (const row of list.querySelectorAll('[data-issue-row]')) row.addEventListener('click', () => void showIssue(row.dataset.key))
    const pagination = payload.pagination ?? { page: 0, pageSize, total: 0 }
    page = pagination.page
    root.querySelector('[data-page]').textContent = `Page ${page + 1}`
    root.querySelector('[data-prev]').disabled = page <= 0
    root.querySelector('[data-next]').disabled = (page + 1) * pagination.pageSize >= pagination.total
  }

  async function showIssue(key, details = []) {
    const panel = root.querySelector('[data-issue-detail]'); panel.textContent = 'Loading issue…'
    try {
      const issue = await api.getJiraReleaseIssue(key, details)
      if (disposed) return
      panel.innerHTML = `<div class="eyebrow">${escapeHtml(issue.key)} · ${escapeHtml(issue.releaseAssociation)}</div><h2>${escapeHtml(issue.summary)}</h2>
        <dl class="release-facts"><div><dt>Status / Resolution</dt><dd>${escapeHtml(issue.status || '—')} / ${escapeHtml(issue.resolution || '—')}</dd></div>
        <div><dt>Priority / Severity</dt><dd>${escapeHtml(issue.priority || '—')} / ${escapeHtml(issue.severity || '—')}</dd></div>
        <div><dt>Project / Release</dt><dd>${escapeHtml(issue.projectId)} / ${escapeHtml(issue.softwareRelease || issue.fixVersions || '待确认')}</dd></div>
        <div><dt>Assignee / QA / Manager</dt><dd>${escapeHtml(issue.assignee || '—')} / ${escapeHtml(issue.qaAssignee || '—')} / ${escapeHtml(issue.manager || '—')}</dd></div>
        <div><dt>Created / Updated / Resolved</dt><dd>${escapeHtml(issue.createdAt || '—')} / ${escapeHtml(issue.updatedAt || '—')} / ${escapeHtml(issue.resolvedAt || '—')}</dd></div></dl>
        <p>${escapeHtml(issue.associationReason)}</p><div class="filter-actions">
        <button class="button button-secondary" data-load-details>Load description, comments, attachments and links</button>
        ${issue.webUrl ? `<a class="button button-primary" href="${escapeHtml(issue.webUrl)}" target="_blank" rel="noreferrer">Open Jira</a>` : ''}</div>
        <div data-lazy-details>${renderLazy(issue.details)}</div>`
      panel.querySelector('[data-load-details]')?.addEventListener('click', () => void showIssue(key, ['description', 'comments', 'attachments', 'links', 'custom_fields']))
    } catch { if (!disposed) panel.textContent = 'Issue details unavailable.' }
  }

  async function load(filters = {}, options = {}) {
    feedback.update({ state: 'running', message: 'Loading cached issues…' })
    try {
      const payload = await api.getJiraReleaseIssues(filters, { ...options, page, pageSize })
      if (!disposed) { render(payload); feedback.update() }
    } catch { if (!disposed) feedback.update({ state: 'failed', message: 'Jira release workbench unavailable.' }) }
  }
  form.addEventListener('submit', event => { event.preventDefault(); page = 0; void load(selectedFilters()) })
  root.querySelector('[data-reset]').addEventListener('click', () => { current = null; page = 0; void load({}, { reset: true }) })
  root.querySelector('[data-sync]').addEventListener('click', async () => {
    feedback.update({ state: 'running', message: 'Syncing current server scope…' })
    try {
      let payload = await api.syncJiraReleaseIssues()
      if (payload.taskId) {
        const task = await tasks.run(api.getJiraReleaseSync, payload.taskId)
        if (task) payload = { ...payload, syncState: task.syncState }
      }
      if (!disposed) {
        render(payload)
        feedback.update(['failed', 'cancelled', 'invalid_credentials'].includes(payload.syncState)
          ? { state: payload.syncState, dataState: payload.dataState } : undefined)
      }
    } catch {
      if (!disposed) feedback.update({ state: 'failed', message: 'Jira sync failed.' })
    }
  })
  root.querySelector('[data-prev]').addEventListener('click', () => { page -= 1; void load(selectedFilters()) })
  root.querySelector('[data-next]').addEventListener('click', () => { page += 1; void load(selectedFilters()) })
  return {
    start: () => load({}, { snapshot: snapshot || true, ...(projectId ? { projectId } : {}) }),
    destroy() { disposed = true; tasks.dispose(); audit.destroy() },
  }
}

function renderLazy(details = {}) {
  return Object.entries(details).map(([name, section]) => `<section><h3>${escapeHtml(name)}</h3><pre>${escapeHtml(JSON.stringify(section.value ?? null, null, 2))}</pre></section>`).join('')
}
