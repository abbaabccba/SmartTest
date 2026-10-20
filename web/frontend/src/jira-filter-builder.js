import { icon } from './icons.js'
import { enhanceMultiSelect, fillSelect } from './multi-select.js'
import { escapeHtml } from './dom.js'

export function createJiraFilterBuilder({ root, api, account, onApplied = () => {} }) {
  let mode = 'basic'; let fields = []; let generatedJql = ''; let advancedDirty = false
  let sourceFilterId = ''; let disposed = false
  root.innerHTML = `<section class="card jira-native-filter" aria-label="Jira issue filter">
    <div class="jira-native-query-row">
      <div class="jira-native-toolbar" data-basic-panel>
        <div data-fixed-fields></div>
        <input class="form-control jira-contains-text" name="containsText" placeholder="Contains text" aria-label="Contains text">
        <div class="jira-more-anchor"><button class="button button-secondary" type="button" data-more aria-expanded="false">More</button>
          <div class="jira-more-menu" data-more-menu hidden></div></div>
      </div>
      <div class="jira-native-advanced" data-advanced-panel hidden><input class="form-control" name="jql" placeholder="JQL" aria-label="JQL"></div>
      <button class="button button-primary" type="button" data-search>Search</button>
      <button class="button button-secondary" type="button" data-advanced>Advanced</button><button class="button button-secondary" type="button" data-basic hidden>Basic</button>
    </div>
    <div class="jira-condition-row" data-conditions></div>
    <div class="jira-native-actions"><select class="form-control jira-saved-filter" name="savedFilter" aria-label="Saved Filter"><option value="">Saved Filter</option></select>
      <button class="button button-secondary" type="button" data-reset>Reset</button><span data-mode>Basic draft</span></div>
    <p class="card-subtitle" data-applied></p><p class="async-feedback" data-feedback role="status"></p></section>`
  const q = selector => root.querySelector(selector)
  const values = name => [...(q(`[name="${name}"]`)?.selectedOptions || [])].map(option => option.value).filter(Boolean)
  function configureSelect(field, host, emptyLabel = `${field.name}: All`) {
    const select = document.createElement('select'); select.name = field.id; select.multiple = true; select.setAttribute('aria-label', `${field.name} values`); host.append(select)
    let request = 0
    const load = async query => {
      const generation = ++request
      try {
        const result = await api.getJiraAnalyticsSuggestions(field.id, query)
        if (disposed || generation !== request) return
        const selected = [...select.selectedOptions].map(option => ({ value: option.value, displayName: option.textContent }))
        const selectedValues = new Set(selected.map(item => item.value))
        const returnedValues = new Set(result.map(item => String(item.value)))
        fillSelect(select, [...result, ...selected.filter(item => !returnedValues.has(item.value))])
        for (const option of select.options) option.selected = selectedValues.has(option.value)
        select._multiSelect?.syncFromSelect()
        q('[data-feedback]').textContent = ''
      } catch {
        if (!disposed && generation === request) q('[data-feedback]').textContent = `Jira suggestions unavailable for ${field.name}.`
      }
    }
    enhanceMultiSelect(select, { emptyLabel, compact: true, searchable: true, onOpen: () => load(''), onSearch: load })
    select.nextElementSibling.querySelector('.multi-select__control').setAttribute('aria-label', emptyLabel)
    return select
  }
  function basicPayload() {
    const more = {}; for (const input of root.querySelectorAll('[data-dynamic-field]')) more[input.dataset.dynamicField] = input.multiple ? [...input.selectedOptions].map(option => option.value) : input.value
    return { project: values('project'), issueType: values('issuetype'), status: values('status'), assignee: values('assignee'),
      containsText: q('[name="containsText"]').value, resolution: values('resolution'), more: Object.fromEntries(Object.entries(more).filter(([key]) => key !== 'resolution')) }
  }
  function showMode(next) { mode = next; q('[data-basic-panel]').hidden = next !== 'basic'; q('[data-advanced-panel]').hidden = next !== 'advanced'; q('[data-advanced]').hidden = next === 'advanced'; q('[data-basic]').hidden = next === 'basic'; q('[data-mode]').textContent = `${next === 'basic' ? 'Basic' : 'Advanced'} draft` }
  function addField(field) {
    if ([...root.querySelectorAll('[data-condition]')].some(item => item.dataset.condition === field.id)) return
    const block = document.createElement('div'); block.dataset.condition = field.id
    let input
    if (['multi', 'user'].includes(field.control)) {
      input = configureSelect(field, block)
      input.dataset.dynamicField = field.id
    } else {
      const label = document.createElement('label'); label.textContent = `${field.name}: `
      input = document.createElement('input'); input.className = 'form-control'; input.dataset.dynamicField = field.id; input.setAttribute('aria-label', field.name)
      label.append(input); block.append(label)
    }
    const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'icon-button'; remove.innerHTML = icon('close'); remove.setAttribute('aria-label', `Remove ${field.name}`); remove.onclick = () => block.remove()
    block.append(remove); q('[data-conditions]').append(block)
  }
  q('[name="jql"]').addEventListener('input', () => { advancedDirty = q('[name="jql"]').value !== generatedJql })
  q('[data-advanced]').addEventListener('click', async () => { showMode('advanced'); const result = await api.validateJiraAnalytics({ mode: 'basic', basic: basicPayload() }); generatedJql = result.userJql ?? result.jql ?? ''; advancedDirty = false; q('[name="jql"]').value = generatedJql })
  q('[data-basic]').addEventListener('click', () => { if (advancedDirty || q('[name="jql"]').value !== generatedJql) { q('[data-feedback]').textContent = 'This JQL cannot be converted to Basic without loss.'; return } showMode('basic'); q('[data-feedback]').textContent = '' })
  q('[data-more]').addEventListener('click', () => { const menu = q('[data-more-menu]'); menu.hidden = !menu.hidden; q('[data-more]').setAttribute('aria-expanded', String(!menu.hidden)) })
  q('[name="savedFilter"]').addEventListener('change', async event => {
    if (!event.target.value) return
    try {
      const filter = await api.getJiraAnalyticsSavedFilter(event.target.value)
      if (disposed) return
      sourceFilterId = filter.id; generatedJql = ''; advancedDirty = true; q('[name="jql"]').value = filter.jql; showMode('advanced')
    } catch {
      if (!disposed) q('[data-feedback]').textContent = 'This Saved Filter is no longer available; the current draft was preserved.'
    }
  })
  q('[data-search]').addEventListener('click', async () => {
    q('[data-feedback]').textContent = 'Validating and starting Jira query…'
    const payload = mode === 'basic' ? { mode, basic: basicPayload() } : { mode, jql: q('[name="jql"]').value, sourceFilterId }
    try {
      const result = await api.searchJiraAnalytics(payload)
      if (disposed) return
      if (!result.validation?.valid) { q('[data-feedback]').textContent = result.validation?.errors?.join(' · ') || 'Invalid JQL'; return }
      q('[data-feedback]').textContent = 'Conditions applied.'
      q('[data-applied]').textContent = `Applied user conditions: ${result.userJql || '(none)'}`
      onApplied()
    } catch {
      if (!disposed) q('[data-feedback]').textContent = 'Jira query failed; the current draft and last valid snapshot were preserved.'
    }
  })
  q('[data-reset]').addEventListener('click', () => { for (const input of root.querySelectorAll('input, textarea, select[multiple]')) { if (input.type === 'checkbox') input.checked = false; else if (input.multiple) { for (const option of input.options) option.selected = false; input._multiSelect?.syncFromSelect() } else input.value = '' } for (const item of q('[data-conditions]').querySelectorAll('[data-condition]')) item.remove(); sourceFilterId = ''; generatedJql = ''; advancedDirty = false; showMode('basic') })
  return {
    async start() {
      try {
        const [state, schema, saved] = await Promise.all([api.getJiraAnalyticsState(), api.getJiraAnalyticsFields(), api.getJiraAnalyticsSavedFilters()])
        if (disposed) return
        for (const field of schema.fixed || []) {
          const host = document.createElement('div'); host.dataset.jiraField = field.id
          if (field.id === 'resolution') q('[data-conditions]').append(host); else q('[data-fixed-fields]').append(host)
          configureSelect(field, host)
        }
        fields = schema.more || []
        q('[data-more-menu]').innerHTML = `<input class="form-control" type="search" data-more-search placeholder="Search" aria-label="Search fields"><div data-more-options>${fields.map(field => `<button type="button" class="menu-item" data-add-field="${escapeHtml(field.id)}" aria-label="Add ${escapeHtml(field.name)}" ${field.queryable ? '' : 'disabled'}>${escapeHtml(field.name)}${field.queryable ? '' : ' · Advanced only'}</button>`).join('')}</div>`
        q('[data-more-search]').addEventListener('input', event => { const term = event.target.value.trim().toLowerCase(); for (const button of q('[data-more-options]').children) button.hidden = !button.textContent.toLowerCase().includes(term) })
        for (const button of q('[data-more-menu]').querySelectorAll('[data-add-field]')) button.onclick = () => addField(fields.find(field => field.id === button.dataset.addField))
        q('[name="savedFilter"]').innerHTML += saved.map(item => `<option value="${escapeHtml(item.id)}">${escapeHtml(item.name)}</option>`).join('')
        q('[data-applied]').textContent = state.conditions ? `Applied user conditions: ${state.userJql || '(none)'}` : ''
      } catch {
        if (!disposed) q('[data-feedback]').textContent = 'Jira Analytics filter unavailable.'
      }
    },
    destroy() { disposed = true; localStorage.removeItem(`smarttest:jira-filter:${account}`); root.replaceChildren() },
  }
}
