import { icon } from '../icons.js'
const SCOPE = 'dashboard/layout'
export const DEFAULT_DASHBOARD_LAYOUT = Object.freeze([
  Object.freeze({ id: 'role-workload-default', type: 'role-workload', x: 0, y: 0, w: 24, h: 21, config: Object.freeze({}) }),
  Object.freeze({ id: 'jira-self-test-default', type: 'jira-statistics-self-test', x: 0, y: 21, w: 24, h: 16, config: Object.freeze({ cardKey: 'self-test' }) }),
  Object.freeze({ id: 'jira-task-default', type: 'jira-statistics-task', x: 0, y: 37, w: 24, h: 16, config: Object.freeze({ cardKey: 'task' }) }),
  Object.freeze({ id: 'jira-customer-feedback-default', type: 'jira-statistics-customer-feedback', x: 0, y: 53, w: 24, h: 16, config: Object.freeze({ cardKey: 'customer-feedback' }) }),
])

function cloneLayout(layout) { return layout.map(item => ({ ...item, config: { ...(item.config ?? {}) } })) }
function positiveInteger(value) { return Number.isInteger(value) && value >= 0 }

export function normalizeLayout(layout, registry) {
  const normalized = []
  const errors = []
  for (const item of Array.isArray(layout) ? layout : []) {
    const definition = registry.get(item?.type)
    const valid = definition && typeof item.id === 'string' && item.id
      && positiveInteger(item.x) && positiveInteger(item.y)
      && Number.isInteger(item.w) && item.w > 0 && Number.isInteger(item.h) && item.h > 0
      && item.x + item.w <= 24
    if (!valid) { errors.push(`Invalid dashboard widget: ${item?.id ?? item?.type ?? 'unknown'}`); continue }
    normalized.push({ id: item.id, type: item.type, x: item.x, y: item.y, w: item.w, h: item.h, config: { ...(item.config ?? {}) } })
  }
  return { layout: normalized, errors }
}

export function createDashboardGrid({ root, registry, preferenceApi, gridFactory, widgetConfig = async () => ({}) }) {
  let grid
  let baseline = []
  let editing = false
  let restoringDefault = false
  let destroyed = false
  const mounted = new Map()

  function setEditing(value) {
    editing = value
    root.querySelector('[data-edit-dashboard]').hidden = value
    root.querySelector('[data-edit-actions]').hidden = !value
    root.querySelector('.dashboard-workspace')?.classList.toggle('is-editing', value)
    const picker = root.querySelector('[data-widget-picker]')
    if (!value && picker) {
      picker.hidden = true
      root.querySelector('[data-add-widget]')?.setAttribute('aria-expanded', 'false')
    }
    grid?.enableMove(value)
  }

  function disposeWidgets() {
    for (const { instance, resizeObserver } of mounted.values()) {
      resizeObserver?.disconnect()
      instance.destroy()
    }
    mounted.clear()
  }

  async function mountWidget(item) {
    const definition = registry.get(item.type)
    const element = document.createElement('section')
    element.className = 'grid-stack-item dashboard-widget'
    element.dataset.widgetId = item.id
    element.dataset.widgetType = item.type
    element.innerHTML = `<div class="grid-stack-item-content"><div class="card dashboard-widget-card"><header class="dashboard-widget-head"><span class="dashboard-drag-handle" title="Drag widget" aria-label="Drag widget">${icon('drag')}</span><strong>${definition.title}</strong><button type="button" class="icon-button" data-remove-widget aria-label="Remove ${definition.title}">${icon('close')}</button></header><div class="dashboard-widget-body"></div></div></div>`
    root.querySelector('.grid-stack').append(element)
    grid.makeWidget(element, { x: item.x, y: item.y, w: definition.defaultW, h: item.h,
      noResize: true, sizeToContent: true,
      id: item.id, type: item.type, config: item.config })
    const instance = definition.create()
    const card = element.querySelector('.dashboard-widget-card')
    const resizeObserver = typeof ResizeObserver === 'function'
      ? new ResizeObserver(() => grid?.resizeToContent(element)) : null
    mounted.set(item.id, { instance, element, item, resizeObserver })
    instance.mount(element.querySelector('.dashboard-widget-body'), await widgetConfig(item.type, item.config))
    grid.resizeToContent(element)
    resizeObserver?.observe(card)
    element.querySelector('[data-remove-widget]').addEventListener('click', () => {
      if (!editing) return
      resizeObserver?.disconnect(); instance.destroy(); mounted.delete(item.id); grid.removeWidget(element)
    })
  }

  async function render(layout) {
    disposeWidgets()
    grid.removeAll(true)
    for (const item of layout) await mountWidget(item)
  }

  function savedLayout() {
    return grid.save(false).map(node => {
      const element = node.el
      const id = node.id ?? element?.dataset.widgetId
      const known = mounted.get(id)?.item
      const type = node.type ?? element?.dataset.widgetType ?? known?.type
      const definition = registry.get(type)
      return { id, type,
        x: node.x, y: node.y,
        w: definition?.defaultW,
        h: node.h,
        config: { ...(known?.config ?? {}) } }
    })
  }

  async function save() {
    try {
      const result = normalizeLayout(savedLayout(), registry)
      if (result.errors.length) throw new Error(result.errors.join('\n'))
      if (restoringDefault) await preferenceApi.reset(SCOPE)
      else await preferenceApi.put(SCOPE, { layout: result.layout })
      baseline = cloneLayout(result.layout)
      restoringDefault = false
      setEditing(false)
      root.querySelector('[data-dashboard-status]').textContent = ''
    } catch (error) {
      root.querySelector('[data-dashboard-status]').textContent = error.message || 'Dashboard layout could not be saved.'
    }
  }

  return {
    async start() {
      root.innerHTML = `<section class="dashboard-workspace"><header class="report-page-head"><div><div class="eyebrow">Dashboard</div><h1>Dashboard</h1></div><div class="report-actions"><button class="button button-primary" type="button" data-edit-dashboard>Edit Dashboard</button><div data-edit-actions hidden><button class="button button-secondary" type="button" data-add-widget aria-expanded="false" aria-controls="dashboard-widget-picker">Add widget</button><button class="button button-secondary" type="button" data-restore-dashboard>Restore default</button><button class="button button-secondary" type="button" data-cancel-dashboard>Cancel</button><button class="button button-primary" type="button" data-save-dashboard>Save</button></div></div></header><div id="dashboard-widget-picker" class="card dashboard-widget-picker" data-widget-picker role="dialog" aria-label="Available Dashboard widgets" hidden><strong>Available widgets</strong><div data-widget-options></div></div><div class="inline-status" data-dashboard-status aria-live="polite"></div><div class="grid-stack"></div></section>`
      const picker = root.querySelector('[data-widget-picker]')
      for (const definition of registry.list()) {
        const button = document.createElement('button')
        button.type = 'button'
        button.className = 'button button-secondary'
        button.dataset.addWidgetType = definition.type
        button.textContent = definition.title
        button.addEventListener('click', () => {
          const id = globalThis.crypto?.randomUUID?.() ?? `widget-${Date.now()}`
          picker.hidden = true
          root.querySelector('[data-add-widget]').setAttribute('aria-expanded', 'false')
          void mountWidget({ id, type: definition.type, x: 0, y: 0, w: definition.defaultW, h: 1, config: {} })
        })
        picker.querySelector('[data-widget-options]').append(button)
      }
      grid = gridFactory({ column: 24, cellHeight: 30, margin: 8, handle: '.dashboard-drag-handle',
        disableDrag: true, disableResize: true, columnOpts: { breakpoints: [{ w: 700, c: 1 }, { w: 1180, c: 12 }], layout: 'moveScale' } }, root.querySelector('.grid-stack'))
      let response
      try { response = await preferenceApi.get(SCOPE) } catch (error) {
        root.querySelector('[data-dashboard-status]').textContent = error.message || 'Dashboard layout could not be loaded.'
        return
      }
      if (destroyed) return
      const source = response?.items?.layout ?? DEFAULT_DASHBOARD_LAYOUT
      const result = normalizeLayout(source, registry)
      root.querySelector('[data-dashboard-status]').textContent = result.errors.join(' ')
      baseline = cloneLayout(result.layout)
      await render(baseline)
      setEditing(false)
      root.querySelector('[data-edit-dashboard]').addEventListener('click', () => { restoringDefault = false; setEditing(true) })
      root.querySelector('[data-cancel-dashboard]').addEventListener('click', () => { restoringDefault = false; void render(baseline).then(() => setEditing(false)) })
      root.querySelector('[data-restore-dashboard]').addEventListener('click', () => { restoringDefault = true; void render(cloneLayout(DEFAULT_DASHBOARD_LAYOUT)) })
      root.querySelector('[data-save-dashboard]').addEventListener('click', () => { void save() })
      root.querySelector('[data-add-widget]').addEventListener('click', () => {
        picker.hidden = !picker.hidden
        root.querySelector('[data-add-widget]').setAttribute('aria-expanded', String(!picker.hidden))
      })
    },
    destroy() { destroyed = true; disposeWidgets(); grid?.destroy(false); grid = null; baseline = [] },
  }
}
