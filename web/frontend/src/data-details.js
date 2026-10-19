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
    open({ title, scope = '', value, rows = [], columns, updatedAt, note = '', error = '', trigger = document.activeElement }) {
      activeClose?.()
      columns = columns.map(column => ({ ...column }))
      const records = rows.map(row => ({ ...row }))
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
      const downloadStatus = element('p', 'inline-status inline-status-error data-details-download-status')
      downloadStatus.dataset.detailsDownloadStatus = ''; downloadStatus.hidden = true
      downloadStatus.setAttribute('role', 'status')
      let active = true; let downloading = false
      download.addEventListener('click', async () => {
        if (!active || downloading || download.disabled) return
        downloading = true; download.disabled = true; downloadLabel.textContent = 'Preparing…'; downloadStatus.hidden = true
        try {
          const Workbook = await loadWorkbook()
          if (!active) return
          const workbook = new Workbook()
          const sheet = workbook.addWorksheet('Details')
          sheet.columns = columns.map(column => ({ header: column.label, key: column.key, width: 24 }))
          sheet.addRows(records.map(row => columns.map(column => {
            const value = row[column.key]
            return value == null || value === '' ? '—' : typeof value === 'number' ? value : String(value)
          })))
          workbook.addWorksheet('Context').addRows([
            ['Title', title], ['Scope', scope], ['Value', value == null ? records.length : String(value)],
            ['Records', records.length], ['Data updated', updatedAt || 'Unavailable'],
          ])
          const buffer = await workbook.xlsx.writeBuffer()
          if (active) saveFile(buffer, `card-details-${new Date().toISOString().replace(/[:T]/g, '-').split('.')[0]}.xlsx`)
        } catch (failure) {
          if (active) { downloadStatus.textContent = failure.message || 'Download failed.'; downloadStatus.hidden = false }
        } finally {
          downloading = false
          if (active) { download.disabled = false; downloadLabel.textContent = 'Download' }
        }
      })
      const amount = element('div', 'data-details-value', value == null ? `${records.length} records` : String(value))
      const description = element('p', 'card-subtitle', note)
      description.hidden = !note
      const body = element('div', 'report-table-wrap data-details-body')
      const table = element('table', 'report-table'); const thead = element('thead'); const labels = element('tr')
      for (const column of columns) labels.append(element('th', '', column.label))
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
        for (const row of records.slice(page * pageSize, (page + 1) * pageSize)) {
          const tr = element('tr')
          for (const column of columns) {
            const td = element('td'); const text = row[column.key] == null || row[column.key] === '' ? '—' : String(row[column.key])
            const url = row[column.link]
            if (url && /^https?:\/\//i.test(url)) {
              const link = element('a', '', text); link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; td.append(link)
            } else td.textContent = text
            tr.append(td)
          }
          tbody.append(tr)
        }
        empty.hidden = Boolean(records.length); table.hidden = !records.length
        previous.disabled = page === 0; next.disabled = (page + 1) * pageSize >= records.length
        pageLabel.textContent = `${records.length ? page * pageSize + 1 : 0}–${Math.min((page + 1) * pageSize, records.length)} / ${records.length}`
      }
      const cleanup = () => {
        active = false
        dialog.remove()
        if (activeClose === closeOwned) activeClose = undefined
        closeOwned = undefined
        if (trigger?.isConnected) trigger.focus({ preventScroll: true })
      }
      closeOwned = () => { dialog.close(); cleanup() }
      activeClose = closeOwned
      close.addEventListener('click', () => closeOwned?.())
      dialog.addEventListener('cancel', event => { event.preventDefault(); closeOwned?.() })
      previous.addEventListener('click', () => { page--; render() })
      next.addEventListener('click', () => { page++; render() })
      render(); document.body.append(dialog); dialog.showModal()
    },
    destroy() { closeOwned?.() },
  }
}
