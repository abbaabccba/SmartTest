import { icon } from './icons.js'

export function fillSelect(select, values) {
  select.replaceChildren()
  for (const raw of values) {
    const value = raw && typeof raw === 'object' ? raw.value : raw
    const label = raw && typeof raw === 'object' ? (raw.displayName ?? raw.label ?? value) : value
    const option = document.createElement('option')
    option.value = `${value ?? ''}`
    option.textContent = `${label ?? ''}`
    select.append(option)
  }
  select._multiSelect?.syncFromSelect()
}

export function enhanceMultiSelect(select, { emptyLabel = 'Select options', compact = false, searchable = true, onOpen, onSearch } = {}) {
  select.classList.add('d-none')
  const container = document.createElement('div'); container.className = 'multi-select'
  container.innerHTML = `<button type="button" class="multi-select__control form-select" aria-haspopup="listbox" aria-expanded="false">
      <span class="multi-select__summary">Select options</span><span class="multi-select__tags" hidden></span>${icon('chevron-down', { class: 'st-icon multi-select__chevron' })}</button>
    <div class="multi-select__dropdown card shadow-lg" role="listbox" aria-multiselectable="true" aria-hidden="true">
      ${searchable ? '<div class="multi-select__search"><input class="form-control" type="search" data-preference="off" placeholder="Search options" aria-label="Search options"></div>' : ''}
      <div class="multi-select__options"></div><div class="multi-select__empty">No options available</div>
      <div class="multi-select__actions"><button type="button" class="button button-secondary" data-clear>Clear</button><button type="button" class="button button-primary" data-select-all>Select all</button></div>
    </div>`
  select.after(container)
  const control = container.querySelector('.multi-select__control')
  const dropdown = container.querySelector('.multi-select__dropdown')
  const options = container.querySelector('.multi-select__options')
  const empty = container.querySelector('.multi-select__empty')
  const search = container.querySelector('input[type="search"]')
  const outside = event => { if (!container.contains(event.target)) close() }
  const keyboard = event => { if (event.key === 'Escape') close() }
  const close = () => {
    container.classList.remove('is-open'); dropdown.setAttribute('aria-hidden', 'true'); control.setAttribute('aria-expanded', 'false')
    document.removeEventListener('mousedown', outside); document.removeEventListener('keydown', keyboard)
    if (search) search.value = ''
  }
  const updateSummary = () => {
    const values = selected(select); const tags = container.querySelector('.multi-select__tags'); const summary = container.querySelector('.multi-select__summary')
    tags.replaceChildren(); tags.hidden = values.length === 0; summary.hidden = values.length > 0
    summary.textContent = emptyLabel
    const shown = compact ? values.slice(0, 1) : values.slice(0, 2)
    shown.forEach(value => { const tag = document.createElement('span'); tag.className = 'multi-select__tag badge'; tag.textContent = [...select.options].find(option => option.value === value)?.textContent || value; tags.append(tag) })
    if (values.length > shown.length) { const more = document.createElement('span'); more.className = 'multi-select__tag badge'; more.textContent = `+${values.length - shown.length}`; tags.append(more) }
    container.querySelector('[data-clear]').disabled = values.length === 0
    container.querySelector('[data-select-all]').disabled = select.options.length === 0 || values.length === select.options.length
  }
  const syncFromSelect = () => {
    options.replaceChildren()
    for (const option of select.options) {
      const label = document.createElement('label'); label.className = 'multi-select__option'
      const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.className = 'form-check-input'; checkbox.dataset.preference = 'off'; checkbox.value = option.value; checkbox.checked = option.selected
      const text = document.createElement('span'); text.textContent = option.textContent
      checkbox.addEventListener('change', () => { option.selected = checkbox.checked; updateSummary(); select.dispatchEvent(new Event('change', { bubbles: true })) })
      label.append(checkbox, text); options.append(label)
    }
    empty.hidden = select.options.length > 0; updateSummary()
  }
  select._multiSelect = { syncFromSelect, setEmptyLabel(label) {
    emptyLabel = label; updateSummary()
  }, setDisabled(disabled) {
    control.disabled = disabled; if (search) search.disabled = disabled
    for (const input of options.querySelectorAll('input')) input.disabled = disabled
    if (disabled) for (const button of container.querySelectorAll('[data-clear], [data-select-all]')) button.disabled = true
    else updateSummary()
  } }
  control.addEventListener('click', () => { const open = !container.classList.contains('is-open'); if (open) { container.classList.add('is-open'); dropdown.setAttribute('aria-hidden', 'false'); control.setAttribute('aria-expanded', 'true'); document.addEventListener('mousedown', outside); document.addEventListener('keydown', keyboard); search?.focus(); void onOpen?.() } else close() })
  search?.addEventListener('input', () => {
    if (onSearch) { void onSearch(search.value.trim()); return }
    let visible = 0; for (const item of options.children) { item.hidden = !item.textContent.toLowerCase().includes(search.value.trim().toLowerCase()); if (!item.hidden) visible += 1 } empty.hidden = visible > 0; empty.textContent = select.options.length ? 'No matches found' : 'No options available'
  })
  container.querySelector('[data-select-all]').addEventListener('click', event => { event.stopPropagation(); for (const option of select.options) option.selected = true; syncFromSelect(); select.dispatchEvent(new Event('change', { bubbles: true })); close() })
  container.querySelector('[data-clear]').addEventListener('click', event => { event.stopPropagation(); for (const option of select.options) option.selected = false; syncFromSelect(); select.dispatchEvent(new Event('change', { bubbles: true })) })
  select.addEventListener('change', updateSummary)
  syncFromSelect()
  return syncFromSelect
}

export function selected(select) {
  return [...select.selectedOptions].map(option => option.value)
}
