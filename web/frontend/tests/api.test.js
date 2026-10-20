import { describe, expect, it, vi } from 'vitest'

import { createAuthApi, createJiraAnalyticsApi, createJiraFilterApi, createJiraStatisticsCardApi, createManualAuditApi, createPreferenceApi, createProjectFactsApi, createReleaseApi, createWifiDatabaseApi } from '../src/api.js'

describe('Jira team bug API contract', () => {
  it('reads the account overview without sending filter state', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ state: 'ready' }) })
    await createJiraStatisticsCardApi({ fetchImpl }).getStatistics()
    expect(fetchImpl).toHaveBeenCalledWith('/api/jira/cards/self-test/statistics', { credentials: 'same-origin' })
  })
})

describe('Preference API contract', () => {
  it('reads, batch writes, and resets an encoded account scope', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: {} }) })
    const api = createPreferenceApi({ fetchImpl })
    await api.get('/wifi-database/rvr'); await api.put('/wifi-database/rvr', { standard: ['11be'] }); await api.reset('/wifi-database/rvr')
    expect(fetchImpl.mock.calls.map(call => [call[0], call[1].method])).toEqual([
      ['/api/preferences/wifi-database%2Frvr', 'GET'], ['/api/preferences/wifi-database%2Frvr', 'PUT'], ['/api/preferences/wifi-database%2Frvr', 'DELETE']
    ])
    expect(fetchImpl.mock.calls[1][1].body).toBe(JSON.stringify({ items: { standard: ['11be'] }, schemaVersion: 1 }))
  })
})

describe('Web session API contract', () => {
  it('uses cookie-backed session endpoints without persisting credentials', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ authenticated: true }) })
    const api = createAuthApi({ fetchImpl })
    await api.login('coco', 'secret')
    await api.session()
    await api.logout()
    expect(fetchImpl.mock.calls.map(call => call[0])).toEqual(['/api/auth/login', '/api/auth/session', '/api/auth/logout'])
    expect(fetchImpl.mock.calls[0][1]).toMatchObject({ method: 'POST', credentials: 'same-origin' })
    expect(fetchImpl.mock.calls[1][1]).toMatchObject({ credentials: 'same-origin' })
    expect(fetchImpl.mock.calls[0][1].body).toBe(JSON.stringify({ username: 'coco', password: 'secret' }))
  })
})

describe('Wi-Fi Database API contract', () => {
  it('keeps the legacy repeated-query filter contract and Peak type mapping', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ data: [] }) })
    const api = createWifiDatabaseApi({ fetchImpl, baseUrl: '/api' })

    await api.getPerformance({
      dataType: 'PEAK_THROUGHPUT',
      productLines: ['Consumer', 'Enterprise'],
      projects: ['Apollo'],
      testReportCsvNames: ['report.csv'],
      standards: ['802.11be'],
      startDate: '2026-08-01',
      endDate: '2026-08-24',
      limit: 1000
    })

    const url = new URL(fetchImpl.mock.calls[0][0], 'https://smarttest.local')
    expect(url.pathname).toBe('/api/performance')
    expect(url.searchParams.getAll('product_line')).toEqual(['Consumer', 'Enterprise'])
    expect(url.searchParams.getAll('project')).toEqual(['Apollo'])
    expect(url.searchParams.getAll('test_report_csv_name')).toEqual(['report.csv'])
    expect(url.searchParams.getAll('standard')).toEqual(['802.11be'])
    expect(url.searchParams.get('data_type')).toBe('performance')
    expect(url.searchParams.get('start_date')).toBe('2026-08-01')
    expect(url.searchParams.get('end_date')).toBe('2026-08-24')
    expect(url.searchParams.get('limit')).toBe('1000')
  })

  it('reports HTTP and network failures as an unavailable API', async () => {
    const httpApi = createWifiDatabaseApi({
      fetchImpl: vi.fn().mockResolvedValue({ ok: false, status: 503, text: async () => '' })
    })
    await expect(httpApi.getFilters({ dataType: 'RVR' })).rejects.toMatchObject({
      name: 'ApiUnavailableError',
      status: 503
    })

    const networkApi = createWifiDatabaseApi({ fetchImpl: vi.fn().mockRejectedValue(new TypeError('offline')) })
    await expect(networkApi.getPerformance({ dataType: 'RVO' })).rejects.toMatchObject({
      name: 'ApiUnavailableError'
    })
  })
})

describe('manual audit API contract', () => {
  it('uses task, cancellation, artifact lookup and common download endpoints', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    const api = createManualAuditApi({ fetchImpl })
    await api.createJiraAudit({ input: 'project=SH' })
    await api.getJiraAudit('a1')
    await api.cancelJiraAudit('a1')
    await api.exportJiraAudit('a1')
    await api.exportConfluenceAudit('c1')
    expect(fetchImpl.mock.calls.map(call => call[0])).toEqual([
      '/api/audits/jira', '/api/audits/jira/a1', '/api/audits/jira/a1/cancel',
      '/api/audits/jira/a1/export',
      '/api/audits/confluence/c1/export'
    ])
    expect(api.downloadUrl('d1')).toBe('/api/downloads/d1')
  })
})

describe('Jira singleton filter API contract', () => {
  it('reads, applies, and resets one server-owned snapshot', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    const api = createJiraFilterApi({ fetchImpl })
    const body = { filters: { project: ['SH'] }, jql: 'labels = weekly' }
    await api.getJiraFilterSnapshot(); await api.applyJiraFilterSnapshot(body); await api.resetJiraFilterSnapshot()
    expect(fetchImpl.mock.calls.map(([, options]) => options.method)).toEqual(['GET', 'PUT', 'DELETE'])
    expect(fetchImpl.mock.calls[1][1].body).toBe(JSON.stringify(body))
  })
})

describe('Jira Analytics filter API contract', () => {
  it('publishes conditions before independently querying the card without issue pagination', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    const api = createJiraAnalyticsApi({ fetchImpl })
    await api.getJiraAnalyticsFields(); await api.getJiraAnalyticsSuggestions('assignee', 'co'); await api.getJiraAnalyticsSavedFilters()
    await api.searchJiraAnalytics({ mode: 'advanced', jql: 'project = SH' })
    const card = createJiraStatisticsCardApi({ fetchImpl, cardKey: 'self-test' })
    await card.queryStatistics(); await card.getStatistics()
    expect(fetchImpl.mock.calls[4][1]).toEqual({ credentials: 'same-origin', method: 'POST' })
    expect(fetchImpl.mock.calls.map(call => call[0])).toEqual([
      '/api/jira/analytics/fields', '/api/jira/analytics/suggestions?fieldName=assignee&query=co', '/api/jira/analytics/saved-filters',
      '/api/jira/analytics/search', '/api/jira/cards/self-test/query', '/api/jira/cards/self-test/statistics',
    ])
    expect(fetchImpl.mock.calls[3][1].body).toBe(JSON.stringify({ mode: 'advanced', jql: 'project = SH' }))
  })
})

describe('Confluence project facts API contract', () => {
  it('polls only the background job status endpoint', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ state: 'loading' }) })

    await createProjectFactsApi({ fetchImpl }).getProjectFactsStatus()

    expect(fetchImpl).toHaveBeenCalledWith('/api/confluence/project-facts/status', { credentials: 'same-origin' })
  })

  it('sends dynamic field filters and project/person search to the read-only endpoint', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ projects: [] }) })
    await createProjectFactsApi({ fetchImpl }).getProjectFacts({
      fields: { 'unexpected owner': ['Alice', 'Bob'], 'support mode': ['B'] }, search: 'Coco'
    })
    const url = new URL(fetchImpl.mock.calls[0][0], 'https://smarttest.local')
    expect(url.pathname).toBe('/api/confluence/project-facts')
    expect(url.searchParams.getAll('field.unexpected owner')).toEqual(['Alice', 'Bob'])
    expect(url.searchParams.has('catalog')).toBe(false)
    expect(url.searchParams.get('field.support mode')).toBe('B')
    expect(url.searchParams.get('search')).toBe('Coco')
  })
})

describe('Release Dashboard and Jira workbench API contract', () => {
  it('encodes repeated release filters and server snapshot modes', async () => {
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ releases: [] }) })
    const api = createReleaseApi({ fetchImpl })
    await api.getDashboardReleases({ productLine: ['DOPL'], stage: ['EVT'] }, { snapshot: true })
    await api.getJiraReleaseIssues(
      { priority: ['P0', 'P1'] },
      { snapshot: 'dashboard', projectId: 'P100', page: 2, pageSize: 25 },
    )
    const dashboard = new URL(fetchImpl.mock.calls[0][0], 'https://smarttest.local')
    const jira = new URL(fetchImpl.mock.calls[1][0], 'https://smarttest.local')
    expect(dashboard.searchParams.get('snapshot')).toBe('1')
    expect(dashboard.searchParams.getAll('productLine')).toEqual(['DOPL'])
    expect(jira.searchParams.get('snapshot')).toBe('dashboard')
    expect(jira.searchParams.get('projectId')).toBe('P100')
    expect(jira.searchParams.getAll('priority')).toEqual(['P0', 'P1'])
    expect(jira.searchParams.get('pageSize')).toBe('25')
  })
})


describe('shared JSON request failures', () => {
  it('classifies native fetch rejection without exposing origin or query', async () => {
    const api = createJiraAnalyticsApi({ baseUrl: 'unsupported://private-host/api', fetchImpl: globalThis.fetch })
    try { await api.getJiraAnalyticsSuggestions('private-field', 'private-query'); expect.fail('must reject') } catch (error) {
      expect(error).toMatchObject({ kind: 'network', code: 'network_error', cause: expect.any(TypeError) })
      expect(error.message).toContain('GET /api/jira/analytics/suggestions: Network request failed')
      expect(error.message).not.toMatch(/private-host|private-field|private-query/)
    }
  })
  it.each([
    [{ detail: { state: 'query_snapshot_unavailable', sql: 'SELECT private' } }, 'query_snapshot_unavailable'],
    [{ detail: 'Invalid input' }, 'Invalid input'],
    [{ detail: [{ type: 'missing', msg: 'Field required', input: 'private-value' }] }, 'missing'],
    [{ detail: 'SELECT password FROM private WHERE token=secret' }, 'HTTP 500.'],
  ])('keeps safe HTTP diagnostics and request identity', async (body, expected) => {
    const fetchImpl = vi.fn().mockResolvedValue(new Response(JSON.stringify(body), { status: 500, headers: { 'x-request-id': '123-test' } }))
    const error = await createJiraStatisticsCardApi({ fetchImpl }).queryStatistics().catch(error => error)
    expect(error).toMatchObject({ kind: 'http', status: 500, requestId: '123-test' })
    expect(error.message).toContain('POST /api/jira/cards/self-test/query: HTTP 500')
    expect(error.message).toContain(expected.replace('.', ''))
    expect(error.message).not.toMatch(/SELECT|private|password|token=|secret/)
  })
  it('does not expose non-JSON HTTP response bodies', async () => {
    const api = createReleaseApi({ fetchImpl: async () => new Response('<html>private response</html>', { status: 502 }) })
    await expect(api.getDashboardReleases()).rejects.toMatchObject({ kind: 'http', status: 502 })
  })
  it('distinguishes malformed successful JSON, retaining cause and safe request id', async () => {
    const api = createPreferenceApi({ fetchImpl: async () => new Response('broken private', { status: 200, headers: { 'x-request-id': '123-parse' } }) })
    await expect(api.get('scope')).rejects.toMatchObject({ kind: 'response', status: 200, code: 'invalid_json_response',
      requestId: '123-parse', cause: expect.any(SyntaxError) })
  })
  it.each(['fetch', 'body'])('does not turn %s cancellation into a failure', async phase => {
    const cause = new DOMException('Cancelled', 'AbortError')
    const fetchImpl = async () => {
      if (phase === 'fetch') throw cause
      return { ok: true, json: async () => { throw cause } }
    }
    await expect(createJiraStatisticsCardApi({ fetchImpl }).getStatistics()).rejects.toBe(cause)
  })
  it('rejects unsafe request identity without echoing it', async () => {
    const api = createJiraStatisticsCardApi({ fetchImpl: async () => ({ ok: false, status: 500,
      headers: { get: () => 'private/unsafe?token=value' }, json: async () => ({ detail: 'query_failed' }) }) })
    const error = await api.getStatistics().catch(error => error)
    expect(error.requestId).toBe('')
    expect(error.message).not.toMatch(/private|unsafe|token=value/)
  })
})
