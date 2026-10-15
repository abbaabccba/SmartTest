// @vitest-environment jsdom
import { expect, it, vi } from 'vitest'
import { createRankingCard } from '../src/widgets/ranking-card.js'
import { initializeTheme, initializeChartTheme } from '../src/theme.js'

// Chart.js captures RAF when its helpers load; keep scheduling controllable before imports.
const animationFrame = vi.hoisted(() => {
  const original = window.requestAnimationFrame.bind(window)
  const driver = { schedule: original, original }
  window.requestAnimationFrame = callback => driver.schedule(callback)
  return driver
})

it('keeps dimension groups independent while querying both selected values', () => {
  const root = document.createElement('div')
  const rowsFor = vi.fn(() => [])
  const config = {
    productLines: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }],
    modes: [{ value: 'x', label: 'X' }, { value: 'y', label: 'Y' }], rowsFor
  }
  const card = createRankingCard()
  card.mount(root, config)
  const products = [...root.querySelectorAll('[data-product-line-segments] button')]
  const modes = [...root.querySelectorAll('[data-mode-segments] button')]
  modes[1].click()
  expect([...root.querySelectorAll('[data-product-line-segments] button')]).toEqual(products)
  expect(products[0].classList.contains('active')).toBe(true)
  expect(rowsFor).toHaveBeenLastCalledWith('a', 'y')
  products[1].click()
  expect([...root.querySelectorAll('[data-mode-segments] button')]).toEqual(modes)
  expect(modes[1].classList.contains('active')).toBe(true)
  expect(rowsFor).toHaveBeenLastCalledWith('b', 'y')
  card.update({ ...config })
  expect([...root.querySelectorAll('[data-product-line-segments] button')]).toEqual(products)
  card.destroy()
})

it('grows initially and updates the same people without restarting from zero, resetting changed identities only', () => {
  const root = document.createElement('div')
  const chartFactory = vi.fn((_canvas, config) => ({
    data: config.data, options: config.options,
    update: vi.fn(), reset: vi.fn(), stop: vi.fn(), destroy: vi.fn()
  }))
  const card = createRankingCard({ chartFactory })
  let rows = [{ name: 'Alice', count: 2 }, { name: 'Bob', count: 1 }]
  const config = { productLines: [{ value: 'a', label: 'A' }], modes: [{ value: 'x', label: 'X' }], rowsFor: () => rows }
  card.mount(root, config)
  const chart = chartFactory.mock.results[0].value
  expect(chart.options.animation.duration).toBe(500)
  expect(chart.options.animation.delay({ type: 'data', dataIndex: 1 })).toBe(40)
  expect(chart.options.animation.delay({ type: 'data', dataIndex: 30 })).toBe(400)
  expect(chart.options.animation.delay({ type: 'dataset', dataIndex: 3 })).toBe(0)
  expect(chart.options.transitions.resize.animation.duration).toBe(500)
  expect(chart.options.animations.y.duration).toBe(0)
  rows = [{ name: 'Alice', count: 4 }, { name: 'Bob', count: 1 }]
  card.update(config)
  expect(chartFactory).toHaveBeenCalledOnce()
  expect(chart.data.datasets[0].data).toEqual([4, 1])
  expect(chart.update).toHaveBeenCalledOnce()
  expect(chart.reset).not.toHaveBeenCalled()
  rows = [{ name: 'Bob', count: 5 }, { name: 'Alice', count: 4 }]
  card.update(config)
  expect(chart.data.labels).toEqual(['Bob', 'Alice'])
  expect(chart.reset).toHaveBeenCalledOnce()
  expect(chart.update).toHaveBeenCalledWith('none')
  rows = []; card.update(config)
  expect(chart.destroy).toHaveBeenCalledOnce()
  card.destroy()
  expect(chart.destroy).toHaveBeenCalledOnce()
})

it('turns off chart animation when reduced motion is requested', () => {
  vi.stubGlobal('matchMedia', () => ({ matches: true }))
  const chartFactory = vi.fn((_canvas, config) => ({
    data: config.data, options: config.options,
    update: vi.fn(), reset: vi.fn(), stop: vi.fn(), destroy: vi.fn()
  }))
  const card = createRankingCard({ chartFactory })
  const config = { productLines: [{ value: 'a', label: 'A' }], modes: [{ value: 'x', label: 'X' }], rowsFor: () => [{ name: 'Alice', count: 1 }] }
  card.mount(document.createElement('div'), config)
  const chart = chartFactory.mock.results[0].value
  expect(chart.options.animation).toBe(false)
  expect(chart.options.transitions.resize.animation.duration).toBe(0)
  card.update(config)
  expect(chart.update).toHaveBeenCalledWith('none')
  expect(chart.reset).not.toHaveBeenCalled()
  card.destroy(); vi.unstubAllGlobals()
})

it('sorts comparison rows and labels each period with its own share of the displayed total', () => {
  const chartFactory = vi.fn((_canvas, config) => ({
    data: config.data, options: config.options,
    update: vi.fn(), reset: vi.fn(), stop: vi.fn(), destroy: vi.fn()
  }))
  const card = createRankingCard({ chartFactory })
  card.mount(document.createElement('div'), {
    productLines: [{ value: 'a', label: 'A' }], modes: [{ value: 'x', label: 'X' }],
    rowsFor: () => [
      { name: 'Zed', count: 2, previousCount: 1 },
      { name: 'Bob', count: 2, previousCount: 4 },
      { name: 'Amy', count: 2, previousCount: 4 },
    ],
  })
  const chart = chartFactory.mock.calls[0][1]
  expect(chart.data.labels).toEqual(['Amy', 'Bob', 'Zed'])
  expect(chart.data.datasets.map(dataset => ({ order: dataset.order, width: dataset.barThickness }))).toEqual([
    { order: 1, width: 12 }, { order: 2, width: 12 },
  ])
  expect(chart.data.datasets.every(dataset => dataset.grouped)).toBe(true)
  const percentage = chart.options.plugins.datalabels.labels.percentage.formatter
  expect(percentage(2, { datasetIndex: 0 })).toBe('33%（2/6）')
  expect(percentage(4, { datasetIndex: 1 })).toBe('44%（4/9）')
  card.destroy()
})

it('formats increase decrease flat and zero-baseline comparisons in labels and tooltips', () => {
  const chartFactory = vi.fn((_canvas, config) => ({
    data: config.data, options: config.options,
    update: vi.fn(), reset: vi.fn(), stop: vi.fn(), destroy: vi.fn()
  }))
  const card = createRankingCard({ chartFactory })
  card.mount(document.createElement('div'), {
    productLines: [{ value: 'a', label: 'A' }], modes: [{ value: 'x', label: 'X' }],
    ranges: { current: { start: '2026-09-01', end: null }, previous: { start: '2026-08-01', end: '2026-09-01' } },
    rowsFor: () => [
      { name: 'Increase', count: 6, previousCount: 3 },
      { name: 'Decrease', count: 2, previousCount: 5 },
      { name: 'Flat', count: 4, previousCount: 4 },
      { name: 'New', count: 3, previousCount: 0 },
    ],
  })
  const chart = chartFactory.mock.calls[0][1]
  const value = chart.options.plugins.datalabels.labels.value.formatter
  expect(value(6, { dataIndex: 0, datasetIndex: 0 })).toBe('↑ 3')
  expect(value(4, { dataIndex: 1, datasetIndex: 0 })).toBe('No change')
  expect(value(3, { dataIndex: 2, datasetIndex: 0 })).toBe('↑ 3')
  expect(value(2, { dataIndex: 3, datasetIndex: 0 })).toBe('↓ 3')
  expect(value(4, { dataIndex: 1, datasetIndex: 1 })).toBeNull()
  const tooltip = chart.options.plugins.tooltip.callbacks.afterBody([{ dataIndex: 3 }])
  expect(tooltip).toContain('Current: 2026-09-01 – now · 2')
  expect(tooltip).toContain('Previous: 2026-08-01 – 2026-09-01 · 5')
  expect(tooltip).toContain('Change: ↓ 3')
  expect(tooltip).not.toContain('%')
  card.destroy()
})

it('animates finite horizontal bar geometry with real Chart.js, including resize', async () => {
  let frame
  animationFrame.schedule = callback => { frame = callback; return 1 }
  vi.useFakeTimers()
  vi.setSystemTime(1000)
  const { Chart, registerables, BasicPlatform } = await import('chart.js')
  Chart.register(...registerables)
  class AnimationPlatform extends BasicPlatform { updateConfig() { } }
  let chart
  const card = createRankingCard({
    chartFactory: (_canvas, config) => {
      const canvas = { width: 800, height: 108, style: {}, getContext: () => context }
      const context = new Proxy({ canvas, measureText: text => ({ width: String(text).length * 7 }) }, {
        get: (target, key) => key in target ? target[key] : () => { },
      })
      chart = new Chart(canvas, {
        ...config, platform: AnimationPlatform,
        options: { ...config.options, responsive: false },
        plugins: [{ id: 'skip-rasterization', beforeDraw: () => false }],
      })
      return chart
    }
  })
  try {
    card.mount(document.createElement('div'), {
      productLines: [{ value: 'a', label: 'A' }], modes: [{ value: 'x', label: 'X' }],
      rowsFor: () => [{ name: 'Alice', count: 4 }, { name: 'Bob', count: 4 }],
    })
    const bars = chart.getDatasetMeta(0).data
    expect(bars[0].options.backgroundColor).toBe('rgba(54, 162, 235, 0.5)')
    vi.setSystemTime(1200); frame()
    expect(bars.every(bar => Number.isFinite(bar.x) && Number.isFinite(bar.width))).toBe(true)
    expect(bars[0].width).toBeGreaterThan(bars[1].width)
    expect(bars[1].width).toBeGreaterThan(0)
    chart.resize(800, 144)
    chart.update('resize')
    vi.setSystemTime(1400); frame()
    expect(bars.every(bar => Number.isFinite(bar.x) && Number.isFinite(bar.width))).toBe(true)
    vi.setSystemTime(2200); frame()
    expect(bars[0].width).toBeCloseTo(bars[1].width)
  } finally {
    card.destroy(); vi.useRealTimers(); vi.restoreAllMocks(); animationFrame.schedule = animationFrame.original
  }
})

it.each([true, false])('handles a zero-total period without inventing labels on zero bars (current empty: %s)', currentEmpty => {
  let chart
  const card = createRankingCard({ chartFactory: (_canvas, config) => {
    chart = { ...config, destroy() {} }
    return chart
  } })
  card.mount(document.createElement('div'), {
    productLines: [{ value: 'a', label: 'A' }], modes: [{ value: 'x', label: 'X' }],
    rowsFor: () => [{ name: 'Alice', count: currentEmpty ? 0 : 5, previousCount: currentEmpty ? 5 : 0 }],
  })
  const labels = chart.options.plugins.datalabels.labels
  expect(labels.percentage.formatter(0, { datasetIndex: currentEmpty ? 0 : 1 })).toBeNull()
  expect(labels.percentage.formatter(5, { datasetIndex: currentEmpty ? 1 : 0 })).toBe('100%（5/5）')
  expect(labels.value.formatter(currentEmpty ? 0 : 5, { datasetIndex: 0, dataIndex: 0 })).toBe(currentEmpty ? '↓ 5' : '↑ 5')
  expect(chart.options.plugins.tooltip.callbacks.afterBody([{ dataIndex: 0 }])).not.toMatch(/NaN|Infinity|%/)
  card.destroy()
})

it('keeps short-bar labels complete to the right and places the difference after them', () => {
  let options
  const card = createRankingCard({ chartFactory: (_canvas, config) => {
    options = config.options
    return { destroy() {} }
  } })
  card.mount(document.createElement('div'), {
    productLines: [{ value: 'a', label: 'A' }], modes: [{ value: 'x', label: 'X' }],
    rowsFor: () => [{ name: 'Large', count: 70, previousCount: 0 }, { name: 'Small', count: 1, previousCount: 1 }],
  })
  const labels = options.plugins.datalabels.labels
  const context = { dataIndex: 1, datasetIndex: 0, dataset: { data: [70, 1] }, chart: {
    ctx: { save() {}, restore() {}, measureText: text => ({ width: text.length * 7 }) },
    options: { font: { size: 12, family: 'Arial' } },
    scales: { x: { getPixelForValue: value => value * 16 } },
  } }
  expect(labels.percentage.formatter(1, context)).toBe('1%（1/71）')
  expect(labels.percentage.align(context)).toBe('right')
  expect(labels.value.offset(context)).toBeGreaterThan(42 + 8)
  context.dataIndex = 0
  expect(labels.percentage.align(context)).toBe('left')
  expect(labels.value.offset(context)).toBe(4)
  card.destroy()
})

it('recolors Jira on theme changes without querying rows or resetting selection and animation', async () => {
  const root = document.createElement('div')
  document.body.append(root)
  initializeTheme()
  const Charts = { defaults: { color: '#666' }, instances: {} }
  const stopTheme = initializeChartTheme(Charts)
  const chartFactory = vi.fn((_canvas, config) => {
    const chart = { data: config.data, options: config.options,
      update: vi.fn(() => config.plugins.forEach(plugin => plugin.beforeUpdate?.(chart))),
      reset: vi.fn(), stop: vi.fn(), destroy: vi.fn(() => delete Charts.instances.current) }
    Charts.instances.current = chart
    return chart
  })
  const card = createRankingCard({ chartFactory })
  const rowsFor = vi.fn(() => [{ name: 'Alice', count: 3, previousCount: 2 }])
  root.style.setProperty('--jira-current-bar', '#9DD5FA')
  root.style.setProperty('--jira-previous-bar', '#FFD3AE')
  try {
    card.mount(root, { palette: 'jira', rowsFor,
      productLines: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }],
      modes: [{ value: 'x', label: 'X' }],
    })
    root.querySelector('[data-value="b"]').click()
    const chart = chartFactory.mock.results[0].value
    expect(chart.data.datasets.map(dataset => dataset.backgroundColor)).toEqual([['#9DD5FA'], ['#FFD3AE']])
    const previousData = chart.data.datasets.map(dataset => dataset.data)
    rowsFor.mockClear(); chart.update.mockClear()
    for (const [dark, colors] of [[true, ['#789AAF', '#B99B85']], [false, ['#9DD5FA', '#FFD3AE']]]) {
      root.style.setProperty('--jira-current-bar', colors[0])
      root.style.setProperty('--jira-previous-bar', colors[1])
      globalThis.SmartTestTheme.apply(dark ? 'dark' : 'light')
      await Promise.resolve()
      expect(chart.data.datasets.map(dataset => dataset.backgroundColor)).toEqual(colors.map(color => [color]))
      expect(chart.data.datasets.map(dataset => dataset.data)).toEqual(previousData)
      expect(root.querySelector('[data-value="b"]').getAttribute('aria-pressed')).toBe('true')
      expect(rowsFor).not.toHaveBeenCalled()
      expect(chart.reset).not.toHaveBeenCalled()
      expect(chart.update).toHaveBeenLastCalledWith('none')
    }
    card.destroy(); chart.update.mockClear()
    globalThis.SmartTestTheme.apply('dark')
    await Promise.resolve()
    expect(chart.update).not.toHaveBeenCalled()
  } finally {
    stopTheme(); card.destroy(); root.remove(); document.documentElement.classList.remove('dark-theme')
  }
})

it('applies theme palette changes to real Chart.js bar elements', async () => {
  const { Chart, registerables, BasicPlatform } = await import('chart.js')
  const { default: ChartDataLabels } = await import('chartjs-plugin-datalabels')
  Chart.register(...registerables, ChartDataLabels)
  initializeTheme()
  const stopTheme = initializeChartTheme(Chart)
  const root = document.createElement('div')
  document.body.append(root)
  const textDraws = []
  let chart
  const card = createRankingCard({ chartFactory: (_canvas, config) => {
    const canvas = { width: 800, height: 108, style: {}, getContext: () => context }
    const context = new Proxy({ canvas, measureText: text => ({ width: String(text).length * 7 }),
      fillText(text) { textDraws.push({ text, color: this.fillStyle }) },
    }, {
      get: (target, key) => key in target ? target[key] : () => {},
    })
    chart = new Chart(canvas, { ...config, platform: BasicPlatform,
      options: { ...config.options, responsive: false },
    })
    return chart
  } })
  try {
    root.style.setProperty('--text-primary', '#1E293B')
    root.style.setProperty('--jira-current-bar', '#9DD5FA')
    root.style.setProperty('--jira-previous-bar', '#FFD3AE')
    card.mount(root, { palette: 'jira', rowsFor: () => [{ name: 'Alice', count: 3, previousCount: 2 }],
      productLines: [{ value: 'a', label: 'A' }], modes: [{ value: 'x', label: 'X' }],
    })
    expect(chart.getDatasetMeta(0).data[0].options.backgroundColor).toBe('#9DD5FA')
    expect(textDraws).toContainEqual({ text: '100%（3/3）', color: '#1E293B' })
    const bar = chart.getDatasetMeta(0).data[0]
    const geometry = { x: bar.x, y: bar.y, width: bar.width, height: bar.height }
    root.style.setProperty('--jira-current-bar', '#789AAF')
    root.style.setProperty('--jira-previous-bar', '#B99B85')
    root.style.setProperty('--text-primary', '#F5F5F5')
    textDraws.length = 0
    globalThis.SmartTestTheme.apply('dark')
    await Promise.resolve()
    expect(chart.getDatasetMeta(0).data[0].options.backgroundColor).toBe('#789AAF')
    expect(chart.getDatasetMeta(1).data[0].options.backgroundColor).toBe('#B99B85')
    expect(textDraws).toContainEqual({ text: '100%（3/3）', color: '#F5F5F5' })
    expect(chart.getDatasetMeta(0).data[0]).toBe(bar)
    expect({ x: bar.x, y: bar.y, width: bar.width, height: bar.height }).toEqual(geometry)
  } finally {
    stopTheme(); card.destroy(); root.remove(); document.documentElement.classList.remove('dark-theme')
  }
})

it('keeps Top3 medals aligned with real chart rows and clears them for summaries and empty data', async () => {
  const { Chart, registerables, BasicPlatform } = await import('chart.js')
  Chart.register(...registerables)
  const root = document.createElement('div')
  let chart
  const card = createRankingCard({ chartFactory: (_canvas, config) => {
    const canvas = { width: 800, height: 200, style: {}, getContext: () => context }
    const context = new Proxy({ canvas, measureText: text => ({ width: String(text).length * 7 }) }, {
      get: (target, key) => key in target ? target[key] : () => {},
    })
    chart = new Chart(canvas, { ...config, platform: BasicPlatform,
      options: { ...config.options, responsive: false },
      plugins: [...(config.plugins ?? []), { id: 'skip-rasterization', beforeDraw: () => false }],
    })
    return chart
  } })
  let rows = ['A', 'B', 'C', 'D'].map((name, index) => ({ name, count: 4 - index }))
  const config = { rowsFor: () => rows }
  try {
    card.mount(root, config)
    const medals = [...root.querySelectorAll('[data-ranking-medal]')]
    expect(medals.map(medal => medal.textContent)).toEqual(['1', '2', '3'])
    expect(medals.map(medal => medal.getAttribute('aria-label'))).toEqual(['Rank 1: A', 'Rank 2: B', 'Rank 3: C'])
    medals.forEach((medal, index) => expect(parseFloat(medal.style.top)).toBe(chart.scales.y.getPixelForValue(index)))
    chart.resize(800, 260)
    medals.forEach((medal, index) => expect(parseFloat(medal.style.top)).toBe(chart.scales.y.getPixelForValue(index)))
    card.update(config)
    expect(root.querySelector('[data-ranking-medal]')).toBe(medals[0])
    rows = [{ name: 'B', count: 9 }]
    card.update(config)
    expect(root.querySelectorAll('[data-ranking-medal]')).toHaveLength(1)
    expect(root.querySelector('[data-ranking-medal]').getAttribute('aria-label')).toBe('Rank 1: B')
    card.update({ ...config, summaryRowsFor: () => [{ name: 'Total', count: 9, valueLabel: '9' }] })
    expect(root.querySelectorAll('[data-ranking-medal]')).toHaveLength(0)
    rows = []; card.update(config)
    expect(root.querySelectorAll('[data-ranking-medal]')).toHaveLength(0)
  } finally { card.destroy() }
  expect(root.childElementCount).toBe(0)
})

it('keeps product controls themed while data regions remain plain across selection changes', () => {
  const firstRoot = document.createElement('div'), secondRoot = document.createElement('div')
  const first = createRankingCard(), second = createRankingCard()
  const rowsFor = vi.fn(() => [])
  const products = [
    { value: 'DOPL', label: 'China Operator', productLine: 'China Operator' },
    { value: 'OOPL', label: 'Global Operator & STB', productLine: 'Global Operator & STB' },
  ]
  const config = { productLines: products, rowsFor }
  try {
    first.mount(firstRoot, config); second.mount(secondRoot, config)
    const buttons = [...firstRoot.querySelectorAll('[data-product-line-segments] button')]
    const content = firstRoot.querySelector('.workload-chart-scroll')
    expect(buttons.map(button => button.dataset.productLine)).toEqual(['China Operator', 'Global Operator & STB'])
    expect(buttons.map(button => button.textContent)).toEqual(['China Operator', 'Global Operator & STB'])
    expect(content.hasAttribute('data-product-line')).toBe(false)
    expect(content.hasAttribute('data-product-surface')).toBe(false)
    buttons[1].click()
    expect(rowsFor).toHaveBeenLastCalledWith('OOPL', '')
    expect(content.hasAttribute('data-product-line')).toBe(false)
    expect(secondRoot.querySelector('.workload-chart-scroll').hasAttribute('data-product-line')).toBe(false)
    first.update({ ...config, productLines: [products[0]] })
    expect(content.hasAttribute('data-product-line')).toBe(false)
    first.update({ ...config, productLines: [{ value: 'DOPL', label: 'China Operator' }] })
    expect(content.hasAttribute('data-product-line')).toBe(false)
    expect(firstRoot.querySelector('[data-product-line-segments] button').hasAttribute('data-product-line')).toBe(false)
    first.update({ ...config, productLines: [] })
    expect(content.hasAttribute('data-product-surface')).toBe(false)
  } finally { first.destroy(); second.destroy() }
})
