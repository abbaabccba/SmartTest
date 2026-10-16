// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createRoleWorkloadWidget, ROLE_WORKLOAD_LAYOUT } from '../src/widgets/role-workload.js'

const productSpaces = [
  { value: 'DOPL', label: 'China Operator' },
  { value: 'SDPL', label: 'Smart Device' },
  { value: 'TV', label: 'TV' },
  { value: 'OOPL', label: 'Global Operator & STB' },
]

describe('RoleWorkloadWidget', () => {
  beforeEach(() => { document.body.innerHTML = '<div id="root"></div>' })

  it('keeps the proven current full-width layout as its minimum width', () => {
    expect(ROLE_WORKLOAD_LAYOUT).toEqual({ defaultW: 24 })
  })

  it('uses the project resource statistics title when it owns the heading', () => {
    const widget = createRoleWorkloadWidget()
    widget.mount(document.querySelector('#root'), { productSpaces, ownerHierarchy: [] })
    expect(document.querySelector('.workload-heading').textContent).toContain('Project Resource Statistics')
  })

  it('keeps the Projects workload sorting, identity cleanup, switching, and chart lifecycle', () => {
    const charts = []
    const chartFactory = vi.fn((canvas, config) => {
      const chart = { config,
        get data() { return config.data }, set data(value) { config.data = value },
        get options() { return config.options }, set options(value) { config.options = value },
        update: vi.fn(), reset: vi.fn(), stop: vi.fn(), destroy: vi.fn(),
      }; charts.push(chart); return chart
    })
    const widget = createRoleWorkloadWidget({ chartFactory })
    widget.mount(document.querySelector('#root'), {
      productSpaces,
      ownerHierarchy: [{ role: 'FAE QA', people: [
        { name: '2c93-user', identity: '2c93-user', projects: [{ space_key: 'TV' }] },
        { name: 'Alice', identity: 'alice-key', projects: [{ space_key: 'DOPL' }, { space_key: 'TV' }] },
        { name: 'Bob', identity: 'bob-key', projects: [{ space_key: 'TV' }, { space_key: 'TV' }] },
      ] }],
    })
    expect(charts.at(-1).config.data.labels).toEqual(['Alice'])
    expect(document.body.textContent).not.toContain('2c93-user')
    ;[...document.querySelectorAll('[data-product-line-segments] button')]
      .find(button => button.textContent === 'TV').click()
    expect(charts.at(-1).config.data.labels).toEqual(['Bob', '2c93-user', 'Alice'])
    expect(charts.at(-1).config.data.datasets[0].data).toEqual([2, 1, 1])
    expect(chartFactory).toHaveBeenCalledOnce()
    expect(charts[0].destroy).not.toHaveBeenCalled()
    expect(charts[0].reset).toHaveBeenCalledOnce()
    widget.destroy()
    expect(charts.at(-1).destroy).toHaveBeenCalledOnce()
    expect(document.querySelector('#root').childElementCount).toBe(0)
  })

  it('uses Unknown only for blanks and preserves every nonempty member value', () => {
    let chartConfig
    const values = ['NA', 'N/A', '-', '<TBD>', 'TBD', 'fan.xu', 'Fan Xu', 'Unknown']
    const people = values.map((name, index) => ({ name, identity: name,
      projects: Array(index + 1).fill({ space_key: 'DOPL' }) }))
    people.push({ name: ' ', identity: ' ', projects: Array(9).fill({ space_key: 'DOPL' }) })
    people.push({ name: '', identity: 'account-only', projects: Array(10).fill({ space_key: 'DOPL' }) })
    const widget = createRoleWorkloadWidget({ chartFactory: (_canvas, config) => {
      chartConfig = config; return { destroy() {} }
    } })
    widget.mount(document.querySelector('#root'), { productSpaces,
      ownerHierarchy: [{ role: 'Major FAE QA', people }] })
    expect(chartConfig.data.labels).toEqual(['account-only', 'Unknown', ...values.toReversed()])
    expect(chartConfig.data.datasets[0].data).toEqual([10, 9, 8, 7, 6, 5, 4, 3, 2, 1])
    widget.destroy()
  })

  it('keeps the existing bounded viewport and per-person chart height', () => {
    const widget = createRoleWorkloadWidget({ chartFactory: () => ({ destroy() {} }) })
    widget.mount(document.querySelector('#root'), {
      productSpaces,
      ownerHierarchy: [{ role: 'FAE QA', people: Array.from({ length: 20 }, (_, index) => ({
        name: `Member ${index}`, identity: `member-${index}`, projects: [{ space_key: 'DOPL' }],
      })) }],
    })
    expect(document.querySelector('.workload-chart-surface').style.height).toBe('756px')
    expect(document.querySelector('.workload-chart-scroll')).not.toBeNull()
  })

  it('keeps every person label visible in a two-person ranking', () => {
    let options
    const widget = createRoleWorkloadWidget({ chartFactory: (_canvas, config) => {
      options = config.options
      expect(config.data.labels).toEqual(['Lingling Yu', 'Fan Xu'])
      return { destroy() {} }
    } })
    widget.mount(document.querySelector('#root'), {
      productSpaces,
      ownerHierarchy: [{ role: 'FAE QA', people: [
        { name: 'Lingling Yu', identity: 'lingling.yu', projects: Array(3).fill({ space_key: 'DOPL' }) },
        { name: 'Fan Xu', identity: 'fan.xu', projects: [{ space_key: 'DOPL' }] },
      ] }],
    })
    expect(options.scales.y.ticks.autoSkip).toBe(false)
    expect(document.querySelector('.workload-chart-surface').style.height).toBe('108px')
    widget.destroy()
  })

  it('shows the existing empty surface when project facts are unavailable', () => {
    const widget = createRoleWorkloadWidget()
    widget.mount(document.querySelector('#root'), { error: 'Local project facts API is unavailable.' })
    expect(document.querySelector('[data-workload-empty]').hidden).toBe(false)
    expect(document.querySelector('[data-workload-empty]').textContent).toBe('Local project facts API is unavailable.')
  })
})

it('passes full product names as visual identities while matching internal project values', () => {
  const root = document.createElement('div')
  const widget = createRoleWorkloadWidget()
  widget.mount(root, { productSpaces, ownerHierarchy: [] })
  expect([...root.querySelectorAll('[data-product-line-segments] button')].map(button => [button.dataset.value, button.dataset.productLine])).toEqual([
    ['DOPL', 'China Operator'], ['SDPL', 'Smart Device'], ['TV', 'TV'], ['OOPL', 'Global Operator & STB'],
  ])
  widget.destroy()
})
