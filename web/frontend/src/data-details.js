import './data-details.css'
import { element } from './dom.js'
import { icon } from './icons.js'
import { downloadExcel } from './excel-download.js'

let activeClose
export const issueColumns = [
  { key: 'key', label: 'Jira Key', link: 'webUrl' }, { key: 'summary', label: 'Summary', link: 'webUrl' },
  { key: 'status', label: 'Status' }, { key: 'person', label: 'Person' },
  { key: 'createdAt', label: 'Created' }, { key: 'count', label: 'Contribution' },
]
export const projectColumns = [
  { key: 'name', label: 'Project', link: 'url' }, { key: 'project_id', label: 'Project ID' },
  { key: 'space_key', label: 'Product line' }, { key: 'stage', label: 'Stage' },
  { key: 'status', label: 'Status' }, { key: 'support_mode', label: 'Support mode' },
  { key: 'role', label: 'Role' }, { key: 'person', label: 'Person' },
]
export function bindDetailsTrigger(target, open) {
  target.classList.add('data-details-trigger')
  target.setAttribute('role', 'button'); target.tabIndex = 0
  target.setAttribute('aria-haspopup', 'dialog'); target.title = 'View details'
  target.addEventListener('click', event => { event.preventDefault(); event.stopPropagation(); open(target) })
  target.addEventListener('keydown', event => {
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); open(target) }
  })
}

export function createDataDetails({
  loadWorkbook = async () => { const exceljs = await import('exceljs'); return exceljs.Workbook ?? exceljs.default?.Workbook },
  saveFile = downloadExcel,
} = {}) {
  let closeOwned
  return {
    open({ title, scope = '', value, rows = [], columns, updatedAt, note = '', error = '', trigger = document.activeElement,
      loadPage, loadActions, loadDownload, downloadColumns, scopeOptions = false }) {
      activeClose?.()
      columns = columns.map(column => ({ ...column }))
      let records = rows.map(row => ({ ...row }))
      let total = records.length; let allQa = false; let scopeGeneration = 0; let requestGeneration = 0
      let currentError = error; let controller = new AbortController()
      const dialog = element('dialog', 'data-details')
      dialog.setAttribute('aria-label', `${title} details`)
      const header = element('header', 'data-details-header')
      const heading = element('div')
      heading.append(element('h2', 'card-title', title), element('p', 'card-subtitle', scope))
      const close = element('button', 'button button-secondary', 'Close')
      close.type = 'button'; close.autofocus = true; close.dataset.detailsClose = ''
      const actions = element('div', 'data-details-actions')
      const download = element('button', 'button button-secondary')
      download.type = 'button'; download.dataset.detailsDownload = ''; download.disabled = Boolean(error) || !records.length
      download.innerHTML = icon('download')
      const downloadLabel = element('span', '', 'Download'); download.append(downloadLabel)
      actions.append(download, close); header.append(heading, actions)
      const scopeButton = element('button', 'button button-secondary', 'All QA (including unassigned)')
      if (scopeOptions) { scopeButton.type = 'button'; scopeButton.dataset.detailsScope = ''; heading.append(scopeButton) }
      const downloadStatus = element('p', 'inline-status inline-status-error data-details-download-status')
      downloadStatus.dataset.detailsDownloadStatus = ''; downloadStatus.hidden = true
      downloadStatus.setAttribute('role', 'status')
      let active = true; let downloading = false
      download.addEventListener('click', async () => {
        if (!active || downloading || download.disabled) return
        downloading = true; download.disabled = true; downloadLabel.textContent = 'Preparing…'; downloadStatus.hidden = true
        const downloadScope = scopeGeneration
        const exportScope = allQa ? `${scope} · All QA (including unassigned)` : scope
        const exportTitle = allQa ? `${title} · All QA (including unassigned)` : title
        const exportUpdatedAt = updatedAt; const exportValue = value == null || allQa ? total : value
        try {
          const exported = loadDownload ? await loadDownload({ allQa, signal: controller.signal }) : records
          if (!active || downloadScope !== scopeGeneration) return
          const exportColumns = downloadColumns ?? columns
          const Workbook = await loadWorkbook()
          if (!active) return
          const workbook = new Workbook()
          const sheet = workbook.addWorksheet('Details')
          sheet.columns = exportColumns.map(column => ({ header: column.label, key: column.key, width: 24 }))
          sheet.addRows(exported.map(row => exportColumns.map(column => {
            const value = row[column.key]
            return value == null || value === '' ? '—' : typeof value === 'number' ? value : String(value)
          })))
          workbook.addWorksheet('Context').addRows([
            ['Title', exportTitle], ['Scope', exportScope], ['Value', exportValue],
            ['Records', exported.length], ['Data updated', exportUpdatedAt || 'Unavailable'],
          ])
          const buffer = await workbook.xlsx.writeBuffer()
          if (active && downloadScope === scopeGeneration) saveFile(buffer, `card-details-${new Date().toISOString().replace(/[:T]/g, '-').split('.')[0]}.xlsx`)
        } catch (failure) {
          if (active && downloadScope === scopeGeneration && failure.name !== 'AbortError') { downloadStatus.textContent = failure.message || 'Download failed.'; downloadStatus.hidden = false }
        } finally {
          downloading = false
          if (active) { download.disabled = Boolean(currentError) || !total; downloadLabel.textContent = 'Download' }
        }
      })
      const amount = element('div', 'data-details-value', value == null ? `${records.length} records` : String(value))
      const description = element('p', 'card-subtitle', note)
      description.hidden = !note
      const body = element('div', 'report-table-wrap data-details-body')
      const table = element('table', 'report-table'); const thead = element('thead'); const labels = element('tr')
      for (const column of columns) labels.append(element('th', '', column.label))
      if (loadActions) labels.append(element('th', '', 'Actions'))
      thead.append(labels); const tbody = element('tbody'); table.append(thead, tbody); body.append(table)
      const empty = element('p', 'card-subtitle', error || 'No records in this range.')
      body.append(empty)
      const footer = element('footer', 'data-details-footer')
      const freshness = element('span', 'card-subtitle', `Data updated: ${updatedAt || 'Unavailable'}`)
      const navigation = element('div', 'filter-actions')
      const previous = element('button', 'button button-secondary', 'Previous'); previous.type = 'button'
      const next = element('button', 'button button-secondary', 'Next'); next.type = 'button'; next.dataset.detailsNext = ''
      const pageLabel = element('span', 'card-subtitle'); pageLabel.setAttribute('aria-live', 'polite')
      navigation.append(previous, pageLabel, next); footer.append(freshness, navigation)
      dialog.append(header, amount, description, downloadStatus, body, footer)
      let page = 0
      const pageSize = 25
      const render = () => {
        tbody.replaceChildren()
        for (const row of loadPage ? records : records.slice(page * pageSize, (page + 1) * pageSize)) {
          const tr = element('tr')
          for (const column of columns) {
            const td = element('td'); const text = row[column.key] == null || row[column.key] === '' ? '—' : String(row[column.key])
            const url = row[column.link]
            if (url && /^https?:\/\//i.test(url)) {
              const link = element('a', '', text); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; td.append(link)
            } else td.textContent = text
            tr.append(td)
          }
          if (loadActions) {
            const cell = element('td'); const expand = element('button', 'button button-secondary', 'View actions')
            expand.type = 'button'; expand.dataset.detailsExpand = ''; cell.append(expand); tr.append(cell)
            expand.addEventListener('click', () => expandRow(row, tr, expand))
          }
          tbody.append(tr)
        }
        empty.textContent = currentError || 'No records in this range.'
        empty.hidden = Boolean(records.length); table.hidden = !records.length
        previous.disabled = page === 0; next.disabled = (page + 1) * pageSize >= total
        pageLabel.textContent = `${total ? page * pageSize + 1 : 0}–${Math.min((page + 1) * pageSize, total)} / ${total}`
      }
      function expandRow(row, tr, button) {
        if (tr.nextElementSibling?.classList.contains('data-details-expanded')) {
          tr.nextElementSibling.remove(); button.textContent = 'View actions'; return
        }
        const expanded = element('tr', 'data-details-expanded'); const cell = element('td'); cell.colSpan = columns.length + 1
        const list = element('div', 'data-details-events'); list.dataset.detailsEvents = ''
        const navigation = element('div', 'filter-actions')
        const prev = element('button', 'button button-secondary', 'Previous actions'); prev.type = 'button'
        const next = element('button', 'button button-secondary', 'Next actions'); next.type = 'button'
        const status = element('span', 'card-subtitle'); navigation.append(prev, status, next)
        cell.append(list, navigation); expanded.append(cell); tr.after(expanded); button.textContent = 'Hide actions'
        let actionPage = 0; let request = 0
        const show = async () => {
          const generation = ++request; prev.disabled = next.disabled = true; list.textContent = 'Loading actions…'
          try {
            const result = await loadActions(row, { page: actionPage, allQa, signal: controller.signal })
            if (!active || !expanded.isConnected || generation !== request) return
            list.replaceChildren()
            for (const item of result.rows) {
              const action = element('article', 'data-details-event')
              action.append(element('p', 'card-subtitle', `${item.eventKind} · ${item.personName || item.person || '—'} · ${item.createdAt || 'Unavailable'}`))
              const payload = item.payload ?? {}
              if (item.eventKind === 'attachment') {
                const url = payload.content || payload.self
                const name = payload.filename || 'Unavailable'
                if (url && /^https?:\/\//i.test(url)) {
                  const link = element('a', '', name); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; action.append(link)
                } else action.append(element('p', '', name))
                action.append(element('p', 'card-subtitle', `${payload.mimeType || '—'} · ${payload.size ?? '—'} bytes`))
              } else {
                const raw = item.eventKind === 'verify' ? (payload.items ?? []).map(change => `${change.fromString || change.from || '—'} → ${change.toString || change.to || '—'}`).join('\n') : payload.body
                if (item.eventKind === 'comment' && typeof item.bodyHtml === 'string') {
                  const rich = element('div', 'data-details-event-markup')
                  rich.innerHTML = item.bodyHtml // HTML is sanitized at the authenticated details response boundary.
                  action.append(rich)
                } else action.append(element('p', 'data-details-event-body', typeof raw === 'string' ? raw : JSON.stringify(raw ?? null, null, 2)))
              }
              list.append(action)
            }
            status.textContent = `${result.total ? actionPage * pageSize + 1 : 0}–${Math.min((actionPage + 1) * pageSize, result.total)} / ${result.total}`
            prev.disabled = actionPage === 0; next.disabled = (actionPage + 1) * pageSize >= result.total
          } catch (failure) {
            if (active && expanded.isConnected && generation === request && failure.name !== 'AbortError') list.textContent = failure.message || 'Actions unavailable.'
          }
        }
        prev.addEventListener('click', () => { actionPage--; void show() })
        next.addEventListener('click', () => { actionPage++; void show() })
        void show()
      }
      async function readPage() {
        const generation = ++requestGeneration
        records = []; total = 0; currentError = 'Loading details…'; render()
        previous.disabled = next.disabled = download.disabled = true
        try {
          const result = await loadPage({ page, allQa, signal: controller.signal })
          if (!active || generation !== requestGeneration) return
          records = result.rows; total = result.total; currentError = ''
          updatedAt = result.updatedAt || updatedAt
          freshness.textContent = `Data updated: ${updatedAt || 'Unavailable'}`
          if (value == null || allQa) amount.textContent = `${allQa ? 'All QA · ' : ''}${total} Jira–QA records`
          else amount.textContent = String(value)
          if (result.totals) description.textContent = `${note} QA comments: ${result.totals.comment}; QA uploads: ${result.totals.attachment}; QA Verify histories: ${result.totals.verify}.`
        } catch (failure) {
          if (!active || generation !== requestGeneration || failure.name === 'AbortError') return
          records = []; total = 0; currentError = failure.message || 'Details unavailable.'
        }
        if (active && generation === requestGeneration) { render(); download.disabled = Boolean(currentError) || !total }
      }
      scopeButton.addEventListener('click', () => {
        allQa = !allQa; page = 0; scopeGeneration++; controller.abort(); controller = new AbortController()
        heading.querySelector('h2').textContent = allQa ? `${title} · All QA (including unassigned)` : title
        dialog.setAttribute('aria-label', `${heading.querySelector('h2').textContent} details`)
        heading.querySelector('.card-subtitle').textContent = allQa ? `${scope} · All QA (including unassigned)` : scope
        scopeButton.textContent = allQa ? 'Return to clicked scope' : 'All QA (including unassigned)'
        void readPage()
      })
      const cleanup = () => {
        active = false; controller.abort()
        dialog.remove()
        if (activeClose === closeOwned) activeClose = undefined
        closeOwned = undefined
        if (trigger?.isConnected) trigger.focus({ preventScroll: true })
      }
      closeOwned = () => { dialog.close(); cleanup() }
      activeClose = closeOwned
      close.addEventListener('click', () => closeOwned?.())
      dialog.addEventListener('cancel', event => { event.preventDefault(); closeOwned?.() })
      previous.addEventListener('click', () => { page--; if (loadPage) void readPage(); else render() })
      next.addEventListener('click', () => { page++; if (loadPage) void readPage(); else render() })
      render(); document.body.append(dialog); dialog.showModal()
      if (loadPage) void readPage()
    },
    destroy() { closeOwned?.() },
  }
}
