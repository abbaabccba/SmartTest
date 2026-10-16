// @vitest-environment jsdom
import { expect, it, vi } from 'vitest'
import { createAuditEmailPage } from '../src/audit-email.js'

it('offers only immediate weekly review and displays both completed reports', async () => {
  document.body.innerHTML = '<div id="page"></div>'
  const detail = { id: 'run-1', state: 'completed', evidence: 'email=recorded', reports: {
    jira: { html: '<p>jira</p>', attachments: [] }, confluence: { html: '<p>confluence</p>', attachments: [] }
  } }
  const api = { history: async () => ({ rows: [], total: 0, productLines: [] }), list: vi.fn(async () => ({ runs: [], total: 0 })), trigger: vi.fn(async () => detail), get: vi.fn() }
  const page = createAuditEmailPage({ root: document.querySelector('#page'), api })
  await page.start()
  expect(document.body.textContent).toContain('每周五北京时间 18:00')
  expect(document.body.textContent).toContain('fae.qa@amlogic.com')
  expect(document.body.textContent).not.toContain('ping.xiong@amlogic.com')
  expect(document.querySelector('[data-event-form]')).toBeNull()
  expect(document.querySelector('[data-schedule-form]')).toBeNull()
  document.querySelector('[data-trigger]').click()
  await vi.waitFor(() => expect(api.trigger).toHaveBeenCalledTimes(1))
  await vi.waitFor(() => expect(document.querySelector('[data-evidence]').value).toBe('email=recorded'))
  expect(document.querySelector('[title="Jira 报告预览"]').srcdoc).toBe('<p>jira</p>')
  page.destroy()
})

it('polls immediate execution to its saved terminal result', async () => {
  document.body.innerHTML = '<div id="page"></div>'
  const complete = { id: 'run-2', state: 'completed', evidence: 'smtp_accepted', reports: { jira: {}, confluence: {} } }
  const api = { history: async () => ({ rows: [], total: 0, productLines: [] }), list: async () => ({ runs: [], total: 0 }), trigger: async () => ({ ...complete, state: 'running' }), get: vi.fn(async () => complete) }
  const page = createAuditEmailPage({ root: document.querySelector('#page'), api, pollDelay: async () => {} })
  await page.start(); document.querySelector('[data-trigger]').click()
  await vi.waitFor(() => expect(api.get).toHaveBeenCalledWith('run-2'))
  expect(document.querySelector('[data-evidence]').value).toBe('smtp_accepted')
  page.destroy()
})

it('audit email api exposes history and immediate trigger only', async () => {
  const { createAuditEmailApi } = await import('../src/api.js')
  const fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
  const api = createAuditEmailApi({ fetchImpl })
  expect(api.listEvents).toBeUndefined(); expect(api.getSchedule).toBeUndefined()
  await api.trigger(); await api.list(4); await api.history('jira', { offset: 10, start_date: '2026-10-01', end_date: '' })
  expect(fetchImpl.mock.calls.map(call => [call[0], call[1].method])).toEqual([
    ['/api/audit-email/runs', 'POST'], ['/api/audit-email/runs?offset=4', 'GET'],
    ['/api/audit-email/history/jira?offset=10&start_date=2026-10-01', 'GET']
  ])
})

it('keeps history date queries, pagination and saved details independent', async () => {
  document.body.innerHTML = '<main></main>'
  const calls = []
  const api = {
    list: async () => ({ runs: [], total: 0 }),
    history: async (kind, query) => {
      calls.push([kind, { ...query }])
      return { productLines: ['TV'], rows: [{ lines: [{ productLine: 'TV', projectCount: null, passed: 58, failed: 60 }], id: 'saved', label: '0828', date: null, source: 'screenshot',
        productLine: 'TV', projectCount: null, total: 118, passed: 58, failed: 60, rate: '49.15%', modifiedPassed: null, modifiedRate: null }], total: 11 }
    },
    get: async () => ({ summary: { jira: { issues: [{ key: 'QA-1', summary: 'Saved issue', creator: 'Coco', passed: true, violations: [] }] }, confluence: {} } }),
  }
  const page = createAuditEmailPage({ root: document.querySelector('main'), api })
  await page.start()
  expect([...document.querySelectorAll('h2')].map(node => node.textContent)).toEqual([
    '审查与报告', 'Jira 历史数据', 'Confluence 历史数据', '执行历史', '报告详情',
  ])
  const jira = document.querySelector('[data-audit-history="jira"]')
  const confluence = document.querySelector('[data-audit-history="confluence"]')
  expect(jira.textContent).toContain('截图历史')
  expect(jira.textContent).toContain('未记录')
  jira.querySelector('[name="start_date"]').value = '2026-10-01'
  jira.querySelector('form').dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
  await vi.waitFor(() => expect(calls).toHaveLength(3))
  expect(calls[2]).toEqual(['jira', { offset: 0, start_date: '2026-10-01', end_date: '' }])
  jira.querySelector('[data-history-next]').click()
  await vi.waitFor(() => expect(calls).toHaveLength(4))
  expect(calls[3][1].offset).toBe(10)
  expect(confluence.querySelector('[name="start_date"]').value).toBe('')
  jira.querySelector('[data-history-view]').click()
  await vi.waitFor(() => expect(jira.textContent).toContain('Saved issue'))
  confluence.querySelector('[data-history-view]').click()
  await vi.waitFor(() => expect(confluence.querySelector('[data-history-detail]').textContent).toContain('未记录'))
  page.destroy()
})

it('discards older history query responses without replacing the other component', async () => {
  document.body.innerHTML = '<main></main>'
  const pending = []
  const api = {
    list: async () => ({ runs: [], total: 0 }),
    history: (kind, query) => kind === 'jira' && query.start_date
      ? new Promise(resolve => pending.push(resolve)) : Promise.resolve({ rows: [], total: 0, productLines: [] }),
  }
  const page = createAuditEmailPage({ root: document.querySelector('main'), api })
  await page.start()
  const jira = document.querySelector('[data-audit-history="jira"]')
  const submit = () => jira.querySelector('form').dispatchEvent(new Event('submit', { cancelable: true }))
  jira.querySelector('[name="start_date"]').value = '2026-10-01'; submit()
  jira.querySelector('[name="start_date"]').value = '2026-10-02'; submit()
  pending[1]({ rows: [{ id: 'new', date: '2026-10-02', source: 'manual', total: 2 }], total: 1 })
  await vi.waitFor(() => expect(jira.textContent).toContain('2026-10-02'))
  pending[0]({ rows: [{ id: 'old', date: '2026-10-01', source: 'manual', total: 1 }], total: 1 })
  await Promise.resolve()
  expect(jira.querySelector('[data-history-rows]').textContent).not.toContain('2026-10-01')
  expect(document.querySelector('[data-audit-history="confluence"] [data-history-count]').textContent).toBe('暂无历史数据')
  page.destroy()
})

it('separates current created issues from the saved previous-period reaudit statistics and issues', async () => {
  document.body.innerHTML = '<main></main>'
  const api = {
    list: async () => ({ runs: [], total: 0 }),
    history: async kind => ({ productLines: [], rows: kind === 'jira' ? [{ id: 'oct-9', date: '2026-10-09', source: 'scheduled',
      total: 74, modifiedPassed: 97, modifiedRate: '78.86%', passed: 60, failed: 14, rate: '81.08%' }] : [], total: kind === 'jira' ? 1 : 0 }),
    get: async () => ({ summary: { jira: { total: 74, passed: 60, failed: 14, rate: '81.08%',
      issues: [{ key: 'CURRENT', summary: 'Current issue', passed: true }] } }, reports: { jira: { remediation: {
      reviewedAt: '2026-10-09T10:01:35+00:00', currentSummary: { total: 123, passed: 97, failed: 26, rate: '78.86%',
        issues: [{ key: 'PREVIOUS', summary: 'Reaudited issue', passed: false }] }
    } } } }),
  }
  const page = createAuditEmailPage({ root: document.querySelector('main'), api })
  await page.start()
  const jira = document.querySelector('[data-audit-history="jira"]')
  expect([...jira.querySelectorAll('thead th')].map(cell => cell.textContent)).toEqual(['审查日期', '来源', '总数', '修改后通过数', '修改后通过率', '通过', '未通过', '通过率'])
  expect(jira.querySelector('[data-history-rows]').textContent).toContain('9778.86%')
  jira.querySelector('[data-history-view]').click()
  await vi.waitFor(() => expect(jira.querySelector('[data-history-detail]').textContent).toContain('上期本次复审'))
  const tables = jira.querySelector('[data-history-detail]').querySelectorAll('table')
  expect(tables[0].textContent).toContain('74601481.08%')
  expect(tables[1].textContent).toContain('Current issue')
  expect(tables[2].textContent).toContain('123972678.86%')
  expect(tables[3].textContent).toContain('Reaudited issue')
  expect(jira.querySelector('[data-history-detail]').textContent).toContain('18:01:35')
  page.destroy()
})

it('renders each Confluence period as one compact row with four product line column groups', async () => {
  document.body.innerHTML = '<main></main>'
  const names = ['China Operator', 'Smart Device', 'TV', 'Global Operator & STB']
  const api = {
    list: async () => ({ runs: [], total: 0 }),
    history: async kind => ({ productLines: names, total: kind === 'confluence' ? 1 : 0,
      rows: kind === 'confluence' ? [{ id: 'saved', date: '2026-10-09', lines: names.map(productLine => ({ productLine, projectCount: 1, passed: 2, failed: 3 })) }] : [] }),
    get: async () => ({ summary: { confluence: { projects: [{ productLine: 'TV', name: 'Saved project', projectId: 'P1', owners: [], rules: [] }] } } }),
  }
  const page = createAuditEmailPage({ root: document.querySelector('main'), api })
  await page.start()
  const component = document.querySelector('[data-audit-history="confluence"]')
  expect(component.querySelectorAll('[data-history-rows] tr')).toHaveLength(1)
  expect([...component.querySelectorAll('th[colspan="3"]')].map(cell => cell.textContent)).toEqual(names)
  expect(component.querySelectorAll('[data-history-rows] td')).toHaveLength(13)
  component.querySelector('[data-history-view]').click()
  await vi.waitFor(() => expect(component.querySelector('[data-history-detail]').textContent).toContain('Saved project'))
  expect([...component.querySelectorAll('h4')].map(heading => heading.textContent)).toEqual(names)
  page.destroy()
})
