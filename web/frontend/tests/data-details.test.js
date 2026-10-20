// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest'
import ExcelJS from 'exceljs'
import { createDataDetails, issueColumns, projectColumns } from '../src/data-details.js'

beforeEach(() => {
  document.body.innerHTML = '<button id="trigger">24</button>'
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', '') }
  HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); this.dispatchEvent(new Event('close')) }
})
it('keeps the clicked results fixed, pages all rows, escapes values and restores focus', () => {
  const details = createDataDetails()
  const trigger = document.querySelector('button'); trigger.focus()
  const rows = Array.from({ length: 27 }, (_, i) => ({ key: `TV-${i}`, summary: '<script>unsafe</script>' }))
  details.open({ title: 'Bugs', scope: 'TV · Monthly · Previous', value: 27, rows,
    columns: [{ key: 'key', label: 'Key' }, { key: 'summary', label: 'Summary' }], trigger })
  rows.length = 0
  expect(document.querySelectorAll('tbody tr')).toHaveLength(25)
  expect(document.querySelector('dialog script')).toBeNull()
  document.querySelector('[data-details-next]').click()
  expect(document.querySelectorAll('tbody tr')).toHaveLength(2)
  expect(document.querySelector('tbody').textContent).toContain('TV-26')
  document.querySelector('dialog').dispatchEvent(new Event('cancel', { cancelable: true }))
  expect(document.querySelector('dialog')).toBeNull()
  expect(document.activeElement).toBe(trigger)
  details.destroy()
})
it('only opens one panel and releases it on owner teardown', () => {
  const first = createDataDetails(); const second = createDataDetails()
  const config = { title: 'Projects', rows: [], columns: [] }
  first.open(config); second.open(config)
  expect(document.querySelectorAll('dialog')).toHaveLength(1)
  first.destroy()
  expect(document.querySelectorAll('dialog')).toHaveLength(1)
  second.destroy()
  expect(document.querySelectorAll('dialog')).toHaveLength(0)
})

it('links Jira keys and summaries and project names only when the owner supplied a safe URL', () => {
  const details = createDataDetails()
  details.open({ title: 'Jira', columns: issueColumns, rows: [
    { key: 'TV-1', summary: '<b>Jira title</b>', webUrl: 'https://jira.example/TV-1' },
    { key: 'TV-2', summary: 'Missing URL' },
    { key: 'TV-3', summary: 'Unsafe URL', webUrl: 'javascript:alert(1)' },
  ] })
  const links = [...document.querySelectorAll('tbody a')]
  expect(links.map(link => link.textContent)).toEqual(['TV-1', '<b>Jira title</b>'])
  expect(links.every(link => link.target === '_blank' && link.rel.includes('noopener') && link.rel.includes('noreferrer'))).toBe(true)
  expect(document.querySelector('tbody b')).toBeNull()
  details.open({ title: 'Projects', columns: projectColumns, rows: [{ name: 'Project', url: 'https://wiki.example/1' }, { name: 'Without URL' }] })
  expect(document.querySelectorAll('tbody a')).toHaveLength(1)
  expect(document.querySelector('tbody a').href).toBe('https://wiki.example/1')
  details.destroy()
})

it('downloads every fixed record and displayed column as XLSX, preserving strings as text', async () => {
  const saveFile = vi.fn()
  const details = createDataDetails({ loadWorkbook: async () => ExcelJS.Workbook, saveFile })
  const rows = Array.from({ length: 27 }, (_, index) => ({ key: `TV-${index}`, summary: index === 26 ? '=HYPERLINK("https://example")' : '<b>Raw title</b>', count: index }))
  details.open({ title: 'Bugs', scope: 'TV · Previous', value: 27, updatedAt: '2026-10-19', rows,
    columns: [{ key: 'key', label: 'Key' }, { key: 'summary', label: 'Summary' }, { key: 'count', label: 'Contribution' }] })
  rows[26].summary = 'Changed after open'; rows.length = 0
  document.querySelector('[data-details-next]').click()
  expect(document.querySelectorAll('tbody tr')).toHaveLength(2)
  document.querySelector('[data-details-download]').click()
  await vi.waitFor(() => expect(saveFile).toHaveBeenCalledOnce())
  const [buffer, filename] = saveFile.mock.calls[0]
  expect(filename).toMatch(/^card-details-.*\.xlsx$/)
  const workbook = new ExcelJS.Workbook(); await workbook.xlsx.load(buffer)
  const sheet = workbook.getWorksheet('Details')
  expect(sheet.rowCount).toBe(28)
  expect(sheet.getRow(1).values.slice(1)).toEqual(['Key', 'Summary', 'Contribution'])
  expect(sheet.getCell('A28').value).toBe('TV-26')
  expect(sheet.getCell('B28').value).toBe('=HYPERLINK("https://example")')
  expect(sheet.getCell('C28').value).toBe(26)
  expect(workbook.getWorksheet('Context').getCell('B2').value).toBe('TV · Previous')
  details.destroy()
})

it('prevents duplicate downloads and abandons a pending download when closed', async () => {
  let finish
  const loadWorkbook = vi.fn(() => new Promise(resolve => { finish = resolve }))
  const saveFile = vi.fn()
  const details = createDataDetails({ loadWorkbook, saveFile })
  details.open({ title: 'Projects', rows: [{ name: 'Project' }], columns: projectColumns })
  const button = document.querySelector('[data-details-download]')
  button.click(); button.click()
  expect(loadWorkbook).toHaveBeenCalledOnce()
  expect(button.disabled).toBe(true)
  details.destroy(); finish(ExcelJS.Workbook)
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(saveFile).not.toHaveBeenCalled()
})

it('shows download failures without dismissing the records and allows retry', async () => {
  const loadWorkbook = vi.fn().mockRejectedValue(new Error('Export failed'))
  const details = createDataDetails({ loadWorkbook })
  details.open({ title: 'Projects', rows: [{ name: 'Project' }], columns: projectColumns })
  const button = document.querySelector('[data-details-download]'); button.click()
  await vi.waitFor(() => expect(document.querySelector('[data-details-download-status]').textContent).toBe('Export failed'))
  expect(button.disabled).toBe(false)
  expect(document.querySelector('tbody').textContent).toContain('Project')
  details.destroy()
})

it('does not save an XLSX buffer that finishes after the panel is replaced', async () => {
  let finish
  class Workbook extends ExcelJS.Workbook {
    get xlsx() { return { writeBuffer: () => new Promise(resolve => { finish = resolve }) } }
  }
  const saveFile = vi.fn()
  const details = createDataDetails({ loadWorkbook: async () => Workbook, saveFile })
  details.open({ title: 'Old scope', rows: [{ name: 'Old project' }], columns: projectColumns })
  document.querySelector('[data-details-download]').click()
  await vi.waitFor(() => expect(finish).toBeTypeOf('function'))
  details.open({ title: 'New scope', rows: [{ name: 'New project' }], columns: projectColumns })
  finish(new Uint8Array())
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(saveFile).not.toHaveBeenCalled()
  expect(document.querySelector('dialog tbody').textContent).toContain('New project')
  details.destroy()
})


it('loads summary pages lazily, expands safe text, switches scope and downloads all actions', async () => {
  const saveFile = vi.fn()
  const details = createDataDetails({ loadWorkbook: async () => ExcelJS.Workbook, saveFile })
  const loadPage = vi.fn(async ({ page, allQa }) => ({ total: allQa ? 1 : 26, updatedAt: '2026-10-20T12:00:00Z', rows: [{ issueId: String(page), key: `TV-${page}`, person: 'qa' }] }))
  const loadActions = vi.fn(async () => ({ total: 1, rows: [{ eventKind: 'comment', createdAt: '2026-01-01', payload: { body: '<img src=x onerror=bad()>\nRaw' } },
    { eventKind: 'attachment', payload: { filename: 'QA image.png', content: 'https://jira.example/file', created: '2026-01-01' } },
    { eventKind: 'verify', payload: { items: [{ fromString: 'Resolved', toString: 'Verified' }] } }] }))
  const loadDownload = vi.fn(async () => Array.from({ length: 27 }, (_, index) => ({ body: `Original-${index}` })))
  details.open({ title: 'Comments', columns: [{ key: 'key', label: 'Jira' }], loadPage, loadActions, loadDownload,
    downloadColumns: [{ key: 'body', label: 'Original' }], scopeOptions: true })
  await vi.waitFor(() => expect(document.querySelector('tbody').textContent).toContain('TV-0'))
  expect(loadActions).not.toHaveBeenCalled()
  document.querySelector('[data-details-expand]').click()
  await vi.waitFor(() => expect(document.querySelector('[data-details-events]').textContent).toContain('<img src=x onerror=bad()>'))
  expect(document.querySelector('[data-details-events] img')).toBeNull()
  expect(document.querySelector('[data-details-events]').textContent).toContain('Resolved → Verified')
  expect(document.querySelector('[data-details-events] a').href).toBe('https://jira.example/file')
  document.querySelector('[data-details-next]').click()
  await vi.waitFor(() => expect(document.querySelector('tbody').textContent).toContain('TV-1'))
  document.querySelector('[data-details-scope]').click()
  expect(document.querySelector('tbody').textContent).not.toContain('TV-1')
  expect(document.querySelector('h2').textContent).toContain('All QA')
  await vi.waitFor(() => expect(loadPage.mock.calls.at(-1)[0].allQa).toBe(true))
  await vi.waitFor(() => expect(document.querySelector('[data-details-download]').disabled).toBe(false))
  document.querySelector('[data-details-download]').click()
  await vi.waitFor(() => expect(saveFile).toHaveBeenCalledOnce())
  const book = new ExcelJS.Workbook(); await book.xlsx.load(saveFile.mock.calls[0][0])
  expect(book.getWorksheet('Details').rowCount).toBe(28)
  expect(book.getWorksheet('Context').getCell('B2').value).toContain('All QA (including unassigned)')
  expect(book.getWorksheet('Context').getCell('B3').value).toBe(1)
  expect(book.getWorksheet('Context').getCell('B5').value).toBe('2026-10-20T12:00:00Z')
  details.destroy()
})

it('ignores late summary results and cancels requests when closed', async () => {
  let finish; let signal
  const details = createDataDetails()
  details.open({ title: 'Verify', columns: [], loadPage: options => { signal = options.signal; return new Promise(resolve => { finish = resolve }) } })
  details.destroy()
  expect(signal.aborted).toBe(true)
  finish({ rows: [{ summary: 'late' }], total: 1 })
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(document.querySelector('dialog')).toBeNull()
})

it('renders only server-provided comment HTML while keeping structured bodies and Verify as text', async () => {
  const details = createDataDetails()
  details.open({ title: 'Comments', columns: [{ key: 'key', label: 'Jira' }],
    loadPage: async () => ({ total: 1, rows: [{ key: 'TV-1' }] }),
    loadActions: async () => ({ total: 3, rows: [
      { eventKind: 'comment', bodyHtml: '<table><tbody><tr><td>one<br>two</td></tr></tbody></table>', payload: { body: '||raw||' } },
      { eventKind: 'comment', payload: { body: { text: '<img src=x>' } } },
      { eventKind: 'verify', bodyHtml: '<b>ignored</b>', payload: { items: [{ fromString: '<b>Resolved</b>', toString: 'Verified' }] } },
    ] }) })
  await vi.waitFor(() => expect(document.querySelector('[data-details-expand]')).not.toBeNull())
  document.querySelector('[data-details-expand]').click()
  await vi.waitFor(() => expect(document.querySelector('.data-details-event table')).not.toBeNull())
  expect(document.querySelector('.data-details-event td').innerHTML).toBe('one<br>two')
  expect(document.querySelector('.data-details-events img')).toBeNull()
  expect(document.querySelector('.data-details-events b')).toBeNull()
  expect(document.querySelector('.data-details-events').textContent).toContain('<b>Resolved</b>')
  details.destroy()
})
