import './ranking-card.css'
import { element } from '../dom.js'
import { bindProductLine } from '../product-line-semantics.js'
import { toFont } from 'chart.js/helpers'

export function createRankingCard({ chartFactory } = {}) {
  let root, chart, config
  let activeProductLine = ''
  let activeMode = ''
  let positionSignature = ''
  const positionLayout = {
    id: 'ranking-positions',
    beforeUpdate(current) { applyPalette(current.data.datasets) },
    afterLayout(current) {
      root.querySelectorAll('[data-ranking-position]').forEach((position, index) => {
        position.style.top = `${current.scales.y.getPixelForValue(index)}px`
      })
    },
  }
  const resizeObserver = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
    if (root) positionModeBackground(root.querySelector('[data-mode-segments]'))
  }) : null

  function segments(target, options, active, setActive, className) {
    const buttons = [...target.children]
    if (buttons.length !== options.length || buttons.some((button, index) => button.dataset.value !== String(options[index].value))) {
      target.replaceChildren()
      for (const option of options) {
        const button = element('button', className, option.label)
        button.type = 'button'
        button.dataset.value = String(option.value)
        button.addEventListener('click', () => {
          setActive(option.value)
          selectSegment(target, option.value)
          renderChart()
        })
        target.append(button)
      }
    }
    ;[...target.children].forEach((button, index) => {
      button.textContent = options[index].label
      button.disabled = Boolean(options[index].disabled)
      if (target.matches('[data-product-line-segments]')) bindProductLine(button, options[index].productLine, 'button')
    })
    selectSegment(target, active)
  }

  function selectSegment(target, active) {
    for (const button of target.children) {
      const selected = button.dataset.value === String(active)
      button.classList.toggle('active', selected)
      button.setAttribute('aria-pressed', String(selected))
    }
    if (target.matches('[data-mode-segments]')) positionModeBackground(target)
  }

  function positionModeBackground(target) {
    const selected = target.querySelector('.active')
    if (!selected) return
    target.style.setProperty('--segment-left', `${selected.offsetLeft}px`)
    target.style.setProperty('--segment-width', `${selected.offsetWidth}px`)
  }

  function applyPalette(datasets) {
    if (config.palette !== 'jira') return
    const styles = getComputedStyle(root)
    // Indexable colors avoid Chart.js retaining shared bar options on update('none').
    datasets[0].backgroundColor = [styles.getPropertyValue('--jira-current-bar').trim()]
    if (datasets[1]) datasets[1].backgroundColor = [styles.getPropertyValue('--jira-previous-bar').trim()]
  }

  function render() {
    const products = config.productLines ?? []
    const modes = config.modes ?? []
    if (!products.some(item => item.value === activeProductLine)) activeProductLine = products[0]?.value ?? ''
    if (!modes.some(item => item.value === activeMode)) activeMode = modes[0]?.value ?? ''
    segments(root.querySelector('[data-product-line-segments]'), products, activeProductLine, value => { activeProductLine = value }, 'product-line-segment')
    segments(root.querySelector('[data-mode-segments]'), modes, activeMode, value => { activeMode = value }, 'role-segment')
    renderChart()
  }

  function renderChart() {
    config.onSelectionChange?.({ productLine: activeProductLine, mode: activeMode })
    const summary = config.summaryRowsFor?.(activeProductLine, activeMode)
    const rows = summary ?? (config.rowsFor?.(activeProductLine, activeMode) ?? [])
      .filter(row => row.count || row.previousCount)
      .sort((left, right) => right.count - left.count
        || Number(right.previousCount || 0) - Number(left.previousCount || 0)
        || left.name.localeCompare(right.name))
    const total = rows.reduce((sum, row) => sum + row.count, 0)
    const previousTotal = rows.reduce((sum, row) => sum + (row.previousCount || 0), 0)
    const surface = root.querySelector('.workload-chart-surface')
    const comparison = rows.some(row => Object.hasOwn(row, 'previousCount'))
    surface.style.height = `${rows.length * (comparison ? 52 : 36) + (comparison ? 40 : 36)}px`
    const empty = root.querySelector('[data-ranked-empty]')
    empty.hidden = Boolean(rows.length)
    empty.textContent = config.error || config.emptyTextFor?.(activeMode) || config.emptyText || 'No data in this product line.'
    const canvas = root.querySelector('[data-ranked-chart]')
    canvas.hidden = !rows.length
    const positionRows = summary || !chartFactory ? [] : rows.slice(0, 3)
    const signature = JSON.stringify([activeProductLine, activeMode, positionRows.map(row => row.name)])
    if (signature !== positionSignature) {
      const positions = root.querySelector('[data-ranking-positions]')
      positions.replaceChildren(...positionRows.map((row, index) => {
        const position = element('span', 'ranking-position', String(index + 1))
        position.dataset.rankingPosition = String(index + 1)
        position.setAttribute('aria-label', `Rank ${index + 1}: ${row.name}`)
        return position
      }))
      positionSignature = signature
    }
    if (!rows.length || !chartFactory) {
      chart?.destroy()
      chart = null
      return
    }
    const reducedMotion = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    const comparisonText = (current, previous) => {
      const difference = current - previous
      if (difference === 0) return 'No change'
      return `${difference > 0 ? '↑' : '↓'} ${Math.abs(difference)}`
    }
    const rangeText = period => period ? `${period.start} – ${period.end ?? 'now'}` : ''
    const quantityLabel = (value, context) => {
      if (summary) return rows[context.dataIndex].valueLabel
      if (!value) return null
      const periodTotal = context?.datasetIndex === 1 ? previousTotal : total
      return `${periodTotal ? Math.round(value / periodTotal * 100) : 0}%（${value}/${periodTotal}）`
    }
    const overflowLabelWidth = context => {
      const value = context.dataset.data[context.dataIndex]
      const text = quantityLabel(value, context)
      if (!text) return 0
      const { ctx, scales, options } = context.chart
      ctx.save()
      ctx.font = toFont(options.font).string
      const width = ctx.measureText(text).width + 8
      ctx.restore()
      const barWidth = Math.abs(scales.x.getPixelForValue(value) - scales.x.getPixelForValue(0))
      return barWidth < width + 4 ? width : 0
    }
    const datasets = [{ label: config.datasetLabel || '', data: rows.map(row => row.count),
      grouped: comparison, barThickness: comparison ? 10 : 12, borderRadius: 999, borderSkipped: false, order: 1 }]
    if (comparison) datasets.push({ label: config.comparisonLabel || 'Previous', data: rows.map(row => row.previousCount),
      grouped: true, barThickness: 10, borderRadius: 999, borderSkipped: false, order: 2 })
    applyPalette(datasets)
    const next = {
      type: 'bar', data: { labels: rows.map(row => row.name), datasets },
      plugins: [positionLayout],
      options: { indexAxis: 'y', responsive: true, maintainAspectRatio: false,
        animation: reducedMotion ? false : { duration: 500, easing: 'easeOutQuart',
          delay: context => context.type === 'data' ? Math.min(context.dataIndex * 40, 400) : 0 },
        transitions: { resize: { animation: { duration: reducedMotion ? 0 : 500 } } },
        animations: { y: { duration: 0 }, width: {
          from: context => Number.isFinite(context.element?.width) ? context.element.width : 0,
        } },
        layout: { padding: { left: positionRows.length ? 32 : 0, right: 28 } }, scales: {
          x: { beginAtZero: true, ticks: { precision: 0 }, grid: { display: false }, border: { display: false } },
          y: { ticks: { autoSkip: false }, grid: { display: false }, border: { display: false } },
        },
        plugins: { legend: { display: comparison, labels: { usePointStyle: true, boxWidth: 8, boxHeight: 8 } }, tooltip: { callbacks: {
          afterBody: items => {
            if (!comparison || !items?.length) return ''
            const row = rows[items[0].dataIndex]
            return `Current: ${rangeText(config.ranges?.current)} · ${row.count}\nPrevious: ${rangeText(config.ranges?.previous)} · ${row.previousCount}\nChange: ${comparisonText(row.count, row.previousCount)}`
          },
        } }, datalabels: {
          color: () => getComputedStyle(root).getPropertyValue('--text-primary').trim(),
          font: context => context.chart.options.font,
          labels: {
          percentage: { anchor: 'end', align: context => overflowLabelWidth(context) ? 'right' : 'left',
            offset: 4, clip: false, formatter: quantityLabel },
          value: { anchor: 'end', align: 'right', offset: context => {
            const width = overflowLabelWidth(context)
            return width ? width + 12 : 4
          }, clip: false,
            formatter: (value, context) => comparison && context.datasetIndex === 0
              ? comparisonText(value, rows[context.dataIndex].previousCount) : null },
        } } } },
    }
    if (!chart) {
      chart = chartFactory(canvas, next)
      return
    }
    const labelsChanged = chart.data.labels.length !== next.data.labels.length
      || chart.data.labels.some((label, index) => label !== next.data.labels[index])
    chart.stop()
    chart.data = next.data
    chart.options = next.options
    if (labelsChanged && !reducedMotion) {
      // Reordered rows must not morph one person's bar into another person's bar.
      chart.update('none')
      chart.reset()
    }
    if (reducedMotion) chart.update('none')
    else chart.update()
  }

  return {
    mount(target, value) {
      root = target; config = value
      positionSignature = ''
      root.innerHTML = `<div class="segmented-ranking"><header class="report-preview-toolbar"><div class="workload-heading">${config.headingHtml || ''}<div class="product-line-segments" data-product-line-segments></div></div><div class="role-segments" data-mode-segments></div></header><div class="workload-chart-scroll"><div class="workload-chart-surface"><canvas data-ranked-chart></canvas><div data-ranking-positions></div><div class="product-space-empty" data-ranked-empty data-workload-empty hidden></div></div></div></div>`
      render()
      resizeObserver?.observe(root.querySelector('[data-mode-segments]'))
    },
    update(value) { config = value; if (root) render() },
    destroy() { resizeObserver?.disconnect(); chart?.destroy(); chart = null; root?.replaceChildren(); root = null },
  }
}
