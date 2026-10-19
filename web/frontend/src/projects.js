import { icon as renderIcon } from './icons.js'
const STATE_COPY = {
  loading: 'Loading project catalog…',
  ready: '',
  no_snapshot: 'No local project snapshot is available.',
  schema_error: 'Local project snapshot is unreadable.',
  partial_success: 'Some project facts are stale or failed.',
  failed: 'Project catalog load failed.',
  reauthentication_required: 'Please verify your account again before refreshing project data.'
}
const COMMON_FILTERS = [
  '__product_space__', 'date of commercial approval', 'project id', 'project owner'
]
import { createAsyncFeedback } from './async-feedback.js'
import { createDownloadButton } from './download-button.js'
import { createDisposableDisplayCache } from './disposable-display.js'
import { element as node } from './dom.js'
import { createTaskController } from './task-polling.js'
import { bindProjectStage, bindProjectStatus, bindProjectStatusText } from './project-semantics.js'
import { bindProductLine } from './product-line-semantics.js'

export function createProjects({ root, api, waitForPreferences, account,
  pollDelay = ms => new Promise(resolve => setTimeout(resolve, ms)), downloadNavigate,
  enableReview = true, filterOnly = false, onSnapshot = () => {} }) {
  const display = createDisposableDisplayCache('projects', account)
  const saveDisplay = payload => {
    if (['ready', 'partial_success'].includes(payload?.state)) display.write(payload)
  }
  root.innerHTML = `<section class="report-workspace projects-workspace">
    <header class="report-page-head"><div><div class="eyebrow">${filterOnly ? 'Confluence · Global Filter' : 'Projects · Current Facts'}</div><h1>${filterOnly ? 'Confluence Filter' : 'Projects'}</h1><p>${filterOnly ? 'Apply the shared project scope before starting the weekly review.' : '查看本地只读项目事实与 QA 责任信息。'}</p></div></header>
    <form class="card report-filter-card"><div class="report-state report-state-loading" role="status">Loading project catalog…</div><div class="report-filter-grid" data-main-facets></div>
      <div class="report-filter-grid"><label>Project / Person / Field Search<input class="form-control" name="search" type="search" placeholder="Project, person or field"></label>
      <div class="filter-actions"><button class="button button-primary" type="submit">Apply Filters</button><button class="button button-secondary" type="button" data-cancel hidden>Cancel Sync</button><button class="button button-secondary" type="button" data-reset>Reset</button></div></div>
      ${enableReview ? `<section class="weekly-review" data-confluence-review><strong class="weekly-review-title">Weekly Review</strong><div class="weekly-review-controls"><label>Start<input class="form-control" name="reviewStartDate" type="date"></label><label>End<input class="form-control" name="reviewEndDate" type="date"></label>
        <button class="button button-secondary" type="button" data-audit>Review Filters</button><button class="button button-secondary" type="button" data-audit-cancel disabled>Cancel Review</button><button class="button button-primary" type="button" data-audit-download disabled>Download</button></div></section>` : ''}</form>
    <div class="async-feedback" data-async-feedback></div><div class="inline-status" data-audit-status aria-live="polite"></div>
    <section class="projects-summary" data-summary ${filterOnly ? 'hidden' : ''}></section>
    <section class="card report-preview" ${filterOnly ? 'hidden' : ''}><header class="report-preview-toolbar"><strong>Projects by Product Lines</strong><span class="count-badge" data-count>0 projects</span></header>
      <div class="report-preview-body product-space-groups" data-projects></div></section></section>`
  const form = root.querySelector('form')
  const facetRoot = root.querySelector('[data-main-facets]')
  const status = root.querySelector('[role="status"]')
  const projectsRoot = root.querySelector('[data-projects]')
  const auditButton = root.querySelector('[data-audit]')
  const cancelButton = root.querySelector('[data-cancel]')
  const auditCancelButton = root.querySelector('[data-audit-cancel]')
  const auditDownloadButton = root.querySelector('[data-audit-download]')
  let facets = []
  let cacheReady = false
  let destroyed = false
  let pollGeneration = 0
  const syncTasks = createTaskController({ delay: pollDelay, interval: 500,
    stateOf: task => task?.state === 'loading' ? 'running' : task?.state })
  let activeSync = null
  let activeAuditId = ''
  let productSpaceDefinitions = []
  let snapshotFields = null
  let snapshotRestored = false
  const feedback = createAsyncFeedback({ root: root.querySelector('[data-async-feedback]'),
    cancelButton, onCancel: cancelSync })
  const auditDownload = enableReview ? createDownloadButton({
    element: auditDownloadButton,
    prepare: async () => (await api.exportConfluenceAudit(activeAuditId)).download,
    navigate: downloadNavigate,
    artifactUrl: api.downloadUrl,
  }) : null
  if (auditDownload) auditDownload.element.disabled = true

  function setDefaultReviewPeriod() {
    const now = new Date(Date.now() + 8 * 60 * 60 * 1000)
    const day = now.getUTCDay() || 7
    const monday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - day + 1))
    const previous = new Date(monday.getTime() - 7 * 24 * 60 * 60 * 1000)
    form.elements.reviewStartDate.value = previous.toISOString().slice(0, 10)
    form.elements.reviewEndDate.value = monday.toISOString().slice(0, 10)
  }
  if (enableReview) setDefaultReviewPeriod()

  function taskState(value) {
    return ({ running: 'running', queued: 'running', completed: 'success',
      failed: 'failed', cancelled: 'cancelled' }[value] ?? 'idle')
  }

  function childMessage(message, task) {
    const child = task?.visibleChild
    return child ? `${message} · ${child.label}` : message
  }

  function updateFeedback(sync) {
    const task = sync?.task
    if (sync?.state === 'loading') feedback.update({ state: task ? taskState(task.state) : 'running',
      message: childMessage('Syncing project details…', task),
      processed: task?.progress?.processed ?? sync.completed, total: task?.progress?.total ?? sync.total })
    else if (sync?.state === 'failed') feedback.update({ state: 'failed', message: 'Project detail sync failed.', dataState: sync.dataState })
    else if (sync?.state === 'cancelled') feedback.update({ state: 'cancelled', dataState: sync?.dataState })
    else if (sync?.state === 'ready' && feedback.state === 'running') feedback.update({ state: 'success' })
  }

  function updateAuditFeedback(audit) {
    const task = audit?.task
    const running = ['queued', 'running'].includes(audit?.status)
    const state = task ? taskState(task.state) : (running ? 'running' : ({
      completed: 'success', failed: 'failed', cancelled: 'cancelled'
    }[audit?.status] ?? 'idle'))
    const stage = childMessage('', task) || audit?.stage
    feedback.update({ state, stage, processed: task?.progress?.processed ?? audit?.progress?.processed,
      total: task?.progress?.total ?? audit?.progress?.total })
  }

  const currentFilters = () => {
    const fields = {}
    for (const facet of facets) {
      const control = form.elements[`field.${facet.key}`]
      const values = control ? selected(control) : []
      if (values.length) fields[facet.key] = values
    }
    return { fields, search: form.elements.search.value }
  }

  const contextToken = filters => JSON.stringify({
    fields: Object.fromEntries(Object.entries(filters.fields ?? {}).sort(([a], [b]) => a.localeCompare(b))
      .map(([key, values]) => [key, [...values].map(value => String(value).trim()).filter(Boolean).sort()])),
    search: String(filters.search ?? '').trim()
  })

  function setBusinessControlsEnabled(enabled, { applyEnabled = enabled } = {}) {
    if (auditButton) auditButton.disabled = !enabled || !applyEnabled
    form.querySelector('[type="submit"]').disabled = !applyEnabled
    form.querySelector('[data-reset]').disabled = !enabled
    form.elements.search.disabled = !enabled
    for (const control of form.querySelectorAll('select')) {
      control.disabled = !enabled
      control._multiSelect?.setDisabled(!enabled)
    }
  }

  function renderFacets(nextFacets, { loading = false } = {}) {
    const selected = snapshotFields ?? currentFilters().fields
    facets = nextFacets ?? []
    facetRoot.replaceChildren()
    const byKey = new Map(facets.map(facet => [facet.key, facet]))
    for (const key of COMMON_FILTERS) {
      const facet = byKey.get(key)
      if (!facet) continue
      const label = node('label', '', facet.labels?.length > 1 ? `${facet.label} (${facet.labels.join(' / ')})` : facet.label)
      const select = node('select', 'form-select'); select.name = `field.${facet.key}`; select.multiple = true
      select.dataset.readyLabel = `All ${facet.label}`
      fillSelect(select, facet.options ?? [])
      for (const option of select.options) option.selected = (selected[facet.key] ?? []).includes(option.value)
      label.append(select); facetRoot.append(label)
      enhanceMultiSelect(select, { emptyLabel: loading ? 'Loading…' : `All ${facet.label}`, compact: true, searchable: false })
    }
    setBusinessControlsEnabled(cacheReady || (loading && facets.some(facet => facet.options?.length)),
      { applyEnabled: cacheReady })
    snapshotFields = null
  }

  function updateFacetOptions(nextFacets) {
    facets = nextFacets ?? facets
    for (const select of form.querySelectorAll('select[name^="field."]')) {
      select._multiSelect?.setEmptyLabel(select.dataset.readyLabel)
    }
    for (const facet of facets) {
      const select = form.elements[`field.${facet.key}`]
      if (!select) continue
      const selectedValues = selected(select)
      const validValues = new Set((facet.options ?? []).map(option => String(option?.value ?? option)))
      const missingSelected = selectedValues.filter(value => !validValues.has(value))
      fillSelect(select, [...(facet.options ?? []), ...missingSelected])
      for (const option of select.options) option.selected = selectedValues.includes(option.value)
      select._multiSelect?.syncFromSelect()
    }
  }

  function projectKey(project) { return project.identity || `${project.space_key || ''}:${project.project_id || ''}` }

  function readableName(person) {
    const name = String(person?.name ?? '').trim()
    const identity = String(person?.identity ?? '').trim()
    return name || identity || 'Unknown'
  }

  function renderSummary(hierarchy, projects, blockProjectCount, warningProjectCount) {
    const people = new Set(); let assignments = 0
    for (const role of hierarchy) for (const person of role.people ?? []) {
      people.add(person.identity || readableName(person)); assignments += person.projects?.length ?? 0
    }
    const roleAssignmentCount = role => {
      const assignments = new Set()
      for (const project of projects) for (const person of project.roles?.[role] ?? []) {
        assignments.add(`${projectKey(project)}\u0000${person.identity || readableName(person)}`)
      }
      return assignments.size
    }
    const summary = root.querySelector('[data-summary]'); summary.replaceChildren()
    const row = (label, value, status = '') => {
      const item = node('div', 'summary-metric-row'); item.dataset.metricRow = ''
      if (status) bindProjectStatusText(item, status)
      item.append(node('span', '', label), node('strong', '', value))
      return item
    }
    const card = (...rows) => {
      const item = node('article', 'card summary-metric'); item.dataset.metric = ''; item.append(...rows)
      summary.append(item)
      return item
    }
    card(row('Support Projects', projects.length)).classList.add('summary-metric-single')
    card(row('Block Projects', blockProjectCount, 'BLOCK'), row('Warning Projects', warningProjectCount, 'WARNING'))
      .classList.add('summary-metric-paired')
    card(row('Major FAE QA Resources', roleAssignmentCount('Major FAE QA')),
      row('FAE QA Resources', roleAssignmentCount('FAE QA'))).classList.add('summary-metric-resources')
    const modeCounts = new Map(productSpaceDefinitions.map(({ value }) => [value, { S: 0, A: 0 }]))
    for (const project of projects) {
      const counts = modeCounts.get(project.space_key)
      const mode = String(project.support_mode || project.fields?.['support mode'] || '').trim().toUpperCase()
      if (counts && (mode === 'S' || mode === 'A')) counts[mode] += 1
    }
    const modeRows = productSpaceDefinitions.map(({ value, label }) => {
      const counts = modeCounts.get(value)
      const item = row(label, `S ${counts.S} · A ${counts.A}`); item.dataset.productModeRow = ''
      return item
    })
    const modeCard = card(...modeRows); modeCard.classList.add('summary-metric-product-modes')
    const modeTitle = node('div', 'summary-metric-title', 'Support Mode'); modeTitle.dataset.metricTitle = ''
    modeCard.prepend(modeTitle)
    card(row('Avg assignments / person', people.size ? (assignments / people.size).toFixed(1) : '0.0'))
      .classList.add('summary-metric-average')
  }

  function renderProjects(hierarchy, projects = [], blockProjectCount, warningProjectCount) {
    projectsRoot.replaceChildren()
    const seenProjects = new Set()
    const uniqueProjects = projects.filter(project => {
      const key = projectKey(project)
      if (seenProjects.has(key)) return false
      seenProjects.add(key)
      return true
    })
    root.querySelector('[data-count]').textContent = `${uniqueProjects.length} projects`
    renderSummary(hierarchy ?? [], uniqueProjects, blockProjectCount, warningProjectCount)
    const projectStage = project => String(project.stage || project.fields?.['current stage'] || '').trim()
    const displayValue = value => Array.isArray(value) ? value.filter(item => item != null && String(item).trim()).join(', ') : String(value ?? '').trim()
    const comparePresentAsc = (left, right) => {
      const leftText = displayValue(left), rightText = displayValue(right)
      if (!leftText) return rightText ? 1 : 0
      if (!rightText) return -1
      return leftText.localeCompare(rightText, undefined, { numeric: true, sensitivity: 'base' })
    }
    const comparePresentDesc = (left, right) => {
      const leftText = displayValue(left), rightText = displayValue(right)
      if (!leftText) return rightText ? 1 : 0
      if (!rightText) return -1
      return rightText.localeCompare(leftText, undefined, { numeric: true, sensitivity: 'base' })
    }
    const projectDisplayName = project => String(project.name || project.project_id || '').trim()
      .replace(/^\d+\.\*?\s*/, '').replace(/\s*-\s*Project Status Report\s*$/i, '').trim()
    const coreFields = new Set([
      '__product_space__', 'project id', 'project status', 'current stage', 'support mode', 'mp time', 'wifi module',
    ])
    const previewRoles = ['Major FAE QA', 'FAE QA', 'QA Reviewer']
    const addFact = (rootNode, label, value) => {
      const text = displayValue(value) || 'Unknown'
      const row = node('div', 'project-card-fact project-detail-item')
      row.append(node('span', 'project-card-label', label), node('span', 'project-card-value', text))
      rootNode.append(row)
      return row
    }
    const createProjectCard = project => {
      const card = node('article', 'kanban-card project-card project-list-row')
      card.dataset.projectId = projectKey(project)
      const summary = node('div', 'project-card-summary')
      const primary = node('div', 'project-list-primary')
      const pageUrl = String(project.page_url ?? '').trim()
      const projectName = projectDisplayName(project)
      const heading = node(pageUrl ? 'a' : 'div', `kanban-card-title${pageUrl ? ' project-card-link' : ''}`, pageUrl ? null : projectName)
      if (pageUrl) {
        heading.href = pageUrl
        heading.target = '_blank'
        heading.rel = 'noopener noreferrer'
        const icon = node('span', 'project-card-link-icon')
        icon.innerHTML = renderIcon('external-link')
        icon.setAttribute('aria-hidden', 'true')
        heading.append(icon, document.createTextNode(projectName))
      }
      const summaryFacts = node('div', 'project-card-summary-facts project-list-meta')
      const descriptionFact = addFact(summaryFacts, 'Description', project.customer_summary)
      addFact(summaryFacts, 'Project ID', project.project_id)
      addFact(summaryFacts, 'MP Time', project.fields?.['mp time'])
      addFact(summaryFacts, 'WIFI Module', project.fields?.['wifi module'])
      const badges = node('div', 'project-card-badges')
      for (const [value, semantic] of [[project.status, 'status'], [project.support_mode, 'support-mode']]) {
        const text = displayValue(value) || 'Unknown'
        const badge = node('span', 'badge badge-blue', text)
        if (semantic === 'status' && text !== 'Unknown') bindProjectStatus(badge, text)
        badges.append(badge)
      }
      const facts = node('div', 'project-card-details project-card-facts project-detail-strip')
      for (const role of previewRoles) {
        addFact(facts, role, (project.roles?.[role] ?? []).map(readableName))
      }
      for (const [label, value] of Object.entries(project.fields ?? {})) {
        if (!coreFields.has(label.toLocaleLowerCase())) addFact(facts, label, value)
      }
      primary.append(heading)
      summary.append(primary, descriptionFact, summaryFacts, badges)
      card.append(summary)
      if (facts.childElementCount) card.append(facts)
      return card
    }

    const groupByStage = projects => {
      const groups = new Map()
      for (const project of projects) {
        const stage = projectStage(project) || 'Unspecified'
        if (!groups.has(stage)) groups.set(stage, { name: stage, projects: [] })
        groups.get(stage).projects.push(project)
      }
      return [...groups.values()].sort((left, right) => right.projects.length - left.projects.length)
    }

    const appendStageGroups = (container, projects) => {
      for (const stage of groupByStage(projects)) {
        const stageGroup = node('details', 'stage-group'); stageGroup.dataset.stageGroup = ''
        const stageSummary = node('summary', 'stage-summary')
        bindProjectStage(stageSummary, stage.name)
        const stageCount = node('span', 'kanban-count', stage.projects.length); stageCount.dataset.stageProjectCount = ''
        stageSummary.append(node('strong', '', stage.name), stageCount)
        const cards = node('div', 'project-list')
        stage.projects.sort((left, right) => comparePresentAsc(left.support_mode, right.support_mode)
          || comparePresentDesc(left.status, right.status))
          .forEach(project => cards.append(createProjectCard(project)))
        stageGroup.append(stageSummary, cards); container.append(stageGroup)
      }
    }

    const groupByLaunchOs = projects => {
      const groups = new Map()
      for (const project of projects) {
        const launchOs = displayValue(project.fields?.['launch os']) || 'Unspecified'
        if (!groups.has(launchOs)) groups.set(launchOs, { name: launchOs, projects: [] })
        groups.get(launchOs).projects.push(project)
      }
      return [...groups.values()].sort((left, right) => right.projects.length - left.projects.length)
    }

    const statusPriority = ['block', 'warning', 'pending', 'normal', 'cancel']
    const statusKind = status => statusPriority.find(keyword => status.toLocaleLowerCase().includes(keyword)) || ''
    const compareProjectStatus = (left, right) => {
      const priority = status => {
        const index = statusPriority.indexOf(statusKind(status))
        return index < 0 ? statusPriority.length : index
      }
      return priority(left) - priority(right) || comparePresentAsc(left, right)
    }

    const createProjectStatusSummary = projects => {
      const distribution = node('span', 'label-distribution'); distribution.dataset.projectStatusSummary = ''
      const counts = new Map()
      for (const project of projects) {
        const status = displayValue(project.status) || 'Unspecified'
        counts.set(status, (counts.get(status) ?? 0) + 1)
      }
      for (const [status, count] of [...counts].sort(([left], [right]) => compareProjectStatus(left, right))) {
        const item = node('span', 'distribution-label'); item.dataset.projectStatusCount = ''
        bindProjectStatus(item, status)
        item.append(node('span', '', status), node('strong', 'distribution-label-count', count))
        distribution.append(item)
      }
      return distribution
    }

    for (const { value: productSpaceKey, label: productSpaceLabel, projectGrouping } of productSpaceDefinitions) {
      const spaceProjects = uniqueProjects.filter(project => project.space_key === productSpaceKey)
      const group = node('section', 'product-space-group'); group.dataset.productSpaceGroup = ''
      const summary = node('button', 'product-space-summary'); summary.type = 'button'; summary.dataset.productSpaceToggle = ''
      bindProductLine(summary, productSpaceLabel, 'button')
      summary.setAttribute('aria-expanded', 'true')
      const count = node('span', 'kanban-count', spaceProjects.length); count.dataset.productCount = ''
      summary.append(node('strong', 'kanban-title', productSpaceLabel), createProjectStatusSummary(spaceProjects), count)
      const stageGroups = node('div', 'stage-groups'); stageGroups.dataset.productGrid = ''
      if (spaceProjects.length) {
        if (projectGrouping === 'launch_os') {
          for (const launchOs of groupByLaunchOs(spaceProjects)) {
            const launchGroup = node('details', 'launch-os-group'); launchGroup.dataset.launchOsGroup = ''; launchGroup.open = true
            const launchSummary = node('summary', 'launch-os-summary')
            const launchCount = node('span', 'kanban-count', launchOs.projects.length); launchCount.dataset.launchOsProjectCount = ''
            launchSummary.append(node('strong', '', launchOs.name), launchCount)
            const launchStages = node('div', 'launch-os-stage-groups')
            appendStageGroups(launchStages, launchOs.projects)
            launchGroup.append(launchSummary, launchStages); stageGroups.append(launchGroup)
          }
        } else {
          appendStageGroups(stageGroups, spaceProjects)
        }
      } else stageGroups.append(node('div', 'product-space-empty', 'No projects.'))
      summary.addEventListener('click', () => {
        const expanded = summary.getAttribute('aria-expanded') === 'true'
        summary.setAttribute('aria-expanded', String(!expanded)); stageGroups.hidden = expanded
      })
      group.append(summary, stageGroups); projectsRoot.append(group)
    }
  }

  function present(payload, {
    updateHierarchy = true, updateFacets = true, detailRequested = false,
  } = {}) {
    const hasCache = payload.dataState === 'ready' || ['ready', 'partial_success'].includes(payload.state)
    const hasDetailJob = detailRequested || Boolean(activeSync)
    const syncing = payload.sync?.state === 'loading'
    if (!snapshotRestored && payload.querySnapshot) {
      snapshotRestored = true
      snapshotFields = payload.querySnapshot.filters ?? {}
      form.elements.search.value = payload.querySnapshot.search ?? ''
    }
    if (hasDetailJob) updateFeedback(payload.sync)
    if (payload.productSpaces) productSpaceDefinitions = payload.productSpaces
    cacheReady = hasCache
    if (!facets.length) renderFacets(payload.facets, { loading: payload.state === 'loading' })
    else if (updateFacets && hasCache) updateFacetOptions(payload.facets)
    for (const select of form.querySelectorAll('select[name^="field."]')) {
      select._multiSelect?.setEmptyLabel(payload.state === 'loading' ? 'Loading…' :
        hasCache ? select.dataset.readyLabel : 'Unavailable')
    }
    if (updateHierarchy) {
      renderProjects(payload.ownerHierarchy ?? [], payload.projects ?? [], payload.blockProjectCount, payload.warningProjectCount)
    }
    status.className = `report-state report-state-${payload.state}`
    status.textContent = STATE_COPY[payload.state] ?? ''
    status.hidden = payload.state === 'ready'
    if (payload.detailState === 'reauthentication_required') {
      root.querySelector('[data-audit-status]').textContent = 'Please verify your account again before loading responsibility details.'
    }
    setBusinessControlsEnabled(hasCache, { applyEnabled: !syncing })
    if (auditButton) auditButton.disabled = !hasCache || syncing
    saveDisplay(payload)
    if (hasCache) onSnapshot(payload)
  }

  function showLoadFailure(message) {
    activeSync = null
    status.className = 'report-state report-state-failed'
    status.hidden = false
    status.textContent = message
    if (!cacheReady) {
      for (const select of form.querySelectorAll('select[name^="field."]')) {
        select._multiSelect?.setEmptyLabel('Unavailable')
      }
    }
    setBusinessControlsEnabled(cacheReady, { applyEnabled: true })
  }

  async function poll(generation) {
    await pollDelay(500)
    if (destroyed || generation !== pollGeneration || !root.isConnected) return
    try {
      const sync = await syncTasks.run(api.getProjectFactsStatus, 'project-facts', value => {
        if (activeSync) updateFeedback(value)
      })
      if (destroyed || generation !== pollGeneration || !root.isConnected) return
      if (!sync) return
      const payload = await api.getProjectFacts({}, { snapshot: true })
      if (destroyed || generation !== pollGeneration || !root.isConnected) return
      const contextUnchanged = !activeSync || contextToken(currentFilters()) === activeSync.token
      if (!activeSync || contextUnchanged) present(payload)
      updateFeedback({ ...sync, dataState: contextUnchanged ? payload.dataState : undefined })
      activeSync = null
      setBusinessControlsEnabled(cacheReady, { applyEnabled: sync.state !== 'loading' })
    } catch {
      if (destroyed || generation !== pollGeneration) return
      if (activeSync) feedback.update({ state: 'failed', message: 'Project detail sync failed.' })
      showLoadFailure('Local project facts API is unavailable. Use Apply Filters to retry.')
    }
  }

  async function load({ updateHierarchy = true, updateFacets = true,
    snapshot = false, reset = false,
    beginPolling = true } = {}) {
    const generation = ++pollGeneration
    const requestedFilters = currentFilters()
    if (!cacheReady) {
      setBusinessControlsEnabled(false)
      status.className = 'report-state report-state-loading'; status.hidden = false; status.textContent = STATE_COPY.loading
    }
    try {
      const options = {
        ...(snapshot ? { snapshot: true } : {}), ...(reset ? { reset: true } : {}),
      }
      const payload = await api.getProjectFacts(requestedFilters, options)
      if (destroyed || generation !== pollGeneration) return
      present(payload, { updateHierarchy, updateFacets })
      if (beginPolling && (payload.state === 'loading' || payload.sync?.state === 'loading')) poll(generation)
      return payload
    } catch {
      if (destroyed || generation !== pollGeneration) return
      if (feedback.state === 'running') feedback.update({ state: 'failed', message: 'Project detail sync failed.' })
      if (!cacheReady) renderProjects([])
      showLoadFailure('Local project facts API is unavailable. Use Apply Filters to retry.')
    }
  }
  form.addEventListener('submit', event => {
    event.preventDefault()
    setBusinessControlsEnabled(cacheReady, { applyEnabled: false })
    void api.applyConfluenceFilterSnapshot(currentFilters()).then(payload => {
      if (destroyed) return
      present(payload, { updateHierarchy: true, updateFacets: false, detailRequested: true })
      if (payload.sync?.state === 'loading') {
        const generation = ++pollGeneration
        activeSync = { token: contextToken(currentFilters()), filters: currentFilters() }
        poll(generation)
      }
    }).catch(() => { if (!destroyed) showLoadFailure('Project filter apply failed. Use Apply Filters to retry.') })
  })
  root.querySelector('[data-reset]').addEventListener('click', () => {
    form.elements.search.value = ''
    for (const select of form.querySelectorAll('select[name^="field."]')) {
      for (const option of select.options) option.selected = false
      select._multiSelect?.syncFromSelect()
    }
    renderFacets(facets)
    void api.resetConfluenceFilterSnapshot().then(payload => { if (!destroyed) present(payload) })
      .catch(() => { if (!destroyed) status.textContent = 'Project catalog API is unavailable.' })
  })
  auditButton?.addEventListener('click', async () => {
    auditButton.disabled = true
    auditCancelButton.disabled = false
    auditDownload.element.disabled = true
    root.querySelector('[data-audit-status]').textContent = ''
    try {
      const created = await api.createConfluenceAudit({
        startDate: form.elements.reviewStartDate.value,
        endDate: form.elements.reviewEndDate.value,
      })
      if (destroyed) return
      activeAuditId = created.auditId
      let audit = created
      updateAuditFeedback(audit)
      while (['queued', 'running'].includes(audit.status)) {
        await pollDelay(500)
        if (destroyed) return
        audit = await api.getConfluenceAudit(activeAuditId)
        if (destroyed) return
        updateAuditFeedback(audit)
      }
      auditDownload.element.disabled = audit.status !== 'completed'
    } catch (error) {
      if (destroyed) return
      root.querySelector('[data-audit-status]').textContent = error?.status === 422 ? 'invalid review period' : 'audit unavailable'
      feedback.update({ state: 'failed' })
    } finally {
      if (!destroyed) { auditButton.disabled = false; auditCancelButton.disabled = true }
    }
  })
  auditCancelButton?.addEventListener('click', async () => {
    await api.cancelConfluenceAudit(activeAuditId)
    auditCancelButton.disabled = true
  })
  async function cancelSync() {
    await api.cancelProjectSync()
    pollGeneration += 1
    activeSync = null
    cancelButton.hidden = true
    feedback.update({ state: 'cancelled' })
    setBusinessControlsEnabled(cacheReady, { applyEnabled: cacheReady })
  }
  async function start() {
    const previousDisplay = display.read()
    if (previousDisplay) present(previousDisplay)
    const loaded = await load({ snapshot: true })
    await waitForPreferences?.()
    if (destroyed) return
    return loaded
  }
  return {
    start,
    destroy() { destroyed = true; pollGeneration += 1; syncTasks.dispose(); auditDownload?.destroy() },
  }
}
import { enhanceMultiSelect, fillSelect, selected } from './multi-select.js'
