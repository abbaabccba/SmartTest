// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createProjects as createProjectsOwner } from '../src/projects.js'

function createProjects(options) {
  const api = options.api
  api.applyConfluenceFilterSnapshot ??= vi.fn(filters => api.getProjectFacts(filters))
  api.resetConfluenceFilterSnapshot ??= vi.fn(() => api.getProjectFacts({}, { reset: true }))
  return createProjectsOwner(options)
}

const productSpaces = [
  { value: 'DOPL', label: 'China Operator' },
  { value: 'SDPL', label: 'Smart Device' },
  { value: 'TV', label: 'TV' },
  { value: 'OOPL', label: 'Global Operator & STB' },
]

const payload = {
  state: 'partial_success',
  blockProjectCount: 0,
  warningProjectCount: 0,
  productSpaces,
  counts: { stale: 1, failed: 0, inactive: 2 }, discrepancies: ['Unexpected Owner'],
  facets: [
    { key: '__product_space__', label: 'Product Space', labels: ['Product Space'], options: [{ value: 'DOPL', label: 'China Operator' }, { value: 'TV', label: 'TV' }] },
    { key: 'support mode', label: 'Support Mode', labels: ['Support Mode'], options: ['A', 'B'] },
    { key: 'unexpected owner', label: 'Unexpected Owner', labels: ['Unexpected Owner'], options: ['Alice'] }
  ],
  projects: [{ project_id: 'A-1', name: 'Apollo', space_key: 'DOPL', status: 'stale', fields: { 'support mode': 'B' },
    roles: { 'Major FAE QA': [{ name: 'Coco', identity: 'u-1' }] } }],
  ownerHierarchy: [{ role: 'Major FAE QA', people: [{ name: 'Coco', identity: 'u-1', projects: [
    { project_id: 'A-1', name: 'Apollo', space_key: 'DOPL', status: 'stale', fields: { 'support mode': 'B' } }
  ] }] }, { role: 'FAE QA', people: [] }, { role: 'QA Reviewer', people: [] }]
}
const fixedFacets = [
  ['__product_space__', 'Product Space'], ['page', 'Page'], ['date of commercial approval', 'Date of Commercial approval'],
  ['project id', 'Project ID'], ['odm', 'ODM'], ['oem/operator', 'OEM/Operator'], ['key part number', 'Key Part Number'],
  ['project status', 'Project Status'], ['current stage', 'Current Stage'], ['major pm', 'Major PM'], ['project owner', 'Project Owner'],
  ['support mode', 'Support Mode'], ['launch os', 'Launch OS'], ['date of kick off', 'Date of Kick Off'],
  ['planned closure', 'planned closure'], ['actual closure', 'actual closure'], ['mp time', 'MP Time'], ['launch time', 'Launch Time'],
  ['next target', 'Next Target'], ['next target date', 'Next Target Date'], ['sum', 'Sum']
].map(([key, label]) => ({ key, label, labels: [label], options: [] }))

describe('Projects', () => {
  beforeEach(() => { document.body.innerHTML = '<div id="app"></div>'; localStorage.clear(); sessionStorage.clear() })

  it('keeps matching database projects usable after refresh failure', async () => {
    const page = createProjects({ root: document.querySelector('#app'), api: {
      getProjectFacts: vi.fn().mockResolvedValue({ ...payload, state: 'failed', dataState: 'ready', sync: { state: 'failed' } }),
    } })
    await page.start()
    expect(document.querySelector('[data-projects]').textContent).toContain('Apollo')
    expect(document.querySelector('[type="submit"]').disabled).toBe(false)
    page.destroy()
  })

  it('does not render Role workload on the Projects page', async () => {
    await createProjects({ root: document.querySelector('#app'), api: {
      getProjectFacts: vi.fn().mockResolvedValue(payload),
    } }).start()
    expect(document.querySelector('[data-workload-widget]')).toBeNull()
  })

  it('renders the previous account display before snapshot calibration completes', async () => {
    const first = createProjects({ root: document.querySelector('#app'), account: 'alice',
      api: { getProjectFacts: vi.fn().mockResolvedValue(payload) } })
    await first.start(); first.destroy()
    document.body.innerHTML = '<div id="app"></div>'
    let finishSnapshot
    const calibrated = { ...payload, projects: [], ownerHierarchy: [] }
    const second = createProjects({ root: document.querySelector('#app'), account: 'alice',
      api: { getProjectFacts: vi.fn(() => new Promise(resolve => { finishSnapshot = resolve })) } })

    const starting = second.start()
    expect(document.querySelector('[data-projects]').textContent).toContain('Apollo')
    finishSnapshot(calibrated)
    await starting
    expect(document.querySelectorAll('.project-card')).toHaveLength(0)
  })

  it('isolates disposable display state by account and clears it on identity change', async () => {
    const alice = createProjects({ root: document.querySelector('#app'), account: 'alice',
      api: { getProjectFacts: vi.fn().mockResolvedValue(payload) } })
    await alice.start()
    alice.destroy()
    document.body.innerHTML = '<div id="app"></div>'
    const bob = createProjects({ root: document.querySelector('#app'), account: 'bob',
      api: { getProjectFacts: vi.fn(() => new Promise(() => {})) } })

    void bob.start()
    expect(document.querySelector('[data-projects]').textContent).not.toContain('Apollo')
    expect(document.querySelector('[role="status"]').textContent).toContain('Loading')
  })

  it('keeps the previous display when snapshot calibration fails', async () => {
    const first = createProjects({ root: document.querySelector('#app'), account: 'alice',
      api: { getProjectFacts: vi.fn().mockResolvedValue(payload) } })
    await first.start(); first.destroy()
    document.body.innerHTML = '<div id="app"></div>'
    const second = createProjects({ root: document.querySelector('#app'), account: 'alice',
      api: { getProjectFacts: vi.fn().mockRejectedValue(new Error('offline')) } })

    await second.start()
    expect(document.querySelector('[data-projects]').textContent).toContain('Apollo')
    expect(document.querySelector('[role="status"]').textContent).toContain('unavailable')
  })

  it('keeps the page and review dates when Reset cannot restore the catalog scope', async () => {
    let failReset
    const api = { getProjectFacts: vi.fn().mockResolvedValueOnce({ ...payload, state: 'ready' })
      .mockImplementationOnce(() => new Promise((_resolve, reject) => { failReset = reject })) }
    const page = createProjects({ root: document.querySelector('#app'), api })
    await page.start()
    const form = document.querySelector('form'), select = form.elements['field.__product_space__']
    select.value = 'TV'
    form.elements.search.value = 'local edit'
    form.elements.reviewStartDate.value = '2026-08-17'
    form.elements.reviewEndDate.value = '2026-08-24'
    const review = form.querySelector('[data-audit]')
    form.querySelector('[data-reset]').click()
    await vi.waitFor(() => expect(failReset).toBeTypeOf('function'))
    failReset(new Error('offline'))
    await vi.waitFor(() => expect(document.querySelector('[role="status"]').textContent).toContain('unavailable'))
    expect(document.querySelector('form')).toBe(form)
    expect(form.elements['field.__product_space__'].value).toBe('')
    expect(form.elements.search.value).toBe('')
    expect(form.elements.reviewStartDate.value).toBe('2026-08-17')
    expect(form.elements.reviewEndDate.value).toBe('2026-08-24')
    expect(form.querySelector('[data-audit]')).toBe(review)
    expect(document.querySelector('[role="status"]').textContent).toContain('unavailable')
    page.destroy()
  })

  it('renders the persisted session snapshot on entry without starting remote work', async () => {
    const api = { getProjectFacts: vi.fn().mockResolvedValue({ ...payload, state: 'ready' }) }
    const page = createProjects({ root: document.querySelector('#app'), api, pollDelay: () => new Promise(() => {}) })
    await page.start()
    expect(api.getProjectFacts).toHaveBeenCalledOnce()
    expect(api.getProjectFacts.mock.calls[0]).toEqual([{ fields: {}, search: '' }, { snapshot: true }])
    expect(document.querySelector('[data-projects]').textContent).toContain('Apollo')
    expect(document.querySelector('[name="field.__product_space__"]').disabled).toBe(false)
    page.destroy()
  })

  it('renders only supported project filters and no more-filter UI', async () => {
    const api = { getProjectFacts: vi.fn().mockResolvedValue(payload) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    expect(document.body.textContent).toContain('Product Space')
    expect(document.body.textContent).not.toContain('Unexpected Owner')
    expect(document.querySelector('.more-filter-panel')).toBeNull()
    expect(document.body.textContent).toContain('Apollo')
    expect(document.body.textContent).toContain('Major FAE QA')
    expect(document.body.textContent).toContain('Coco')
    expect(document.body.textContent).toContain('Some project facts are stale or failed')
    expect(document.querySelector('[name="reportType"]')).toBeNull()
    expect(document.querySelector('.report-directory')).toBeNull()
    expect(document.querySelector('[data-download]')).toBeNull()
  })

  it('groups projects under collapsed Current Stage rows inside expanded product lines', async () => {
    const grouped = {
      ...payload,
      state: 'ready',
      facets: [
        { key: '__product_space__', label: 'Product Space', options: productSpaces },
      ],
      projects: [
        { identity: 'p-first', project_id: 'P-1', name: 'First China Project', space_key: 'DOPL', status: 'NORMAL', stage: 'Validation', customer_summary: 'Customer A', roles: { 'Major FAE QA': [{ name: 'Coco' }, { name: 'Bob' }] }, fields: { 'mp time': '2026-10-18', tags: ['one', 'two'] } },
        { identity: 'p-tv', project_id: 'P-2', name: 'TV Project', space_key: 'TV', status: 'NORMAL', stage: 'Development', support_mode: 'A', customer_summary: 'Customer B', roles: { 'FAE QA': [{ name: 'Alice' }] }, fields: { odm: 'ODM B' } },
        { identity: 'p-second', project_id: 'P-3', name: 'Second China Project', space_key: 'DOPL', status: 'BLOCK', stage: 'Pilot', roles: {} },
        { identity: 'p-none', project_id: 'P-4', name: 'No Owner Project', space_key: 'SDPL', stage: '', roles: {} },
        { identity: 'p-first', project_id: 'P-1', name: 'Duplicate First Project', space_key: 'DOPL', stage: 'Development', roles: {} },
      ],
      ownerHierarchy: [],
    }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(grouped) }
    await createProjects({ root: document.querySelector('#app'), api }).start()

    expect(document.body.textContent).toContain('Projects by Product Lines')
    const groups = [...document.querySelectorAll('[data-product-space-group]')]
    expect(groups.map(group => group.querySelector('[data-product-space-toggle] strong').textContent)).toEqual([
      'China Operator', 'Smart Device', 'TV', 'Global Operator & STB',
    ])
    const toggles = groups.map(group => group.querySelector('[data-product-space-toggle]'))
    expect(toggles.every(toggle => toggle.getAttribute('aria-expanded') === 'true')).toBe(true)
    expect(groups.map(group => group.querySelector('[data-product-count]').textContent)).toEqual(['2', '1', '1', '0'])
    const statusSummary = groups[0].querySelector('[data-project-status-summary]')
    expect(statusSummary.textContent).not.toContain('Current Stage')
    const stageContainer = groups[0].querySelector('.stage-groups')
    expect(statusSummary.parentElement).toBe(toggles[0])
    expect([...groups[0].children]).toEqual([toggles[0], stageContainer])
    expect([...statusSummary.querySelectorAll('[data-project-status-count]')].map(item => item.textContent)).toEqual([
      'BLOCK1', 'NORMAL1',
    ])
    expect([...statusSummary.querySelectorAll('[data-project-status-count]')].every(item => item.classList.contains('distribution-label'))).toBe(true)
    expect([...statusSummary.querySelectorAll('[data-project-status-count] strong')].map(item => item.textContent)).toEqual(['1', '1'])
    toggles[0].click()
    expect(toggles[0].getAttribute('aria-expanded')).toBe('false')
    expect(stageContainer.hidden).toBe(true)
    expect(statusSummary.closest('[data-product-space-toggle]')).toBe(toggles[0])
    expect([...groups[1].querySelectorAll('[data-project-status-count]')].map(item => item.textContent)).toEqual(['Unspecified1'])
    const statusLabels = [...document.querySelectorAll('[data-project-status-count]')]
    const normalSlots = statusLabels.filter(label => label.firstElementChild.textContent === 'NORMAL')
      .map(label => label.dataset.projectStatus)
    expect(normalSlots).toEqual([normalSlots[0], normalSlots[0]])
    expect(normalSlots[0]).toBe('NORMAL')
    const stageGroups = [...groups[0].querySelectorAll('[data-stage-group]')]
    expect(stageGroups.map(group => group.querySelector('summary strong').textContent)).toEqual(['Validation', 'Pilot'])
    expect(stageGroups.map(group => group.querySelector('[data-stage-project-count]').textContent)).toEqual(['1', '1'])
    expect(stageGroups.every(group => !group.open)).toBe(true)
    expect([...stageGroups[0].querySelectorAll('.project-card')].map(card => card.dataset.projectId)).toEqual(['p-first'])
    expect([...stageGroups[1].querySelectorAll('.project-card')].map(card => card.dataset.projectId)).toEqual(['p-second'])
    expect(document.querySelectorAll('[data-project-id="p-first"]')).toHaveLength(1)
    expect(document.querySelector('[data-project-id="p-none"]').textContent).toContain('No Owner Project')
    expect(document.querySelector('[data-project-id="p-first"]').textContent).toContain('Coco, Bob')
    expect(document.querySelector('[data-project-id="p-first"]').textContent).toContain('one, two')
    expect(document.querySelector('[data-project-id="p-first"]').textContent).not.toContain('Validation')
    expect(groups.every(group => group.querySelector('[data-product-grid]'))).toBe(true)
    expect([...document.querySelectorAll('.project-card-summary .badge')].map(item => item.textContent))
      .not.toEqual(expect.arrayContaining(['DOPL', 'SDPL', 'TV', 'OOPL']))
  })

  it('orders Current Stage groups by project count descending', async () => {
    const project = (identity, stage) => ({
      identity, project_id: identity, name: identity, space_key: 'DOPL', status: 'ACTIVE', stage,
      customer_summary: 'Customer', roles: {}, fields: {}
    })
    const sorted = { ...payload, state: 'ready', projects: [
      project('small-1', 'Small'),
      project('high-1', 'High'), project('high-2', 'High'), project('high-3', 'High'),
      project('equal-a-1', 'Equal A'), project('equal-a-2', 'Equal A'),
      project('equal-b-1', 'Equal B'), project('equal-b-2', 'Equal B'),
    ], ownerHierarchy: [] }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(sorted) }
    await createProjects({ root: document.querySelector('#app'), api }).start()

    const groups = [...document.querySelector('[data-product-space-group]').querySelectorAll('[data-stage-group]')]
    expect(groups.map(group => group.querySelector('summary strong').textContent)).toEqual(['High', 'Equal A', 'Equal B', 'Small'])
    expect(groups.map(group => group.querySelector('[data-stage-project-count]').textContent)).toEqual(['3', '2', '2', '1'])
  })

  it('groups only TV projects by Launch OS before Current Stage', async () => {
    const behaviorSpaces = [
      { value: 'plain-space', label: 'Plain Space', projectGrouping: null },
      { value: 'grouped-space', label: 'Grouped Space', projectGrouping: 'launch_os' },
    ]
    const project = (identity, spaceKey, launchOs, stage) => ({
      identity, project_id: identity, name: identity, space_key: spaceKey, status: 'NORMAL', stage,
      customer_summary: 'Customer', roles: {}, fields: { 'launch os': launchOs },
    })
    const grouped = { ...payload, productSpaces: behaviorSpaces, state: 'ready', ownerHierarchy: [], projects: [
      project('tv-android-evt', 'grouped-space', 'Android 16', 'EVT'),
      project('tv-android-dvt', 'grouped-space', 'Android 16', 'DVT'),
      project('tv-linux-evt', 'grouped-space', 'Linux', 'EVT'),
      project('tv-missing', 'grouped-space', undefined, 'EVT'),
      project('tv-blank', 'grouped-space', '  ', 'DVT'),
      project('dopl-android', 'plain-space', 'Android 16', 'Pilot'),
    ] }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(grouped) }

    await createProjects({ root: document.querySelector('#app'), api }).start()

    const productGroups = [...document.querySelectorAll('[data-product-space-group]')]
    const tv = productGroups.find(group => group.querySelector('[data-product-space-toggle] strong').textContent === 'Grouped Space')
    const launchGroups = [...tv.querySelectorAll(':scope > [data-product-grid] > [data-launch-os-group]')]
    expect(launchGroups.map(group => group.querySelector(':scope > summary strong').textContent)).toEqual([
      'Android 16', 'Unspecified', 'Linux',
    ])
    expect(launchGroups.map(group => group.querySelector(':scope > summary [data-launch-os-project-count]').textContent)).toEqual([
      '2', '2', '1',
    ])
    expect([...launchGroups[0].querySelectorAll('[data-stage-group]')].map(group => [
      group.querySelector('summary strong').textContent,
      group.querySelector('[data-stage-project-count]').textContent,
    ])).toEqual([['EVT', '1'], ['DVT', '1']])
    expect([...tv.querySelectorAll('.project-card')]).toHaveLength(5)
    expect(new Set([...tv.querySelectorAll('.project-card')].map(card => card.dataset.projectId)).size).toBe(5)

    const dopl = productGroups.find(group => group.querySelector('[data-product-space-toggle] strong').textContent === 'Plain Space')
    expect(dopl.querySelector('[data-launch-os-group]')).toBeNull()
    expect([...dopl.querySelectorAll(':scope > [data-product-grid] > [data-stage-group]')]
      .map(group => group.querySelector('summary strong').textContent)).toEqual(['Pilot'])
  })

  it('prioritizes Project Status labels and marks only their semantic warning tones', async () => {
    const statuses = ['9 CANCEL CLOSE', 'NORMAL 09/05', '7 PENDING', 'WARNING', 'BLOCK', 'ACTIVE']
    const stages = ['1 EVALUATION', '2 IN DEVELOPMENT', '3 SYSTEM UPGRADE', '4 MP MAINTENANCE', '5 MP CLOSE', '6 POC CLOSE']
    const api = { getProjectFacts: vi.fn().mockResolvedValue({
      ...payload, state: 'ready', ownerHierarchy: [], projects: statuses.map((status, index) => ({
        identity: `status-${index}`, project_id: `P-${index}`, name: status, space_key: 'DOPL',
        status, support_mode: index === 0 ? 'BLOCK' : '', stage: stages[index], roles: {}, fields: {},
      })),
    }) }

    await createProjects({ root: document.querySelector('#app'), api }).start()

    const labels = [...document.querySelector('[data-project-status-summary]').children]
    expect(labels.map(label => label.firstElementChild.textContent)).toEqual([
      'BLOCK', 'WARNING', '7 PENDING', 'NORMAL 09/05', '9 CANCEL CLOSE', 'ACTIVE',
    ])
    expect(labels.map(label => label.dataset.projectStatus || '')).toEqual([
      'BLOCK', 'WARNING', '', 'NORMAL', '', '',
    ])
    const supportMode = document.querySelectorAll('[data-project-id="status-0"] .project-card-badges .badge')[1]
    const blockStatus = document.querySelectorAll('[data-project-id="status-4"] .project-card-badges .badge')[0]
    expect(supportMode.textContent).toBe('BLOCK')
    expect(supportMode.dataset.projectStatus).toBeUndefined()
    expect(blockStatus.textContent).toBe('BLOCK')
    expect(blockStatus.dataset.projectStatus).toBe('BLOCK')
    expect([...document.querySelectorAll('.stage-summary')].map(item => item.dataset.projectStage).sort()).toEqual(stages)
  })

  it('orders projects by Support Mode ascending then Project Status descending', async () => {
    const project = (identity, supportMode, status) => ({
      identity, project_id: identity, name: identity, space_key: 'DOPL', stage: '2 IN DEVELOPMENT',
      support_mode: supportMode, status, customer_summary: 'Customer', roles: {}, fields: {}
    })
    const sorted = { ...payload, state: 'ready', projects: [
      project('b-normal', 'B', 'NORMAL'), project('none-warning', '', 'WARNING'),
      project('a-normal', 'A', 'NORMAL'), project('b-warning', 'B', 'WARNING'),
      project('a-warning', 'A', 'WARNING'),
    ], ownerHierarchy: [] }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(sorted) }

    await createProjects({ root: document.querySelector('#app'), api }).start()

    const cards = [...document.querySelector('[data-stage-group]').querySelectorAll('.project-card')]
    expect(cards.map(card => card.dataset.projectId)).toEqual([
      'a-warning', 'a-normal', 'b-warning', 'b-normal', 'none-warning',
    ])
  })

  it('renders the persisted snapshot while preferences restore unapplied controls', async () => {
    let releasePreferences
    const api = { getProjectFacts: vi.fn().mockResolvedValue(payload) }
    const waitForPreferences = vi.fn(() => new Promise(resolve => {
      releasePreferences = () => {
        document.querySelector('[name="field.__product_space__"] option').selected = true
        resolve()
      }
    }))
    const started = createProjects({
      root: document.querySelector('#app'), api, waitForPreferences
    }).start()

    await vi.waitFor(() => expect(releasePreferences).toBeTypeOf('function'))
    expect(document.querySelector('[data-projects]').textContent).toContain('Apollo')
    releasePreferences()
    await started

    expect(api.getProjectFacts).toHaveBeenCalledOnce()
    expect([...document.querySelector('[name="field.__product_space__"]').selectedOptions].map(option => option.value)).toEqual(['DOPL'])
    expect(document.querySelectorAll('.project-card')).toHaveLength(1)
  })

  it('keeps project controls separate from the Weekly Review controls', async () => {
    const api = { getProjectFacts: vi.fn().mockResolvedValue(payload) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    expect([...document.querySelector('.filter-actions').querySelectorAll('button')].map(button => button.textContent)).toEqual([
      'Apply Filters', 'Cancel Sync', 'Reset'
    ])
    expect(document.querySelector('.weekly-review [data-audit]').textContent).toBe('Review Filters')
  })

  it('places catalog feedback above the filter controls', async () => {
    const api = { getProjectFacts: vi.fn().mockResolvedValue(payload) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    const form = document.querySelector('.report-filter-card')
    const status = form.querySelector('[role="status"]')

    expect(status).not.toBeNull()
    expect(status.compareDocumentPosition(form.querySelector('[data-main-facets]')) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })

  it('renders Weekly Review as a heading followed by one control row', async () => {
    const api = { getProjectFacts: vi.fn().mockResolvedValue(payload) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    const review = document.querySelector('.weekly-review')
    const controls = review.querySelector('.weekly-review-controls')

    expect(review.firstElementChild.classList).toContain('weekly-review-title')
    expect([...controls.children].map(item => item.textContent)).toEqual([
      'Start', 'End', 'Review Filters', 'Cancel Review', 'Download'
    ])
  })

  it('separates each project card summary from hover-revealed details', async () => {
    const projectWithSummaryFacts = { ...payload, projects: [{
      ...payload.projects[0], customer_summary: 'Customer A', fields: { ...payload.projects[0].fields, 'mp time': '2026-10-18' }
    }] }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(projectWithSummaryFacts) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    const card = document.querySelector('.project-card')
    const summary = card.querySelector('.project-card-summary')

    expect(summary.textContent).toContain('Apollo')
    expect(summary.textContent).toContain('DescriptionCustomer A')
    expect(summary.textContent).toContain('Project IDA-1')
    expect(summary.textContent).toContain('MP Time2026-10-18')
    expect(summary.textContent).not.toContain('Major FAE QA')
    expect(card.querySelector('.project-card-details').textContent).toContain('Major FAE QA')
    expect(card.querySelector('.project-card-details').textContent).toContain('Coco')
    expect(card.querySelector('.project-card-details').textContent).not.toContain('Customer A')
    expect(card.querySelector('.project-card-details').textContent).not.toContain('2026-10-18')
  })

  it('renders projects as horizontal list rows with a horizontal hover detail strip', async () => {
    const api = { getProjectFacts: vi.fn().mockResolvedValue(payload) }
    await createProjects({ root: document.querySelector('#app'), api }).start()

    const list = document.querySelector('.project-list')
    const row = list.querySelector('.project-list-row')
    expect(list).not.toBeNull()
    expect(row.querySelector('.project-list-primary').textContent).toContain('Apollo')
    expect(row.querySelector('.project-list-primary').textContent).not.toContain('A-1')
    expect(row.querySelector('.project-list-meta').textContent).toContain('Project IDA-1')
    expect(row.querySelector('.project-list-meta').textContent).not.toContain('China Operator')
    expect(row.querySelector('.project-list-meta').textContent).not.toContain('Unspecified')
    expect(row.querySelector('.project-card-badges').textContent).toContain('stale')
    expect(row.querySelector('.project-detail-strip').textContent).toContain('Major FAE QA')
    expect(row.querySelectorAll('.project-detail-item').length).toBeGreaterThan(0)
    expect([...row.querySelector('.project-card-summary').children].map(item => item.className)).toEqual([
      'project-list-primary', 'project-card-fact project-detail-item',
      'project-card-summary-facts project-list-meta', 'project-card-badges',
    ])
  })

  it('shows only the project name in the card title', async () => {
    const titled = { ...payload, projects: [{
      ...payload.projects[0], name: '1.* Apollo - Project Status Report'
    }] }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(titled) }
    await createProjects({ root: document.querySelector('#app'), api }).start()

    expect(document.querySelector('.project-card .kanban-card-title').textContent).toBe('Apollo')
  })

  it('shows WIFI Module in the project summary', async () => {
    const withWifiModule = { ...payload, projects: [{
      ...payload.projects[0], fields: { ...payload.projects[0].fields, 'wifi module': 'W2' },
    }] }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(withWifiModule) }

    await createProjects({ root: document.querySelector('#app'), api }).start()

    const wifiFact = [...document.querySelectorAll('.project-card-summary .project-detail-item')]
      .find(item => item.querySelector('.project-card-label')?.textContent === 'WIFI Module')
    expect(wifiFact?.querySelector('.project-card-value').textContent).toBe('W2')
    expect(document.querySelector('.project-card-details').textContent).not.toContain('WIFI Module')
  })

  it('preserves nonempty role values and uses Unknown only for empty members', async () => {
    const values = ['NA', 'N/A', '-', '<TBD>', 'TBD', 'fan.xu', 'Fan Xu']
    const roles = values.map(name => ({ name, identity: name }))
    roles.push({ name: ' ', identity: 'account-only' }, { name: ' ', identity: ' ' })
    await createProjects({ root: document.querySelector('#app'), api: {
      getProjectFacts: vi.fn().mockResolvedValue({ ...payload, projects: [{ ...payload.projects[0],
        roles: { 'Major FAE QA': roles } }] }),
    } }).start()
    const fact = [...document.querySelectorAll('.project-detail-item')]
      .find(item => item.querySelector('.project-card-label').textContent === 'Major FAE QA')
    expect(fact.querySelector('.project-card-value').textContent)
      .toBe([...values, 'account-only', 'Unknown'].join(', '))
  })

  it('keeps every declared preview attribute visible as Unknown when unavailable', async () => {
    const missing = { ...payload, projects: [{
      identity: 'DOPL:A-1', name: 'Apollo', page_url: '', space_key: 'DOPL',
      project_id: '', customer_summary: '', status: '', support_mode: '', fields: {}, roles: {},
    }] }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(missing) }

    await createProjects({ root: document.querySelector('#app'), api }).start()

    const card = document.querySelector('.project-card')
    const facts = Object.fromEntries([...card.querySelectorAll('.project-detail-item')].map(item => [
      item.querySelector('.project-card-label').textContent,
      item.querySelector('.project-card-value').textContent,
    ]))
    expect(facts).toMatchObject({
      Description: 'Unknown',
      'Project ID': 'Unknown',
      'MP Time': 'Unknown',
      'WIFI Module': 'Unknown',
      'Major FAE QA': 'Unknown',
      'FAE QA': 'Unknown',
      'QA Reviewer': 'Unknown',
    })
    expect([...card.querySelectorAll('.project-card-badges .badge')].map(item => item.textContent))
      .toEqual(['Unknown', 'Unknown'])
    expect(card.querySelector('.project-card-summary').textContent).not.toContain('Project Status')
    expect(card.querySelector('.project-card-summary').textContent).not.toContain('Support Mode')
  })

  it('links only project names that have a catalog page URL', async () => {
    const linked = { ...payload, projects: [
      { ...payload.projects[0], project_id: 'A-1', name: 'Apollo', page_url: 'https://confluence.example/pages/1' },
      { ...payload.projects[0], project_id: 'B-2', name: 'Blank URL', page_url: '' },
    ] }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(linked) }
    await createProjects({ root: document.querySelector('#app'), api }).start()

    const cards = [...document.querySelectorAll('.project-card')]
    const link = cards[0].querySelector('.project-card-link')
    expect(link.href).toBe('https://confluence.example/pages/1')
    expect(link.target).toBe('_blank')
    expect(link.rel).toBe('noopener noreferrer')
    const icon = link.querySelector('[aria-hidden="true"]')
    expect(icon).not.toBeNull()
    expect(link.firstElementChild).toBe(icon)
    expect(link.textContent).toBe('Apollo')
    expect(cards[1].querySelector('.kanban-card-title').textContent).toBe('Blank URL')
    expect(cards[1].querySelector('a')).toBeNull()
  })

  it('keeps matched projects visible when responsibility data is unavailable', async () => {
    const ownerless = { ...payload, projects: [{
      identity: 'DOPL:A-1', project_id: 'A-1', name: 'Apollo', space_key: 'DOPL',
      fields: { 'current stage': '2 IN DEVELOPMENT' }, roles: {}, responsibility_unavailable: true
    }], ownerHierarchy: [{ role: 'Major FAE QA', people: [] }, { role: 'FAE QA', people: [] }, { role: 'QA Reviewer', people: [] }] }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(ownerless) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    expect(document.querySelector('[data-count]').textContent).toBe('1 projects')
    expect(document.querySelector('[data-projects]').textContent).toContain('Apollo')
    expect(document.body.textContent).toContain('Project / Person / Field Search')
  })

  it('renders identity-free metrics and project cards without the old owner accordion', async () => {
    const api = { getProjectFacts: vi.fn().mockResolvedValue(payload) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    expect([...document.querySelectorAll('[data-metric] strong')].map(item => item.textContent)).toEqual([
      '1', '0', '0', '1', '0', 'S 0 · A 0', 'S 0 · A 0', 'S 0 · A 0', 'S 0 · A 0', '1.0',
    ])
    expect(document.querySelectorAll('details.owner-role, details.owner-person').length).toBe(0)
    expect(document.body.textContent).not.toContain('u-1')
    expect(document.querySelector('.project-card').textContent).toContain('Apollo')
  })

  it('shows canonical accessible projects separately from filtered matched projects', async () => {
    const api = { getProjectFacts: vi.fn().mockResolvedValue({
      ...payload, accessibleProjectCount: 651, blockProjectCount: 7, warningProjectCount: 10,
    }) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    const metrics = Object.fromEntries([...document.querySelectorAll('[data-metric-row]')].map(row => [
      row.querySelector('span').textContent, row.querySelector('strong').textContent,
    ]))
    expect([...document.querySelectorAll('[data-metric]')][0].querySelector('span').textContent).toBe('Support Projects')
    const blockRow = [...document.querySelectorAll('[data-metric-row]')]
      .find(row => row.querySelector('span').textContent === 'Block Projects')
    const warningRow = [...document.querySelectorAll('[data-metric-row]')]
      .find(row => row.querySelector('span').textContent === 'Warning Projects')
    expect(blockRow.closest('[data-metric]')).toBe(warningRow.closest('[data-metric]'))
    expect(blockRow.dataset.projectStatusText).toBe('BLOCK')
    expect(warningRow.dataset.projectStatusText).toBe('WARNING')
    expect(metrics['Block Projects']).toBe('7')
    expect(metrics['Warning Projects']).toBe('10')
    expect(metrics['Support Projects']).toBe('1')
    expect(metrics['Accessible projects']).toBeUndefined()
    expect(document.querySelector('.summary-definition')).toBeNull()
    expect(document.body.textContent).not.toContain('Metric definition')
  })

  it('renders every summary metric from an empty matched collection as zero', async () => {
    const api = { getProjectFacts: vi.fn().mockResolvedValue({
      ...payload, state: 'ready', blockProjectCount: 0, warningProjectCount: 0, projects: [], ownerHierarchy: [],
    }) }

    await createProjects({ root: document.querySelector('#app'), api }).start()

    expect([...document.querySelectorAll('[data-metric] strong')].map(item => item.textContent)).toEqual([
      '0', '0', '0', '0', '0', 'S 0 · A 0', 'S 0 · A 0', 'S 0 · A 0', 'S 0 · A 0', '0.0',
    ])
  })

  it('shows filtered S and A Support Mode counts for every product line', async () => {
    const project = (identity, spaceKey, supportMode) => ({
      identity, project_id: identity, name: identity, space_key: spaceKey, support_mode: supportMode,
      status: 'NORMAL', roles: {}, fields: {},
    })
    const api = { getProjectFacts: vi.fn().mockResolvedValue({
      ...payload, state: 'ready', ownerHierarchy: [], projects: [
        project('dopl-s-1', 'DOPL', 'S'), project('dopl-s-2', 'DOPL', 's'),
        project('dopl-a', 'DOPL', 'A'), project('tv-a', 'TV', 'A'),
      ],
    }) }

    await createProjects({ root: document.querySelector('#app'), api }).start()

    const rows = [...document.querySelectorAll('[data-product-mode-row]')].map(row => [
      row.querySelector('span').textContent, row.querySelector('strong').textContent,
    ])
    const modeCard = document.querySelector('.summary-metric-product-modes')
    expect(modeCard.firstElementChild.textContent).toBe('Support Mode')
    expect(modeCard.firstElementChild.dataset.metricTitle).toBe('')
    expect(rows).toEqual([
      ['China Operator', 'S 2 · A 1'],
      ['Smart Device', 'S 0 · A 0'],
      ['TV', 'S 0 · A 1'],
      ['Global Operator & STB', 'S 0 · A 0'],
    ])
    expect(document.body.textContent).not.toContain('Product lines')
  })

  it('counts unique project and Major QA assignment relationships', async () => {
    const projects = [{ project_id: 'A-1', identity: 'DOPL:A-1', space_key: 'DOPL', status: 'NORMAL',
      roles: { 'Major FAE QA': [
        { identity: 'alice', name: 'Alice' }, { identity: 'bob', name: 'Bob' }, { identity: 'alice', name: 'Alice' },
      ] } }, { project_id: 'A-2', identity: 'DOPL:A-2', space_key: 'DOPL', status: 'WARNING',
      roles: { 'Major FAE QA': [{ identity: 'alice', name: 'Alice' }] } }]
    const api = { getProjectFacts: vi.fn().mockResolvedValue({
      ...payload, projects, blockProjectCount: 0, warningProjectCount: 1, ownerHierarchy: [],
    }) }

    await createProjects({ root: document.querySelector('#app'), api }).start()

    const metrics = Object.fromEntries([...document.querySelectorAll('[data-metric]')].map(card => [
      card.querySelector('span').textContent, card.querySelector('strong').textContent,
    ]))
    expect(metrics['Major FAE QA Resources']).toBe('3')
  })

  it('counts Major FAE QA and FAE QA project-person relationships separately', async () => {
    const projects = [{ project_id: 'A-1', identity: 'DOPL:A-1', space_key: 'DOPL', status: 'NORMAL', roles: {
      'Major FAE QA': [{ identity: 'alice', name: 'Alice' }, { identity: 'alice', name: 'Alice' }],
      'FAE QA': [{ identity: 'alice', name: 'Alice' }, { identity: 'bob', name: 'Bob' }],
    } }, { project_id: 'A-2', identity: 'DOPL:A-2', space_key: 'DOPL', status: 'NORMAL', roles: {
      'Major FAE QA': [{ identity: 'alice', name: 'Alice' }],
      'FAE QA': [{ identity: 'bob', name: 'Bob' }],
    } }]
    const api = { getProjectFacts: vi.fn().mockResolvedValue({ ...payload, projects, ownerHierarchy: [] }) }

    await createProjects({ root: document.querySelector('#app'), api }).start()

    const metrics = Object.fromEntries([...document.querySelectorAll('[data-metric-row]')].map(row => [
      row.querySelector('span').textContent, row.querySelector('strong').textContent,
    ]))
    expect(metrics['Major FAE QA Resources']).toBe('2')
    expect(metrics['FAE QA Resources']).toBe('3')
    expect(metrics['Major QA Resources']).toBeUndefined()
  })

  it('keeps only the four supported filters', async () => {
    const complete = { ...payload, facets: [
      { key: '__product_space__', label: 'Product Space', options: ['DOPL'] },
      { key: 'date of commercial approval', label: 'Date of Commercial approval', options: [2025, 2026] },
      { key: 'project id', label: 'Project ID', options: ['A-1'] },
      { key: 'project status', label: 'Project Status', options: ['NORMAL'] },
      { key: 'current stage', label: 'Current Stage', options: ['Stage 1'] },
      { key: 'project owner', label: 'Project Owner', options: ['Alice'] },
      { key: 'support mode', label: 'Support Mode', options: ['A'] },
      { key: 'odm', label: 'ODM', options: ['ODM-X'] }
    ] }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(complete) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    expect([...document.querySelectorAll('[data-main-facets] > label')].map(row => row.firstChild.textContent)).toEqual([
      'Product Space', 'Date of Commercial approval', 'Project ID', 'Project Owner'
    ])
    expect(document.querySelector('[name="field.project status"]')).toBeNull()
    expect(document.querySelector('[name="field.current stage"]')).toBeNull()
    expect(document.querySelector('[name="field.project owner"]')).not.toBeNull()
    expect(document.querySelector('[name="field.support mode"]')).toBeNull()
    expect(document.querySelector('[name="field.odm"]')).toBeNull()
  })

  it('disables Apply while the local snapshot is loading', async () => {
    let resolve
    const api = { getProjectFacts: vi.fn().mockImplementationOnce(() => new Promise(done => { resolve = done })).mockResolvedValue(payload) }
    const started = createProjects({ root: document.querySelector('#app'), api }).start()
    expect(document.querySelector('[type="submit"]').disabled).toBe(true)
    resolve(payload); await started
    expect(document.querySelector('[type="submit"]').disabled).toBe(false)
  })

  it('keeps Apply available to acquire data when there is no local cache', async () => {
    const empty = {
      ...payload,
      state: 'no_snapshot', projects: [], ownerHierarchy: [],
      facets: payload.facets.map(facet => ({ ...facet, options: [] }))
    }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(empty) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    expect(document.querySelector('[data-audit]').disabled).toBe(true)
    expect(document.querySelector('[type="submit"]').disabled).toBe(false)
    expect(document.querySelector('[data-reset]').disabled).toBe(true)
    expect(document.querySelector('[name="search"]').disabled).toBe(true)
    expect([...document.querySelectorAll('[data-main-facets] select')].every(item => item.disabled)).toBe(true)
    expect([...document.querySelectorAll('[data-main-facets] .multi-select__control')].every(item => item.disabled)).toBe(true)
    expect([...document.querySelectorAll('[data-main-facets] .multi-select__summary')].every(item => !item.textContent.includes('Loading'))).toBe(true)
  })

  it('renders all four supported filters from the immediate loading payload then polls to ready', async () => {
    const loading = { ...payload, state: 'loading', projects: [], ownerHierarchy: [], facets: fixedFacets }
    const api = {
      getProjectFacts: vi.fn().mockResolvedValueOnce(loading).mockResolvedValueOnce(payload),
      getProjectFactsStatus: vi.fn().mockResolvedValue({ state: 'ready', completed: 0, total: 0 }),
    }
    let releasePoll
    const pollDelay = vi.fn(() => new Promise(resolve => { releasePoll = resolve }))
    const component = createProjects({ root: document.querySelector('#app'), api, pollDelay })
    await component.start()
    expect([...document.querySelectorAll('[data-main-facets] > label')].map(row => row.firstChild.textContent)).toEqual([
      'Product Space', 'Date of Commercial approval', 'Project ID', 'Project Owner'
    ])
    expect([...document.querySelectorAll('[data-main-facets] select')].every(item => item.disabled)).toBe(true)
    expect([...document.querySelectorAll('[data-main-facets] .multi-select__summary')].every(item => item.textContent === 'Loading…')).toBe(true)
    expect(document.querySelector('[role="status"]').textContent).toContain('Loading')
    releasePoll()
    await vi.waitFor(() => expect(api.getProjectFacts).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(document.querySelector('[type="submit"]').disabled).toBe(false))
    expect([...document.querySelectorAll('[data-main-facets] .multi-select__summary')]
      .every(item => !item.textContent.includes('Loading'))).toBe(true)
    expect(api.getProjectFacts.mock.calls[0][1]).toEqual({ snapshot: true })
    expect(api.getProjectFacts.mock.calls[1][1]).toEqual({ snapshot: true })
  })

  it('treats an empty completed catalog as ready and does not poll again', async () => {
    const readyEmpty = {
      ...payload, state: 'ready', projects: [], ownerHierarchy: [],
      facets: fixedFacets.map(facet => ({ ...facet, options: [] })),
    }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(readyEmpty) }
    const pollDelay = vi.fn(() => Promise.resolve())

    await createProjects({ root: document.querySelector('#app'), api, pollDelay }).start()

    expect(api.getProjectFacts).toHaveBeenCalledOnce()
    expect(api.getProjectFacts.mock.calls[0][1]).toEqual({ snapshot: true })
    expect(pollDelay).not.toHaveBeenCalled()
    expect(document.querySelector('[type="submit"]').disabled).toBe(false)
  })

  it('does not present catalog loading as a detail sync job', async () => {
    const loading = {
      ...payload, state: 'loading', projects: [], ownerHierarchy: [],
      facets: fixedFacets,
      sync: { state: 'loading', completed: 0, total: 0 },
    }
    const api = { getProjectFacts: vi.fn().mockResolvedValue(loading) }
    await createProjects({
      root: document.querySelector('#app'), api,
      pollDelay: () => new Promise(() => {}),
    }).start()

    expect(document.querySelector('[data-async-feedback]').hidden).toBe(true)
    expect(document.querySelector('[data-async-feedback]').dataset.state).toBe('idle')
    expect(document.querySelector('[data-cancel]').hidden).toBe(true)
    expect(document.querySelector('[role="status"]').textContent).toContain('Loading project catalog')
    expect(document.querySelector('[role="status"]').textContent).not.toContain('Syncing project details')
  })

  it('stops polling on failed state and when the component is destroyed', async () => {
    const loading = { ...payload, state: 'loading', projects: [], ownerHierarchy: [], facets: fixedFacets }
    const failed = { ...loading, state: 'failed' }
    const api = {
      getProjectFacts: vi.fn().mockResolvedValueOnce(loading).mockResolvedValueOnce(failed),
      getProjectFactsStatus: vi.fn().mockResolvedValue({ state: 'failed', completed: 0, total: 0 }),
    }
    const component = createProjects({ root: document.querySelector('#app'), api, pollDelay: () => Promise.resolve() })
    await component.start()
    await vi.waitFor(() => expect(document.querySelector('[role="status"]').textContent).toContain('failed'))
    expect([...document.querySelectorAll('[data-main-facets] .multi-select__summary')].every(item => !item.textContent.includes('Loading'))).toBe(true)
    expect(document.querySelector('[type="submit"]').disabled).toBe(false)
    expect(api.getProjectFacts).toHaveBeenCalledTimes(2)
    component.destroy()
    await Promise.resolve()
    expect(api.getProjectFacts).toHaveBeenCalledTimes(2)
  })

  it('ends catalog loading after a poll network failure and leaves Apply available to retry', async () => {
    const api = {
      getProjectFacts: vi.fn().mockResolvedValue({ ...payload, state: 'loading', facets: fixedFacets }),
      getProjectFactsStatus: vi.fn().mockRejectedValue(new Error('offline')),
      applyConfluenceFilterSnapshot: vi.fn().mockResolvedValue(payload),
    }
    const component = createProjects({ root: document.querySelector('#app'), api, pollDelay: () => Promise.resolve() })
    await component.start()
    await vi.waitFor(() => expect(document.querySelector('[role="status"]').textContent).toContain('unavailable'))
    expect(document.querySelector('[role="status"]').hidden).toBe(false)
    expect([...document.querySelectorAll('[data-main-facets] .multi-select__summary')].every(item => !item.textContent.includes('Loading'))).toBe(true)
    expect(document.querySelector('[type="submit"]').disabled).toBe(false)
    document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await vi.waitFor(() => expect(api.applyConfluenceFilterSnapshot).toHaveBeenCalledOnce())
    component.destroy()
  })

  it('shows Apply transport errors and enables an explicit retry', async () => {
    const api = {
      getProjectFacts: vi.fn().mockResolvedValue(payload),
      applyConfluenceFilterSnapshot: vi.fn().mockRejectedValue(new Error('offline')),
    }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await vi.waitFor(() => expect(document.querySelector('[role="status"]').textContent).toContain('apply failed'))
    expect(document.querySelector('[role="status"]').hidden).toBe(false)
    expect(document.querySelector('[type="submit"]').disabled).toBe(false)
  })

  it('polls catalog status without restarting catalog or requesting details', async () => {
    const partial = { ...payload, state: 'loading', sync: { state: 'loading', completed: 0, total: 0 } }
    const api = {
      getProjectFacts: vi.fn().mockResolvedValueOnce(partial).mockResolvedValueOnce(payload),
      getProjectFactsStatus: vi.fn().mockResolvedValueOnce({ state: 'loading', completed: 0, total: 0 })
        .mockResolvedValueOnce({ state: 'ready', completed: 0, total: 0 }),
    }
    const component = createProjects({ root: document.querySelector('#app'), api, pollDelay: () => new Promise(resolve => setTimeout(resolve, 1)) })

    await component.start()
    expect(document.querySelector('[name="field.__product_space__"]').disabled).toBe(true)
    expect(document.querySelector('[type="submit"]').disabled).toBe(true)
    await vi.waitFor(() => expect(api.getProjectFactsStatus).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(api.getProjectFacts).toHaveBeenCalledTimes(2))
    await vi.waitFor(() => expect(document.querySelector('[type="submit"]').disabled).toBe(false))
    expect(api.getProjectFacts.mock.calls.map(call => call[1])).toEqual([
      { snapshot: true }, { snapshot: true },
    ])
    component.destroy()
  })

  it('submits selected supported facets and project/person search', async () => {
    const api = { getProjectFacts: vi.fn().mockResolvedValue(payload) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    const form = document.querySelector('form')
    const productSpaceControl = form.elements['field.__product_space__']
    form.elements['field.__product_space__'].value = 'DOPL'
    form.elements.search.value = 'Coco'
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await vi.waitFor(() => expect(api.getProjectFacts).toHaveBeenCalledTimes(2))
    expect(form.elements['field.__product_space__']).toBe(productSpaceControl)
    expect(api.getProjectFacts.mock.calls[1][0]).toEqual({ fields: { '__product_space__': ['DOPL'] }, search: 'Coco' })
    expect(api.getProjectFacts.mock.calls[1][1]).toBeUndefined()
    expect(document.querySelector('[type="submit"]').disabled).toBe(false)
  })

  it('shows detail feedback only after Apply starts a real detail job', async () => {
    let resolveApply
    const api = { getProjectFacts: vi.fn().mockResolvedValueOnce(payload)
      .mockImplementationOnce(() => new Promise(resolve => { resolveApply = resolve })),
      cancelProjectSync: vi.fn() }
    await createProjects({
      root: document.querySelector('#app'), api,
      pollDelay: () => new Promise(() => {}),
    }).start()

    document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))

    expect(document.querySelector('[data-async-feedback]').hidden).toBe(true)
    expect(document.querySelector('[type="submit"]').disabled).toBe(true)
    resolveApply({ ...payload, sync: { state: 'loading', completed: 0, total: 1 } })
    await vi.waitFor(() => expect(document.querySelector('[data-async-feedback]').dataset.state).toBe('running'))
    expect(document.querySelector('[data-async-feedback] [role="progressbar"]').dataset.indeterminate).toBe('false')
    expect(document.querySelector('[data-cancel]').hidden).toBe(false)
  })

  it('shows Product Space labels while submitting keys and supports select all and clear', async () => {
    const api = { getProjectFacts: vi.fn().mockResolvedValue(payload) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    const select = document.querySelector('[name="field.__product_space__"]')
    const multi = select.nextElementSibling
    expect([...select.options].map(option => option.textContent)).toEqual(['China Operator', 'TV'])
    multi.querySelector('.multi-select__control').click()
    multi.querySelector('[data-select-all]').click()
    expect(api.getProjectFacts).toHaveBeenCalledOnce()
    expect(multi.querySelector('.multi-select__tags').textContent).toContain('China Operator+1')
    multi.querySelector('.multi-select__control').click()
    multi.querySelector('[data-clear]').click()
    expect(api.getProjectFacts).toHaveBeenCalledOnce()
    expect(multi.querySelector('.multi-select__summary').textContent).toBe('All Product Space')
  })

  it('keeps catalog facets fixed while Apply updates only downstream results', async () => {
    const filtered = { ...payload, facets: [{ key: '__product_space__', label: 'Product Space', options: [] }],
      projects: [], ownerHierarchy: [] }
    const api = { getProjectFacts: vi.fn().mockResolvedValueOnce(payload).mockResolvedValueOnce(filtered) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    const select = document.querySelector('[name="field.__product_space__"]')
    select.options[0].selected = true
    select.dispatchEvent(new Event('change', { bubbles: true }))
    expect(api.getProjectFacts).toHaveBeenCalledOnce()
    expect([...select.options].map(option => option.value)).toEqual(['DOPL', 'TV'])
    document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await vi.waitFor(() => expect(api.getProjectFacts).toHaveBeenCalledTimes(2))
    expect([...select.options].map(option => option.value)).toEqual(['DOPL', 'TV'])
    expect(document.querySelectorAll('.project-card')).toHaveLength(0)
  })

  it('preserves a valid applied date when a no-match response has no facet options', async () => {
    const dateFacet = { key: 'date of commercial approval', label: 'Date of Commercial approval', options: [2025] }
    const initial = { ...payload, state: 'ready', facets: [dateFacet] }
    const noMatch = {
      ...initial, facets: [{ ...dateFacet, options: [] }], projects: [], ownerHierarchy: [],
      blockProjectCount: 0, warningProjectCount: 0, sync: { state: 'ready', completed: 0, total: 0 },
    }
    const api = {
      getProjectFacts: vi.fn().mockResolvedValueOnce(initial)
        .mockResolvedValueOnce({ ...noMatch, sync: { state: 'loading', completed: 0, total: 0 } })
        .mockResolvedValueOnce(noMatch),
      getProjectFactsStatus: vi.fn().mockResolvedValue({ state: 'ready', completed: 0, total: 0 }),
    }
    await createProjects({ root: document.querySelector('#app'), api, pollDelay: () => Promise.resolve() }).start()
    const select = document.querySelector('[name="field.date of commercial approval"]')
    expect(document.querySelector('[data-audit-status]').textContent).not.toContain('已清除失效筛选')
    select.options[0].selected = true

    document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await vi.waitFor(() => expect(api.getProjectFacts).toHaveBeenCalledTimes(3))

    expect([...select.selectedOptions].map(option => option.value)).toEqual(['2025'])
    expect(document.querySelector('[data-audit-status]').textContent).not.toContain('已清除失效筛选')
  })

  it('Reset clears local values and restores the authorized catalog scope without details', async () => {
    const api = { getProjectFacts: vi.fn().mockResolvedValue(payload) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    const select = document.querySelector('[name="field.__product_space__"]')
    select.options[0].selected = true
    document.querySelector('[name="search"]').value = 'Coco'
    document.querySelector('[name="reviewStartDate"]').value = '2026-08-01'
    document.querySelector('[name="reviewEndDate"]').value = '2026-08-08'
    document.querySelector('[data-reset]').click()
    await vi.waitFor(() => expect(api.getProjectFacts).toHaveBeenCalledTimes(2))
    expect(api.resetConfluenceFilterSnapshot).toHaveBeenCalledOnce()
    expect([...select.selectedOptions]).toEqual([])
    expect(document.querySelector('[name="search"]').value).toBe('')
    expect(document.querySelector('[name="reviewStartDate"]').value).toBe('2026-08-01')
    expect(document.querySelector('[name="reviewEndDate"]').value).toBe('2026-08-08')
    expect([...select.options].map(option => option.value)).toEqual(['DOPL', 'TV'])
  })

  it('Reset restores authorized Product Space candidates after a narrowed catalog response', async () => {
    const narrowed = { ...payload, facets: payload.facets.map(facet => facet.key === '__product_space__'
      ? { ...facet, options: [{ value: 'DOPL', label: 'China Operator' }] }
      : facet) }
    const api = { getProjectFacts: vi.fn()
      .mockResolvedValueOnce(narrowed).mockResolvedValueOnce(payload) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    expect([...document.querySelector('[name="field.__product_space__"]').options].map(option => option.value)).toEqual(['DOPL'])
    expect([...document.querySelectorAll('[data-product-space-group]')]
      .map(group => group.querySelector('[data-product-space-toggle] strong').textContent)).toEqual(productSpaces.map(item => item.label))

    document.querySelector('[name="field.__product_space__"] option').selected = true
    document.querySelector('[name="search"]').value = 'filtered'
    document.querySelector('[data-reset]').click()
    await vi.waitFor(() => expect(api.getProjectFacts).toHaveBeenCalledTimes(2))

    const restored = document.querySelector('[name="field.__product_space__"]')
    expect([...restored.options].map(option => option.value)).toEqual(['DOPL', 'TV'])
    expect([...restored.selectedOptions]).toEqual([])
    expect(document.querySelector('[name="search"]').value).toBe('')
  })

  it('refreshes results when the applied detail sync completes', async () => {
    const syncing = { ...payload, sync: { state: 'loading', completed: 0, total: 1 } }
    const updated = { ...payload, sync: { state: 'ready', completed: 1, total: 1 },
      projects: [], ownerHierarchy: [] }
    const api = { getProjectFacts: vi.fn().mockResolvedValueOnce(payload)
      .mockResolvedValueOnce(syncing).mockResolvedValueOnce(updated),
      getProjectFactsStatus: vi.fn().mockResolvedValue({ state: 'ready', completed: 1, total: 1 }),
      cancelProjectSync: vi.fn() }
    await createProjects({ root: document.querySelector('#app'), api, pollDelay: () => Promise.resolve() }).start()
    document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await vi.waitFor(() => expect(document.querySelectorAll('.project-card')).toHaveLength(0))
    expect(document.querySelector('[type="submit"]').disabled).toBe(false)
  })

  it('does not apply a completed job after controls change and cancel keeps local results', async () => {
    let finishPoll
    const syncing = { ...payload, sync: { state: 'loading', completed: 0, total: 1 } }
    const api = { getProjectFacts: vi.fn().mockResolvedValueOnce(payload).mockResolvedValueOnce(syncing),
      getProjectFactsStatus: vi.fn().mockImplementationOnce(() => new Promise(resolve => { finishPoll = () => resolve({ state: 'ready', completed: 1, total: 1 }) })),
      cancelProjectSync: vi.fn().mockResolvedValue({ cancelled: true }) }
    await createProjects({ root: document.querySelector('#app'), api, pollDelay: () => Promise.resolve() }).start()
    document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await vi.waitFor(() => expect(finishPoll).toBeTypeOf('function'))
    document.querySelector('[name="search"]').value = 'changed'
    document.querySelector('[data-cancel]').click()
    await vi.waitFor(() => expect(api.cancelProjectSync).toHaveBeenCalledOnce())
    finishPoll()
    await Promise.resolve()
    expect(document.querySelector('[data-projects]').textContent).not.toContain('Old job result')
    expect(document.querySelector('[data-projects]').textContent).toContain('Apollo')
    expect(document.querySelector('[type="submit"]').disabled).toBe(false)
  })

  it('shows the complete Core Product Space names without a search control', async () => {
    const facets = payload.facets.map(facet => facet.key === '__product_space__'
      ? { ...facet, options: productSpaces }
      : facet)
    const api = { getProjectFacts: vi.fn().mockResolvedValue({ ...payload, facets }) }

    await createProjects({ root: document.querySelector('#app'), api }).start()

    const multi = document.querySelector('[name="field.__product_space__"]').nextElementSibling
    expect(multi.querySelector('input[type="search"]')).toBeNull()
    expect([...multi.querySelectorAll('.multi-select__option span')].map(node => node.textContent)).toEqual(productSpaces.map(item => item.label))
  })

  it('reviews the applied singleton snapshot and keeps an independent date window', async () => {
    const api = {
      getProjectFacts: vi.fn().mockResolvedValue(payload),
      createConfluenceAudit: vi.fn().mockResolvedValue({ auditId: 'a1', status: 'queued', stage: '', progress: { processed: 0, total: 0 } }),
      getConfluenceAudit: vi.fn()
        .mockResolvedValueOnce({ auditId: 'a1', status: 'running', stage: 'loading_versions', progress: { processed: 1, total: 8 } })
        .mockResolvedValueOnce({ auditId: 'a1', status: 'completed', stage: 'finalizing', progress: { processed: 1, total: 1 } }),
      exportConfluenceAudit: vi.fn().mockResolvedValue({ download: { id: 'd1', fileName: 'review.zip' } }),
      downloadUrl: id => '/api/downloads/' + id,
    }
    await createProjects({
      root: document.querySelector('#app'), api, pollDelay: () => Promise.resolve()
    }).start()
    document.querySelector('[name="reviewStartDate"]').value = '2026-08-17'
    document.querySelector('[name="reviewEndDate"]').value = '2026-08-24'
    document.querySelector('[name="search"]').value = 'changed before review'
    const projectFactCalls = api.getProjectFacts.mock.calls.length
    document.querySelector('[data-audit]').click()
    await vi.waitFor(() => expect(api.createConfluenceAudit).toHaveBeenCalledOnce())
    expect(api.getProjectFacts).toHaveBeenCalledTimes(projectFactCalls)
    expect(api.getProjectFacts.mock.calls.at(-1)).toEqual([
      { fields: {}, search: '' }, { snapshot: true }
    ])
    expect(api.createConfluenceAudit).toHaveBeenCalledWith({
      startDate: '2026-08-17', endDate: '2026-08-24'
    })
    await vi.waitFor(() => expect(document.querySelector('[data-audit-download]').disabled).toBe(false))
    expect(document.querySelector('[data-async-feedback]').dataset.state).toBe('success')
    expect(document.querySelector('[data-audit-download]').textContent).toBe('Download')
    expect(document.querySelector('[data-audit-findings]')).toBeNull()
  })

  it('uses one root feedback component and renders a delayed child as text without changing its progress scale', async () => {
    const api = {
      getProjectFacts: vi.fn().mockResolvedValueOnce(payload)
        .mockResolvedValueOnce({ ...payload, sync: {
          state: 'loading', completed: 9, total: 10,
          task: { state: 'running', progress: { processed: 2, total: 10 }, visibleChild: { label: 'Loading project A', state: 'running' } },
        } }),
      cancelProjectSync: vi.fn(),
    }
    await createProjects({ root: document.querySelector('#app'), api, pollDelay: () => new Promise(() => {}) }).start()
    document.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await vi.waitFor(() => expect(document.querySelector('[data-async-feedback]').dataset.state).toBe('running'))
    expect(document.querySelectorAll('.async-feedback')).toHaveLength(1)
    expect(document.querySelector('[data-async-feedback] [role="progressbar"]').getAttribute('aria-valuenow')).toBe('2')
    expect(document.querySelector('[data-async-feedback]').textContent).toContain('Loading project A')
  })

  it.each([
    ['no_snapshot', 'No local project snapshot'],
    ['schema_error', 'Local project snapshot is unreadable'],
    ['partial_success', 'Some project facts are stale or failed']
  ])('renders %s state', async (state, message) => {
    const api = { getProjectFacts: vi.fn().mockResolvedValue({ ...payload, state, projects: [] }) }
    await createProjects({ root: document.querySelector('#app'), api }).start()
    expect(document.querySelector('[role="status"]').textContent).toContain(message)
  })
})

it('keeps Projects titles themed without binding data groups to product backgrounds', async () => {
  const root = document.createElement('div')
  const page = createProjects({ root, api: { getProjectFacts: vi.fn().mockResolvedValue(payload) } })
  try {
    await page.start()
    const groups = [...root.querySelectorAll('[data-product-space-group]')]
    expect(groups.every(group => !group.hasAttribute('data-product-line') && !group.hasAttribute('data-product-surface'))).toBe(true)
    expect(groups.map(group => group.querySelector('[data-product-space-toggle]').dataset.productLine)).toEqual(['China Operator', 'Smart Device', 'TV', 'Global Operator & STB'])
    const toggle = groups[0].querySelector('[data-product-space-toggle]')
    expect(toggle.dataset.productLine).toBe('China Operator')
    expect(toggle.dataset.productSurface).toBe('button')
    expect(toggle.textContent).toContain('China Operator')
    expect(toggle.textContent).not.toContain('DOPL')
    toggle.click()
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(groups[0].querySelector('[data-product-grid]').hidden).toBe(true)
  } finally { page.destroy() }
})

it('keeps project rows plain without changing hover details or stage semantics', async () => {
  const root = document.createElement('div')
  const page = createProjects({ root, api: { getProjectFacts: vi.fn().mockResolvedValue({ ...payload,
    projects: [
      { identity: 'SD-1', project_id: 'SD-1', name: 'Speaker One', space_key: 'SDPL', status: 'NORMAL', stage: '2 IN DEVELOPMENT', page_url: 'https://example.com/speaker', roles: {}, fields: { owner: 'Alice' } },
      { identity: 'TV-1', project_id: 'TV-1', name: 'TV One', space_key: 'TV', status: 'WARNING', stage: '2 IN DEVELOPMENT', roles: {}, fields: {} },
    ],
  }) } })
  try {
    await page.start()
    const speaker = root.querySelector('[data-project-id="SD-1"]')
    const tv = root.querySelector('[data-project-id="TV-1"]')
    expect([speaker, tv].every(row => !row.hasAttribute('data-product-line') && !row.hasAttribute('data-product-surface'))).toBe(true)
    expect(speaker.tagName).toBe('ARTICLE')
    expect(speaker.querySelector('.project-card-details').textContent).toContain('Alice')
    expect(speaker.querySelector('a').href).toBe('https://example.com/speaker')
    expect(speaker.querySelector('[data-project-status]').dataset.projectStatus).toBe('NORMAL')
    const stage = speaker.closest('details')
    expect(stage.open).toBe(false)
    expect(stage.querySelector('summary').dataset.projectStage).toBe('2 IN DEVELOPMENT')
    expect(stage.hasAttribute('data-product-surface')).toBe(false)
  } finally { page.destroy() }
})
