// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createJiraStatisticsCard, JIRA_STATISTICS_CARDS } from '../src/widgets/jira-statistics-card.js'

it('enables Comments and Verify together while Basic is pending', async () => {
  document.body.innerHTML = '<div id="root"></div>'
  const payload = { state: 'loading', current: { productLines: [{ id: 'TV', label: 'TV', people: [
    { identity: 'qa', displayName: 'QA', commentCount: 5, verifyCount: 3 }] }] }, previous: { productLines: [] },
    availability: { current: { basic: false, activity: true }, previous: { basic: false, activity: false } } }
  const chartFactory = vi.fn(chartMock)
  const widget = createJiraStatisticsCard({ pollDelay: 60000, chartFactory })
  widget.mount(document.querySelector('#root'), { api: { getStatistics: async () => payload }, account: 'comments' })
  const comments = () => document.querySelector('[data-mode-segments] button[data-value="commentCount"]')
  await vi.waitFor(() => expect(comments()?.disabled).toBe(false))
  expect(document.querySelector('[data-mode-segments] button[data-value="bugCount"]').disabled).toBe(true)
  expect(document.querySelector('[data-mode-segments] button[data-value="verifyCount"]').disabled).toBe(false)
  comments().click()
  expect(chartFactory.mock.calls.at(-1)[1].data.datasets[0].data).toEqual([5])
  widget.destroy()
})

const productLines = [
  { id: 'China Operator', label: 'China Operator', people: [
    { identity: 'a', displayName: 'Alice', bugCount: 2, commentCount: 1, verifyCount: 0, invalidCount: 1 },
    { identity: 'b', displayName: 'Bob', bugCount: 3, commentCount: 0, verifyCount: 2, invalidCount: 0 },
  ] },
  { id: 'Smart Device', label: 'Smart Device', people: [] },
  { id: 'TV', label: 'TV', people: [] },
  { id: 'Global Operator & STB', label: 'Global Operator & STB', people: [] },
  { id: 'Wireless Connection', label: 'Wireless Connection', people: [
    { identity: 'w', displayName: 'WiFi Owner', bugCount: 4, commentCount: 2, verifyCount: 1, invalidCount: 0 },
  ] },
]

function chartMock(_canvas, config) {
  return {
    get data() { return config.data }, set data(value) { config.data = value },
    get options() { return config.options }, set options(value) { config.options = value },
    update: vi.fn(), reset: vi.fn(), stop: vi.fn(), destroy: vi.fn(),
  }
}

function mount(api, chartFactory = vi.fn(chartMock), account = 'coco', cardKey = 'self-test') {
  document.body.innerHTML = '<div id="root"></div>'
  const widget = createJiraStatisticsCard({ pollDelay: 0, chartFactory })
  widget.mount(document.querySelector('#root'), { api, account, cardKey })
  return { widget, chartFactory }
}

describe('Jira statistics card', () => {
  it('declares three cards with shared component metadata', () => {
    expect(Object.keys(JIRA_STATISTICS_CARDS)).toEqual(['self-test', 'task', 'customer-feedback'])
    expect(Object.values(JIRA_STATISTICS_CARDS).map(card => card.title)).toEqual([
      'Self-Test Jira Statistics by Product Line',
      'Task Jira Statistics by Product Line',
      'Customer Feedback Jira Statistics by Product Line',
    ])
    expect(JIRA_STATISTICS_CARDS.task.firstMetricLabel).toBe('Tasks')
    expect(JIRA_STATISTICS_CARDS['customer-feedback'].title).toContain('Customer Feedback')
  })
  it('restores a card period and queries only when its own period is changed', async () => {
    const api = { getStatistics: vi.fn().mockResolvedValue({ state: 'ready', productLines, period: 'quarter' }),
      queryStatistics: vi.fn().mockResolvedValue({ state: 'ready', productLines, period: 'week' }) }
    const { widget } = mount(api)
    await vi.waitFor(() => expect(document.querySelector('[data-jira-period="quarter"]').checked).toBe(true))
    const periods = document.querySelector('[data-jira-periods]')
    expect(periods.previousElementSibling.matches('.report-preview-toolbar')).toBe(true)
    expect(periods.parentElement).not.toBe(document.querySelector('[data-product-line-segments]').parentElement)
    document.querySelector('[data-jira-period="week"]').click()
    await vi.waitFor(() => expect(api.queryStatistics).toHaveBeenCalledWith('week', 'reuse'))
    expect(document.querySelector('[data-jira-period="week"]').checked).toBe(true)
    widget.update({ query: true })
    await vi.waitFor(() => expect(api.queryStatistics).toHaveBeenCalledWith(undefined, 'refresh'))
    widget.destroy()
  })
  it('keeps radio identities independent across card instances and associates visible labels', () => {
    document.body.innerHTML = '<div id="first"></div><div id="second"></div>'
    const api = { getStatistics: () => new Promise(() => {}) }
    const first = createJiraStatisticsCard(), second = createJiraStatisticsCard()
    first.mount(document.querySelector('#first'), { api, account: 'coco', cardKey: 'self-test' })
    second.mount(document.querySelector('#second'), { api, account: 'coco', cardKey: 'task' })
    const firstInputs = [...document.querySelectorAll('#first input')]
    const secondInputs = [...document.querySelectorAll('#second input')]
    expect(firstInputs).toHaveLength(4)
    expect(new Set([...firstInputs, ...secondInputs].map(input => input.id)).size).toBe(8)
    expect(firstInputs[0].name).not.toBe(secondInputs[0].name)
    expect(firstInputs.map(input => input.labels[0].textContent)).toEqual(['Weekly', 'Monthly', 'Quarterly', 'Yearly'])
    firstInputs[0].labels[0].click()
    expect(firstInputs[0].checked).toBe(true)
    expect(secondInputs[1].checked).toBe(true)
    first.destroy(); second.destroy()
  })
  it('does not render aggregate summary cards while retaining aggregate values in its disposable display', async () => {
    const api = { getStatistics: vi.fn().mockResolvedValue({
      state: 'ready', productLines, teamTotal: 12, unmappedCount: 2, unassignedCount: 1,
    }) }
    const { widget } = mount(api)
    await vi.waitFor(() => expect(document.querySelector('[data-ranked-chart]')).not.toBeNull())
    expect(document.querySelector('[data-jira-statistics-summary]')).toBeNull()
    expect(document.body.textContent).not.toContain('Current total')
    expect(document.body.textContent).not.toContain('Previous total')
    expect(document.body.textContent).not.toContain('Unmapped projects')
    expect(document.body.textContent).not.toContain('Missing creators')
    const cached = JSON.parse(sessionStorage.getItem('smarttest:jira-statistics-display:coco:self-test'))
    expect(cached.unmappedCount).toBe(2)
    expect(cached.productLines).toHaveLength(5)
    widget.destroy()
  })
  it('never renders query JQL and keeps dimension clicks presentation-only', async () => {
    const api = { getStatistics: vi.fn().mockResolvedValue({ state: 'ready', productLines,
      query: { activeJql: 'aggregate query' } }) }
    const { widget } = mount(api, undefined, 'coco', 'task')
    await vi.waitFor(() => expect(document.querySelector('[data-ranked-chart]')).not.toBeNull())
    expect(document.body.textContent).not.toContain('aggregate query')
    document.querySelector('[data-product-line-segments] button[data-value="TV"]').click()
    document.querySelector('[data-mode-segments] button[data-value="verifyCount"]').click()
    expect(api.getStatistics).toHaveBeenCalledTimes(1)
    widget.destroy()
  })
  beforeEach(() => { sessionStorage.clear() })

  it('stops polling after cancellation without removing its last display', async () => {
    sessionStorage.setItem('smarttest:jira-statistics-display:coco:self-test', JSON.stringify({ state: 'ready', productLines }))
    const api = { getStatistics: vi.fn().mockResolvedValue({ state: 'cancelled', error: 'Query cancelled.' }) }
    const { widget, chartFactory } = mount(api)
    await vi.waitFor(() => expect(document.body.textContent).toContain('Query cancelled.'))
    await new Promise(resolve => setTimeout(resolve, 20))
    expect(api.getStatistics).toHaveBeenCalledTimes(1)
    expect(chartFactory.mock.calls.at(-1)[1].data.datasets[0].data).toEqual([3, 2])
    widget.destroy()
  })

  it('reuses the same card display on any page and calibrates it from SQLite', async () => {
    sessionStorage.setItem('smarttest:jira-statistics-display:coco:self-test', JSON.stringify({ state: 'ready', productLines }))
    document.body.innerHTML = '<div id="root"></div>'
    const chartFactory = vi.fn(chartMock)
    const api = { getStatistics: vi.fn().mockResolvedValue({ state: 'no_snapshot' }) }
    const widget = createJiraStatisticsCard({ chartFactory })
    widget.mount(document.querySelector('#root'), { api, account: 'coco' })
    expect(chartFactory).toHaveBeenCalledOnce()
    await vi.waitFor(() => expect(document.body.textContent).toContain('Apply a Jira query'))
    api.getStatistics.mockResolvedValue({ state: 'ready', productLines })
    widget.update()
    await vi.waitFor(() => expect(chartFactory).toHaveBeenCalledTimes(2))
    expect(sessionStorage.getItem('smarttest:jira-statistics-display:coco:self-test')).not.toBeNull()
    widget.destroy()
  })

  it('replays disposable display immediately and calibrates it from the SQLite response', async () => {
    sessionStorage.setItem('smarttest:jira-statistics-display:coco:self-test', JSON.stringify({ state: 'ready', productLines }))
    let resolve
    const api = { getStatistics: () => new Promise(done => { resolve = done }) }
    const { widget, chartFactory } = mount(api)
    expect(chartFactory.mock.calls.at(-1)[1].data.datasets[0].data).toEqual([3, 2])
    resolve({ state: 'ready', productLines: [{ ...productLines[0], people: [{ ...productLines[0].people[0], bugCount: 8 }] }] })
    await vi.waitFor(() => expect(chartFactory.mock.calls.at(-1)[1].data.datasets[0].data).toEqual([8]))
    const stored = JSON.parse(sessionStorage.getItem('smarttest:jira-statistics-display:coco:self-test'))
    expect(stored.productLines[0].people[0]).not.toHaveProperty('identity')
    expect(stored).not.toHaveProperty('task')
    widget.destroy()
  })

  it('retains a displayed graph during refresh, polls, and replaces it in place when fresh', async () => {
    sessionStorage.setItem('smarttest:jira-statistics-display:coco:self-test', JSON.stringify({ state: 'ready', productLines }))
    let resolveFresh
    const api = { getStatistics: vi.fn()
      .mockResolvedValueOnce({ state: 'loading', task: null, productLines })
      .mockImplementationOnce(() => new Promise(done => { resolveFresh = done })) }
    const { widget, chartFactory } = mount(api)
    await vi.waitFor(() => expect(resolveFresh).toBeTypeOf('function'))
    expect(document.querySelector('[data-ranked-chart]')).not.toBeNull()
    expect(document.body.textContent).toContain('Loading Jira')
    expect(chartFactory).toHaveBeenCalledOnce()
    resolveFresh({ state: 'ready', productLines: [{ ...productLines[0], people: [{ ...productLines[0].people[0], bugCount: 7 }] }] })
    await vi.waitFor(() => expect(chartFactory.mock.calls.at(-1)[1].data.datasets[0].data).toEqual([7]))
    expect(document.querySelector('[data-jira-statistics-state]')).toBeNull()
    widget.destroy()
  })

  it('keeps the graph and reports a background failure instead of clearing it', async () => {
    sessionStorage.setItem('smarttest:jira-statistics-display:coco:self-test', JSON.stringify({ state: 'ready', productLines }))
    const { widget } = mount({ getStatistics: async () => ({ state: 'failed', error: 'jira_search_failed' }) })
    await vi.waitFor(() => expect(document.body.textContent).toContain('jira_search_failed'))
    expect(document.querySelector('[data-ranked-chart]')).not.toBeNull()
    widget.destroy()
  })

  it('does not reuse another account display and shows loading only on a first empty visit', () => {
    sessionStorage.setItem('smarttest:jira-statistics-display:alice:self-test', JSON.stringify({ state: 'ready', productLines }))
    const { widget, chartFactory } = mount({ getStatistics: () => new Promise(() => {}) }, undefined, 'bob')
    expect(chartFactory).not.toHaveBeenCalled()
    expect(document.body.textContent).toContain('Loading Jira')
    widget.destroy()
  })
  it('switches four metrics and product lines with sorted horizontal ranking data', async () => {
    const api = { getStatistics: vi.fn().mockResolvedValue({ state: 'ready', teamTotal: 5, productLines }) }
    const { widget, chartFactory } = mount(api)
    await vi.waitFor(() => expect(chartFactory).toHaveBeenCalled())
    expect(document.querySelector('[data-product-line-segments] button').textContent).toBe('China Operator')
    const bugsChart = chartFactory.mock.calls.at(-1)[1]
    expect(bugsChart.data.labels).toEqual(['Bob', 'Alice'])
    expect(bugsChart.data.datasets[0].data).toEqual([3, 2])
    expect(bugsChart.options.plugins.datalabels.labels.percentage.formatter(3)).toBe('3 · 60%')
    expect(bugsChart.options.plugins.datalabels.labels.value.formatter(3)).toBeNull()
    expect(bugsChart.options.plugins.datalabels.labels.value.align).toBe('right')
    ;[...document.querySelectorAll('[data-mode-segments] button')].find(button => button.textContent === 'Verify').click()
    expect(chartFactory.mock.results[0].value.destroy).not.toHaveBeenCalled()
    expect(chartFactory.mock.results[0].value.reset).toHaveBeenCalledOnce()
    expect(chartFactory.mock.calls.at(-1)[1].data.labels).toEqual(['Bob'])
    expect(chartFactory.mock.calls.at(-1)[1].data.datasets[0].data).toEqual([2])
    ;[...document.querySelectorAll('[data-product-line-segments] button')].find(button => button.textContent === 'Smart Device').click()
    expect(document.querySelector('[data-ranked-empty]').hidden).toBe(false)
    expect(document.body.textContent).not.toContain('%')
    expect(document.querySelector('table')).toBeNull()
    ;[...document.querySelectorAll('[data-product-line-segments] button')].find(button => button.textContent === 'Wireless Connection').click()
    expect(chartFactory.mock.calls.at(-1)[1].data.labels).toEqual(['WiFi Owner'])
    widget.destroy()
    expect(chartFactory.mock.results[1].value.destroy).toHaveBeenCalledOnce()
  })

  it('groups current and previous values with readable comparison labels and ranges', async () => {
    const previousLines = productLines.map(line => ({ ...line, people: line.id === 'China Operator' ? [
      { identity: 'a', displayName: 'Alice', bugCount: 5, commentCount: 2, verifyCount: 0, invalidCount: 0 },
      { identity: 'c', displayName: 'Carol', bugCount: 4, commentCount: 1, verifyCount: 0, invalidCount: 0 },
    ] : [] }))
    const api = { getStatistics: vi.fn().mockResolvedValue({ state: 'ready', period: 'month',
      ranges: { current: { start: '2026-09-01', end: null }, previous: { start: '2026-08-01', end: '2026-09-01' } },
      current: { teamTotal: 5, productLines, unmappedCount: 0, unassignedCount: 0 },
      previous: { teamTotal: 9, productLines: previousLines, unmappedCount: 0, unassignedCount: 0 },
    }) }

    const { widget, chartFactory } = mount(api)
    await vi.waitFor(() => expect(chartFactory).toHaveBeenCalled())
    const chart = chartFactory.mock.calls.at(-1)[1]
    expect(chart.data.labels).toEqual(['Bob', 'Alice', 'Carol'])
    expect(chart.data.datasets.map(dataset => dataset.data)).toEqual([[3, 2, 0], [0, 5, 4]])
    expect(chart.data.datasets.map(dataset => dataset.barThickness)).toEqual([12, 12])
    expect(chart.data.datasets.every(dataset => dataset.grouped)).toBe(true)
    expect(chart.options.plugins.datalabels.labels.value.formatter(2, { dataIndex: 1, datasetIndex: 0 })).toBe('↓ 3')
    expect(chart.options.plugins.datalabels.labels.value.formatter(5, { dataIndex: 1, datasetIndex: 1 })).toBeNull()
    expect(chart.options.plugins.tooltip.callbacks.afterBody([{ dataIndex: 1 }])).toContain('Current: 2026-09-01 – now')
    expect(chart.options.plugins.tooltip.callbacks.afterBody([{ dataIndex: 1 }])).toContain('Previous: 2026-08-01 – 2026-09-01')
    widget.destroy()
  })

  it('polls loading state and renders explicit failure', async () => {
    const api = { getStatistics: vi.fn()
      .mockResolvedValueOnce({ state: 'loading', task: { progress: { processed: 2, total: 10 } } })
      .mockResolvedValueOnce({ state: 'failed', error: 'jira_search_failed' }) }
    mount(api)
    await vi.waitFor(() => expect(document.body.textContent).toContain('jira_search_failed'))
    expect(api.getStatistics).toHaveBeenCalledTimes(2)
  })
})


it.each(['self-test', 'task', 'customer-feedback'])('shows Comments instead of Resolved for %s', async cardKey => {
  const api = { getStatistics: vi.fn().mockResolvedValue({ state: 'ready', productLines: [{ id: 'TV', label: 'TV', people: [
    { identity: 'creator', displayName: 'Creator', bugCount: 2, commentCount: 0, verifyCount: 1, invalidCount: 0 },
    { identity: 'author', displayName: 'Author', bugCount: 0, commentCount: 3, verifyCount: 0, invalidCount: 0 },
  ] }] }) }
  const { widget, chartFactory } = mount(api, vi.fn(chartMock), 'coco', cardKey)
  await vi.waitFor(() => expect(document.querySelector('[data-mode-segments] button')).not.toBeNull())
  expect([...document.querySelectorAll('[data-mode-segments] button')].map(button => button.textContent)).not.toContain('Resolved')
  const comments = [...document.querySelectorAll('button')].find(button => button.textContent === 'Comments')
  expect(comments).toBeTruthy()
  comments.click()
  const chart = chartFactory.mock.calls.at(-1)[1]
  expect(chart.data.labels).toEqual(['Author'])
  expect(chart.data.datasets[0].data).toEqual([3])
  widget.destroy()
})

it('shows Customer components and current Invalid bars and restores both from display cache', async () => {
  const current = { productLines: [{ ...productLines[0], issueCount: 5, invalidCount: 1,
    components: [{ name: 'Audio', count: 4 }, { name: 'Video', count: 3 }] },
    { ...productLines[1], issueCount: 0, invalidCount: 0, components: [] },
    { ...productLines[4], issueCount: 17, invalidCount: 0, components: [] }] }
  const previous = { productLines: [{ ...productLines[0], issueCount: 10, invalidCount: 4,
    components: [{ name: 'Audio', count: 2 }, { name: 'Camera', count: 1 }] }] }
  const api = { getStatistics: async () => ({ state: 'ready', current, previous }) }
  let mounted = mount(api, undefined, 'customer', 'customer-feedback')
  await vi.waitFor(() => expect(mounted.chartFactory).toHaveBeenCalled())
  const inspect = () => {
    expect(document.querySelector('[data-mode-segments] button').textContent).toBe('Component/s')
    const config = mounted.chartFactory.mock.calls.at(-1)[1]
    expect(config.data.labels).toEqual(['Audio', 'Video', 'Camera'])
    expect(config.data.datasets.map(item => item.data)).toEqual([[4, 3, 0], [2, 0, 1]])
    document.querySelector('[data-value="invalidCount"]').click()
    expect(document.querySelector('[data-ranked-chart]').hidden).toBe(false)
    expect(config.data.labels).toEqual(['Total', 'Invalid'])
    expect(config.data.datasets.map(item => item.data)).toEqual([[5, 1]])
    expect(config.options.plugins.datalabels.labels.percentage.formatter(5, { dataIndex: 0 })).toBe('5')
    expect(config.options.plugins.datalabels.labels.percentage.formatter(1, { dataIndex: 1 })).toBe('1 · 20%')
    document.querySelector('[data-product-line-segments] button[data-value="Smart Device"]').click()
    expect(document.querySelector('[data-ranked-chart]').hidden).toBe(false)
    expect(config.data.labels).toEqual(['Total', 'Invalid'])
    expect(config.data.datasets.map(item => item.data)).toEqual([[0, 0]])
    expect(config.options.plugins.datalabels.labels.percentage.formatter(0, { dataIndex: 1 })).toBe('0 · 0%')
    document.querySelector('[data-product-line-segments] button[data-value="Wireless Connection"]').click()
    expect(config.data.labels).toEqual(['Total', 'Invalid'])
    expect(config.data.datasets.map(item => item.data)).toEqual([[17, 0]])
    expect(config.options.plugins.datalabels.labels.percentage.formatter(0, { dataIndex: 1 })).toBe('0 · 0%')
    document.querySelector('[data-product-line-segments] button[data-value="China Operator"]').click()
    document.querySelector('[data-value="commentCount"]').click()
    expect(config.data.labels).toEqual(['Alice'])
    document.querySelector('[data-value="verifyCount"]').click()
    expect(config.data.labels).toEqual(['Bob'])
  }
  inspect()
  mounted.widget.destroy()
  mounted = mount({ getStatistics: () => new Promise(() => {}) }, undefined, 'customer', 'customer-feedback')
  inspect()
  mounted.widget.destroy()
})

it('does not render obsolete Customer personnel cache as component or Invalid statistics', () => {
  sessionStorage.setItem('smarttest:jira-statistics-display:legacy:customer-feedback', JSON.stringify({ state: 'ready', productLines }))
  const { widget, chartFactory } = mount({ getStatistics: () => new Promise(() => {}) }, undefined, 'legacy', 'customer-feedback')
  expect(chartFactory).not.toHaveBeenCalled()
  expect(document.querySelector('[data-mode-segments] button').disabled).toBe(true)
  expect(document.body.textContent).toContain('Loading Jira')
  widget.destroy()
})


it('renders Verify authors instead of P0', async () => {
  const api = { getStatistics: vi.fn().mockResolvedValue({ state: 'ready', productLines: [{ id: 'TV', label: 'TV', people: [
    { identity: 'verifier', displayName: 'Verifier', bugCount: 0, commentCount: 0, verifyCount: 2, invalidCount: 0 },
  ] }] }) }
  const { widget, chartFactory } = mount(api)
  await vi.waitFor(() => expect([...document.querySelectorAll('[data-mode-segments] button')].map(button => button.textContent)).toContain('Verify'))
  const buttons = [...document.querySelectorAll('[data-mode-segments] button')]
  expect(buttons.map(button => button.textContent)).not.toContain('P0')
  buttons.find(button => button.textContent === 'Verify').click()
  expect(chartFactory.mock.calls.at(-1)[1].data.labels).toEqual(['Verifier'])
  expect(chartFactory.mock.calls.at(-1)[1].data.datasets[0].data).toEqual([2])
  widget.destroy()
})


it('renders available layers without treating pending Verify or previous period as zero', async () => {
  const line = count => [{ id: 'TV', label: 'TV', people: [{ identity: 'qa', displayName: 'QA', bugCount: 2, commentCount: 1, verifyCount: count, invalidCount: 0 }] }]
  let payload = { state: 'loading', current: { productLines: line(undefined) }, previous: { productLines: line(undefined) },
    availability: { current: { basic: true, activity: false }, previous: { basic: false, activity: false } } }
  const chartFactory = vi.fn(chartMock)
  const widget = createJiraStatisticsCard({ pollDelay: 60000, chartFactory })
  document.body.innerHTML = '<div id="root"></div>'
  widget.mount(document.querySelector('#root'), { api: { getStatistics: async () => payload }, account: 'layers' })
  await vi.waitFor(() => expect(chartFactory).toHaveBeenCalled())
  const verify = () => document.querySelector('[data-mode-segments] button[data-value="verifyCount"]')
  expect(verify().disabled).toBe(true)
  expect(verify().textContent).toContain('Loading')
  expect(chartFactory.mock.calls.at(-1)[1].data.datasets).toHaveLength(1)
  payload = { ...payload, state: 'failed' }
  widget.update()
  await vi.waitFor(() => expect(verify().textContent).toContain('Failed'))
  expect(verify().disabled).toBe(true)
  payload = { ...payload, state: 'loading', current: { productLines: line(3) },
    availability: { current: { basic: true, activity: true }, previous: { basic: true, activity: false } } }
  widget.update()
  await vi.waitFor(() => expect(verify().disabled).toBe(false))
  expect(verify().textContent).toContain('Previous: Loading')
  verify().click()
  expect(chartFactory.mock.calls.at(-1)[1].data.datasets).toHaveLength(1)
  expect(chartFactory.mock.calls.at(-1)[1].data.datasets[0].data).toEqual([3])
  payload = { ...payload, state: 'ready', previous: { productLines: line(1) },
    availability: { current: { basic: true, activity: true }, previous: { basic: true, activity: true } } }
  widget.update()
  await vi.waitFor(() => expect(chartFactory.mock.calls.at(-1)[1].data.datasets).toHaveLength(2))
  expect(chartFactory.mock.calls.at(-1)[1].data.datasets[1].data).toEqual([1])
  widget.destroy()
})
it('keeps the initial loading skeleton and fills its existing nodes in place', async () => {
  let payload = { state: 'loading', task: { progress: { processed: 2, total: 10 } },
    productLines: productLines.map(line => ({ ...line, people: [] })),
    availability: { current: { basic: false, activity: false }, previous: { basic: false, activity: false } } }
  document.body.innerHTML = '<div id="root"></div>'
  const chartFactory = vi.fn(chartMock)
  const widget = createJiraStatisticsCard({ pollDelay: 60000, chartFactory })
  widget.mount(document.querySelector('#root'), { account: 'initial-loading', api: { getStatistics: async () => payload } })
  const canvas = document.querySelector('[data-ranked-chart]')
  expect(canvas).not.toBeNull()
  const surface = document.querySelector('.workload-chart-scroll')
  await vi.waitFor(() => expect(document.querySelectorAll('[data-product-line-segments] button')).toHaveLength(5))
  const product = document.querySelector('[data-product-line-segments] button')
  const metric = document.querySelector('[data-mode-segments] button')
  expect(metric.disabled).toBe(true)
  expect(surface.textContent).toContain('2/10')
  payload = { state: 'loading', task: { progress: { processed: 3, total: 10 } } }
  widget.update()
  await vi.waitFor(() => expect(surface.textContent).toContain('3/10'))
  expect(document.querySelector('[data-ranked-chart]')).toBe(canvas)
  expect(document.querySelector('[data-product-line-segments] button')).toBe(product)
  payload = { state: 'ready', productLines }
  widget.update()
  await vi.waitFor(() => expect(chartFactory).toHaveBeenCalledOnce())
  expect(document.querySelector('[data-ranked-chart]')).toBe(canvas)
  expect(document.querySelector('.workload-chart-scroll')).toBe(surface)
  expect(document.querySelector('[data-mode-segments] button')).toBe(metric)
  expect(document.querySelector('[data-product-line-segments] button')).toBe(product)
  expect(metric.disabled).toBe(false)
  widget.destroy()
})

it('binds the five Jira visual identities using full names', async () => {
  const { widget } = mount({ getStatistics: vi.fn().mockResolvedValue({ state: 'ready', productLines }) })
  try {
    await vi.waitFor(() => expect(document.querySelectorAll('[data-product-line-segments] button')).toHaveLength(5))
    const buttons = [...document.querySelectorAll('[data-product-line-segments] button')]
    expect(buttons.map(button => button.dataset.productLine)).toEqual(['China Operator', 'Smart Device', 'TV', 'Global Operator & STB', 'Wireless Connection'])
    buttons[4].click()
    expect(document.querySelector('.workload-chart-scroll').dataset.productLine).toBe('Wireless Connection')
  } finally { widget.destroy() }
})
