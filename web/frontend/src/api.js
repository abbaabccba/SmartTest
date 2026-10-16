export class ApiUnavailableError extends Error {
  constructor(message, { status, cause } = {}) {
    super(message, { cause })
    this.name = 'ApiUnavailableError'
    this.status = status
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
    let response
    try { response = await fetchImpl(`${baseUrl}${path}`, { credentials: 'same-origin', ...options }) } catch (cause) {
      throw new ApiUnavailableError('Authentication service unavailable.', { cause })
    }
    if (!response.ok) throw new ApiUnavailableError(`Authentication failed (${response.status}).`, { status: response.status })
    return response.json()
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
    let response
    const path = `${baseUrl}/preferences/${encodeURIComponent(scope.replace(/^\/+/, ''))}`
    const options = { method, credentials: 'same-origin' }
    if (body) { options.headers = { 'content-type': 'application/json' }; options.body = JSON.stringify(body) }
    try { response = await fetchImpl(path, options) } catch (cause) {
      throw new ApiUnavailableError('Preference service unavailable.', { cause })
    }
    if (!response.ok) throw new ApiUnavailableError(`Preference service unavailable (${response.status}).`, { status: response.status })
    return response.json()
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
    let response
    try {
      response = await fetchImpl(url)
    } catch (cause) {
      throw new ApiUnavailableError('Wi-Fi Database API unavailable.', { cause })
    }
    if (!response.ok) {
      const detail = (await response.text()).trim()
      throw new ApiUnavailableError(detail || `Wi-Fi Database API unavailable (${response.status}).`, {
        status: response.status
      })
    }
    return response.json()
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
    let response
    try { response = await fetchImpl(`${baseUrl}${path}`, options) } catch (cause) {
      throw new ApiUnavailableError('Manual audit API unavailable.', { cause })
    }
    if (!response.ok) throw new ApiUnavailableError(`Manual audit API unavailable (${response.status}).`, { status: response.status })
    return response.json()
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
    const response = await fetchImpl(`${baseUrl}/jira/filter-snapshot`, {
      method, credentials: 'same-origin',
      ...(body === undefined ? {} : { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }),
    })
    if (!response.ok) throw new ApiUnavailableError(`Jira filter API unavailable (${response.status}).`, { status: response.status })
    return response.json()
  }
  return {
    getJiraFilterSnapshot: () => request(),
    applyJiraFilterSnapshot: body => request('PUT', body),
    resetJiraFilterSnapshot: () => request('DELETE'),
  }
}

export function createJiraAnalyticsApi({ fetchImpl = globalThis.fetch, baseUrl = '/api' } = {}) {
  async function request(path, method = 'GET', body) {
    const response = await fetchImpl(`${baseUrl}/jira/analytics${path}`, { method, credentials: 'same-origin', ...(body === undefined ? {} : { headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) }) })
    if (!response.ok) throw new ApiUnavailableError(`Jira Analytics API unavailable (${response.status}).`, { status: response.status })
    return response.json()
  }
  return { getJiraAnalyticsState: () => request('/state'), getJiraAnalyticsFields: () => request('/fields'), getJiraAnalyticsSuggestions: (fieldName, query = '') => request(`/suggestions?fieldName=${encodeURIComponent(fieldName)}&query=${encodeURIComponent(query)}`), getJiraAnalyticsSavedFilters: () => request('/saved-filters'), getJiraAnalyticsSavedFilter: id => request(`/saved-filters/${encodeURIComponent(id)}`), validateJiraAnalytics: body => request('/validate', 'POST', body), searchJiraAnalytics: body => request('/search', 'POST', body) }
}

export function createJiraStatisticsCardApi({ fetchImpl = globalThis.fetch, baseUrl = '/api', cardKey = 'self-test' } = {}) {
  async function request(query = false, period, intent = 'refresh') {
    const path = `/jira/cards/${encodeURIComponent(cardKey)}/${query ? 'query' : 'statistics'}`
    const response = await fetchImpl(`${baseUrl}${path}`, { credentials: 'same-origin', ...(query ? { method: 'POST',
      headers: { 'content-type': 'application/json' }, body: JSON.stringify({ intent, ...(period ? { period } : {}) }) } : {}) })
    if (!response.ok) throw new ApiUnavailableError(`Jira statistics card unavailable (${response.status}).`, { status: response.status })
    return response.json()
  }
  return { getStatistics: () => request(), queryStatistics: (period, intent) => request(true, period, intent) }
}

export function createProjectFactsApi({ fetchImpl = globalThis.fetch, baseUrl = '/api' } = {}) {
  async function changeFilter(method, filters = {}) {
    const response = await fetchImpl(`${baseUrl}/confluence/filter-snapshot`, {
      method, credentials: 'same-origin',
      ...(method === 'PUT' ? { headers: { 'content-type': 'application/json' }, body: JSON.stringify({
        filters: filters.fields ?? {}, search: filters.search ?? '',
      }) } : {}),
    })
    if (!response.ok) throw new ApiUnavailableError(`Project filter API unavailable (${response.status}).`, { status: response.status })
    return response.json()
  }
  return {
    applyConfluenceFilterSnapshot: filters => changeFilter('PUT', filters),
    resetConfluenceFilterSnapshot: () => changeFilter('DELETE'),
    async getProjectFactsStatus() {
      const response = await fetchImpl(`${baseUrl}/confluence/project-facts/status`, { credentials: 'same-origin' })
      if (!response.ok) throw new ApiUnavailableError(`Project facts status unavailable (${response.status}).`, { status: response.status })
      return response.json()
    },
    async getProjectFacts(filters = {}, { snapshot = false, reset = false } = {}) {
      const query = new URLSearchParams()
      for (const [key, values] of Object.entries(filters.fields ?? {})) {
        for (const value of Array.isArray(values) ? values : [values]) if (`${value}`.trim()) query.append(`field.${key}`, value)
      }
      if (filters.search) query.set('search', filters.search)
      if (snapshot) query.set('snapshot', '1')
      if (reset) query.set('reset', '1')
      let response
      try { response = await fetchImpl(`${baseUrl}/confluence/project-facts${query.size ? `?${query}` : ''}`) } catch (cause) {
        throw new ApiUnavailableError('Project facts API unavailable.', { cause })
      }
      if (!response.ok) throw new ApiUnavailableError(`Project facts API unavailable (${response.status}).`, { status: response.status })
      return response.json()
    },
    async cancelProjectSync() {
      const response = await fetchImpl(`${baseUrl}/confluence/project-facts/cancel`, {
        method: 'POST', credentials: 'same-origin'
      })
      if (!response.ok) throw new ApiUnavailableError(`Project sync cancellation failed (${response.status}).`, { status: response.status })
      return response.json()
    },
  }
}

export function createAuditEmailApi({ fetchImpl = globalThis.fetch, baseUrl = '/api/audit-email' } = {}) {
  async function request(path, method = 'GET', body) {
    let response
    try { response = await fetchImpl(`${baseUrl}${path}`, { method, credentials: 'same-origin',
      ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) }) } catch (cause) {
      throw new ApiUnavailableError('审查报告服务不可用。', { cause })
    }
    if (!response.ok) throw new ApiUnavailableError(`审查报告请求失败（${response.status}）。`, { status: response.status })
    return response.json()
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
    let response
    try {
      response = await fetchImpl(`${baseUrl}${path}${query.size ? `?${query}` : ''}`, {
        method, credentials: 'same-origin'
      })
    } catch (cause) {
      throw new ApiUnavailableError('Release API unavailable.', { cause })
    }
    if (!response.ok) throw new ApiUnavailableError(`Release API unavailable (${response.status}).`, { status: response.status })
    return response.json()
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
