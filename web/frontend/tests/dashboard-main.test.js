// @vitest-environment jsdom

import { beforeEach, describe, expect, it, vi } from 'vitest'

const state = vi.hoisted(() => ({ authenticated: null, dashboard: null }))

vi.mock('../src/authenticated-page.js', () => ({
  startAuthenticatedPage: vi.fn(options => { state.authenticated = options }),
}))
vi.mock('../src/dashboard/dashboard-grid.js', () => ({
  createDashboardGrid: vi.fn(options => { state.dashboard = options; return { start() {}, destroy() {} } }),
}))
vi.mock('gridstack', () => ({ GridStack: { init: vi.fn() } }))
vi.mock('chart.js', () => ({ Chart: Object.assign(vi.fn(), { register: vi.fn(), defaults: { color: '#666' }, instances: {} }), registerables: [] }))
vi.mock('chartjs-plugin-datalabels', () => ({ default: {} }))

describe('Dashboard page', () => {
  beforeEach(() => {
    vi.resetModules()
    document.body.innerHTML = '<main></main>'
    state.authenticated = null
    state.dashboard = null
  })

  it('mounts the account dashboard with shared widget and existing APIs', async () => {
    await import('../src/dashboard-main.js')
    state.authenticated.mount(document.querySelector('main'), { username: 'coco' })
    expect(state.dashboard.registry.get('role-workload').title).toBe('Project Human Resource Statistics')
    expect(state.dashboard.registry.get('role-workload')).not.toHaveProperty('defaultH')
    expect(state.dashboard.registry.get('jira-statistics-self-test').title).toBe('Self Test Jira Statistics by Product Line')
    expect(state.dashboard.registry.get('jira-statistics-task').title).toBe('Task Jira Statistics by Product Line')
    expect(state.dashboard.registry.get('jira-statistics-customer-feedback').title).toBe('Customer Feedback Jira Statistics by Product Line')
    expect(state.dashboard.preferenceApi).toMatchObject({ get: expect.any(Function), put: expect.any(Function), reset: expect.any(Function) })
    expect(state.dashboard.gridFactory).toBeTypeOf('function')
    expect(state.dashboard.widgetConfig).toBeTypeOf('function')
  })

  it('provides the account Jira overview API to its independent widget', async () => {
    await import('../src/dashboard-main.js')
    state.authenticated.mount(document.querySelector('main'), { username: 'coco' })
    for (const key of ['self-test', 'task', 'customer-feedback']) {
      const config = await state.dashboard.widgetConfig(`jira-statistics-${key}`)
      expect(config.cardKey).toBe(key)
      expect(config.api.getStatistics).toBeTypeOf('function')
    }
  })

  it('loads Role workload from the complete account-visible catalog instead of the Projects filter snapshot', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ ownerHierarchy: [], productSpaces: [] }),
    })
    vi.stubGlobal('fetch', fetchMock)
    await import('../src/dashboard-main.js')
    state.authenticated.mount(document.querySelector('main'), { username: 'coco' })
    await state.dashboard.widgetConfig('role-workload')
    const url = new URL(fetchMock.mock.calls[0][0], 'http://localhost')
    expect(url.pathname).toBe('/api/confluence/project-facts')
    expect(url.search).toBe('')
    vi.unstubAllGlobals()
  })
})
