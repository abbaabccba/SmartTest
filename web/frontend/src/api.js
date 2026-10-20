export class ApiUnavailableError extends Error {
  constructor(message, { status, cause, code, requestId, kind } = {}) {
    super(message, { cause })
    this.name = 'ApiUnavailableError'
    this.status = status
    this.code = code
    this.requestId = requestId
    this.kind = kind
  }
}

function safeCode(value) {
  return typeof value === 'string' && /^[a-zA-Z][a-zA-Z0-9_.-]{0,79}$/.test(value) ? value : ''
}

function safeDetail(value) {
  if (typeof value !== 'string' || value.length > 180) return ''
  // Never echo transport bodies, query/SQL text or credential-bearing diagnostics.
  if (/https?:|[\r\n<>@]|password|token|secret|authorization|cookie|\b(select|insert|update|delete|from|where|jql|sql|traceback)\b/i.test(value)) return ''
  return /^[\p{L}\p{N} _.,!()'-]+$/u.test(value) ? value : ''
}

async function requestJson(fetchImpl, url, options) {
  const method = options?.method || 'GET'
  const path = new URL(url, 'http://local').pathname
  const context = `${method} ${path}`
  let response
  try { response = await fetchImpl(url, options) } catch (cause) {
    if (cause?.name === 'AbortError') throw cause
    throw new ApiUnavailableError(`${context}: Network request failed; no HTTP response received.`,
      { kind: 'network', code: 'network_error', cause })
  }
  const rawRequestId = response.headers?.get('x-request-id')
  const requestId = typeof rawRequestId === 'string' && /^[a-zA-Z0-9_-]{1,100}$/.test(rawRequestId) ? rawRequestId : ''
  if (!response.ok) {
    let body
    try { body = await response.json() } catch (cause) {
      if (cause?.name === 'AbortError') throw cause
      // A non-JSON error body must not replace the HTTP failure or expose its contents.
    }
    const detail = body?.detail
    const code = safeCode(body?.code) || safeCode(detail?.code) || safeCode(detail?.state)
      || safeCode(body?.error) || safeCode(detail)
    const descriptions = Array.isArray(detail) ? detail.map(item => safeCode(item?.type)).filter(Boolean)
      : [safeDetail(typeof detail === 'object' ? detail?.message : detail)].filter(Boolean)
    const reason = code || descriptions.slice(0, 3).join(', ')
    throw new ApiUnavailableError(`${context}: HTTP ${response.status}${reason ? ` (${reason})` : ''}${requestId ? ` [requestId=${requestId}]` : ''}.`,
      { kind: 'http', status: response.status, code, requestId })
  }
  try { return await response.json() } catch (cause) {
    if (cause?.name === 'AbortError') throw cause
    throw new ApiUnavailableError(`${context}: Invalid JSON response${requestId ? ` [requestId=${requestId}]` : ''}.`,
      { kind: 'response', status: response.status, code: 'invalid_json_response', requestId, cause })
  }
}

function identityChangeMarker() {
  if (typeof globalThis.crypto?.randomUUID === 'function') return globalThis.crypto.randomUUID()
  return `${Date.now()}-${Math.random()}`
}

export function createAuthApi({ fetchImpl = globalThis.fetch, baseUrl = '/api' } = {}) {
  async function change(path, options) {
    const result = await request(path, options)
    if (globalThis.window) {
      globalThis.SmartTestTheme?.clear()
      window.localStorage.setItem('smarttest:identity-change', identityChangeMarker())
      window.dispatchEvent(new Event('auth:changed'))
    }
    return result
  }
  async function request(path, options = {}) {
    return requestJson(fetchImpl, `${baseUrl}${path}`, { credentials: 'same-origin', ...options })
  }
  return {
    session: () => request('/auth/session'),
    login: (username, password) => change('/auth/login', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ username, password })
    }),
    logout: () => change('/auth/logout', { method: 'POST' })
  }
}

export function createPreferenceApi({ fetchImpl = globalThis.fetch, baseUrl = '/api' } = {}) {
  async function request(scope, method, body) {
    const path = `${baseUrl}/preferences/${encodeURIComponent(scope.replace(/^\/+/, ''))}`
    const options = { method, credentials: 'same-origin' }
    if (body) { options.headers = { 'content-type': 'application/json' }; options.body = JSON.stringify(body) }
    return requestJson(fetchImpl, path, options)
  }
  return {
    get: scope => request(scope, 'GET'),
    put: (scope, items) => request(scope, 'PUT', { items, schemaVersion: 1 }),
    reset: scope => request(scope, 'DELETE')
  }
}

const TYPE_MAP = {
  PEAK_THROUGHPUT: 'performance',
  RVR: 'RVR',
  RVO: 'RVO'
}

function appendValues(params, key, values) {
  for (const value of values ?? []) {
    if (`${value}`.trim()) params.append(key, value)
  }
}

function buildQuery(filters = {}) {
  const params = new URLSearchParams()
  appendValues(params, 'product_line', filters.productLines)
  appendValues(params, 'project', filters.projects)
  appendValues(params, 'report_name', filters.reportNames)
  appendValues(params, 'test_report_csv_name', filters.testReportCsvNames)
  appendValues(params, 'standard', filters.standards)
  params.set('data_type', TYPE_MAP[filters.dataType] ?? filters.dataType ?? 'performance')
  if (filters.startDate) params.set('start_date', filters.startDate)
  if (filters.endDate) params.set('end_date', filters.endDate)
  if (filters.limit) params.set('limit', filters.limit)
  return params
}

export function createWifiDatabaseApi({ fetchImpl = globalThis.fetch, baseUrl = '/api' } = {}) {
  async function getJson(path, filters) {
    const query = buildQuery(filters)
    const url = `${baseUrl}${path}?${query}`
    return requestJson(fetchImpl, url)
  }

  return {
    getFilters: filters => getJson('/filters', filters),
    getPerformance: filters => getJson('/performance', filters)
  }
}

export function createManualAuditApi({ fetchImpl = globalThis.fetch, baseUrl = '/api' } = {}) {
  async function request(path, { method = 'GET', body } = {}) {
    const options = { method, credentials: 'same-origin' }
    if (body !== undefined) {
      options.headers = { 'content-type': 'application/json' }
      options.body = JSON.stringify(body)
    }
    return requestJson(fetchImpl, `${baseUrl}${path}`, options)
  }
  const encode = encodeURIComponent
  const post = (path, body) => request(path, { method: 'POST', body })
  return {
    createJiraAudit: body => post('/audits/jira', body),
    getJiraAudit: id => request(`/audits/jira/${encode(id)}`),
    cancelJiraAudit: id => post(`/audits/jira/${encode(id)}/cancel`),
    exportJiraAudit: id => post(`/audits/jira/${encode(id)}/export`),
    createConfluenceAudit: body => post('/audits/confluence', body),
    getConfluenceAudit: id => request(`/audits/confluence/${encode(id)}`),
    cancelConfluenceAudit: id => post(`/audits/confluence/${encode(id)}/cancel`),
    exportConfluenceAudit: id => post(`/audits/confluence/${encode(id)}/export`),
    downloadUrl: id => `${baseUrl}/downloads/${encode(id)}`,
  }
}

export function createJiraFilterApi({ fetchImpl = globalThis.fetch, baseUrl = '/api' } = {}) {
  async function request(method = 'GET', body) {
    return requestJson(fetchImpl, `${baseUrl}/jira/filter-snapshot`, {
      method, credentials: 'same-origin',
      ...(body === undefined ? {} : { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }),
    })
  }
  return {
    getJiraFilterSnapshot: () => request(),
    applyJiraFilterSnapshot: body => request('PUT', body),
    resetJiraFilterSnapshot: () => request('DELETE'),
  }
}

export function createJiraAnalyticsApi({ fetchImpl = globalThis.fetch, baseUrl = '/api' } = {}) {
  async function request(path, method = 'GET', body) {
    return requestJson(fetchImpl, `${baseUrl}/jira/analytics${path}`, { method, credentials: 'same-origin', ...(body === undefined ? {} : { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }) })
  }
  return { getJiraAnalyticsState: () => request('/state'), getJiraAnalyticsFields: () => request('/fields'), getJiraAnalyticsSuggestions: (fieldName, query = '') => request(`/suggestions?fieldName=${encodeURIComponent(fieldName)}&query=${encodeURIComponent(query)}`), getJiraAnalyticsSavedFilters: () => request('/saved-filters'), getJiraAnalyticsSavedFilter: id => request(`/saved-filters/${encodeURIComponent(id)}`), validateJiraAnalytics: body => request('/validate', 'POST', body), searchJiraAnalytics: body => request('/search', 'POST', body) }
}

export function createJiraStatisticsCardApi({ fetchImpl = globalThis.fetch, baseUrl = '/api', cardKey = 'self-test' } = {}) {
  async function request(query = false) {
    const path = `/jira/cards/${encodeURIComponent(cardKey)}/${query ? 'query' : 'statistics'}`
    return requestJson(fetchImpl, `${baseUrl}${path}`, { credentials: 'same-origin', ...(query ? { method: 'POST' } : {}) })
  }
  return { getStatistics: () => request(), queryStatistics: () => request(true),
    getDetails: (payload, { signal } = {}) => requestJson(fetchImpl, `${baseUrl}/jira/cards/${encodeURIComponent(cardKey)}/details`, {
      method: 'POST', credentials: 'same-origin', signal, headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload),
    }),
  }
}

export function createProjectFactsApi({ fetchImpl = globalThis.fetch, baseUrl = '/api' } = {}) {
  async function changeFilter(method, filters = {}) {
    return requestJson(fetchImpl, `${baseUrl}/confluence/filter-snapshot`, {
      method, credentials: 'same-origin',
      ...(method === 'PUT' ? { headers: { 'content-type': 'application/json' }, body: JSON.stringify({
        filters: filters.fields ?? {}, search: filters.search ?? '',
      }) } : {}),
    })
  }
  return {
    applyConfluenceFilterSnapshot: filters => changeFilter('PUT', filters),
    resetConfluenceFilterSnapshot: () => changeFilter('DELETE'),
    async getProjectFactsStatus() {
      return requestJson(fetchImpl, `${baseUrl}/confluence/project-facts/status`, { credentials: 'same-origin' })
    },
    async getProjectFacts(filters = {}, { snapshot = false, reset = false } = {}) {
      const query = new URLSearchParams()
      for (const [key, values] of Object.entries(filters.fields ?? {})) {
        for (const value of Array.isArray(values) ? values : [values]) if (`${value}`.trim()) query.append(`field.${key}`, value)
      }
      if (filters.search) query.set('search', filters.search)
      if (snapshot) query.set('snapshot', '1')
      if (reset) query.set('reset', '1')
      return requestJson(fetchImpl, `${baseUrl}/confluence/project-facts${query.size ? `?${query}` : ''}`)
    },
    async cancelProjectSync() {
      return requestJson(fetchImpl, `${baseUrl}/confluence/project-facts/cancel`, {
        method: 'POST', credentials: 'same-origin'
      })
    },
  }
}

export function createAuditEmailApi({ fetchImpl = globalThis.fetch, baseUrl = '/api/audit-email' } = {}) {
  async function request(path, method = 'GET', body) {
    return requestJson(fetchImpl, `${baseUrl}${path}`, { method, credentials: 'same-origin',
      ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) })
  }
    return { list: (offset = 0) => request(`/runs?offset=${offset}`), get: id => request(`/runs/${encodeURIComponent(id)}`), trigger: () => request('/runs', 'POST'),
    history: (kind, options = {}) => {
      const query = new URLSearchParams(Object.entries(options).filter(([, value]) => value !== ''))
      return request(`/history/${encodeURIComponent(kind)}?${query}`)
    },
    attachmentUrl: (id, kind, name) => `${baseUrl}/runs/${encodeURIComponent(id)}/attachments/${encodeURIComponent(kind)}/${encodeURIComponent(name)}` }
}

export function createReleaseApi({ fetchImpl = globalThis.fetch, baseUrl = '/api' } = {}) {
  async function request(path, { method = 'GET', filters = {}, options = {} } = {}) {
    const query = new URLSearchParams()
    for (const [key, values] of Object.entries(filters)) {
      for (const value of Array.isArray(values) ? values : [values]) if (`${value}`.trim()) query.append(key, value)
    }
    if (options.snapshot === true) query.set('snapshot', '1')
    else if (options.snapshot) query.set('snapshot', options.snapshot)
    if (options.reset) query.set('reset', '1')
    if (options.page !== undefined) query.set('page', options.page)
    if (options.pageSize !== undefined) query.set('pageSize', options.pageSize)
    if (options.projectId) query.set('projectId', options.projectId)
    return requestJson(fetchImpl, `${baseUrl}${path}${query.size ? `?${query}` : ''}`, {
      method, credentials: 'same-origin'
    })
  }
  return {
    getDashboardReleases: (filters = {}, options = {}) => request('/dashboard/releases', { filters, options }),
    syncDashboardReleases: () => request('/dashboard/releases/sync', { method: 'POST' }),
    getDashboardReleaseSync: taskId => request(`/dashboard/releases/sync/${encodeURIComponent(taskId)}`),
    getDashboardRelease: projectId => request(`/dashboard/releases/${encodeURIComponent(projectId)}`),
    getJiraReleaseIssues: (filters = {}, options = {}) => request('/jira/release-issues', { filters, options }),
    syncJiraReleaseIssues: () => request('/jira/release-issues/sync', { method: 'POST' }),
    getJiraReleaseSync: taskId => request(`/jira/release-issues/sync/${encodeURIComponent(taskId)}`),
    getJiraReleaseIssue: (issueKey, details = []) => request(
      `/jira/release-issues/${encodeURIComponent(issueKey)}`,
      { filters: { details } },
    ),
  }
}
