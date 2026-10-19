// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

function mountTheme() {
  const style = document.createElement('style')
  style.textContent = readFileSync(resolve(import.meta.dirname, '../src/smarttest-theme.css'), 'utf8')
  document.head.append(style)
  return style.sheet
}

it('uses the document root as the conditional page-scroll owner', () => {
  const style = document.createElement('style')
  style.textContent = readFileSync(resolve(import.meta.dirname, '../src/smarttest-theme.css'), 'utf8')
  document.head.append(style)

  expect(getComputedStyle(document.documentElement).overflowY).toBe('auto')
})

it('keeps hidden stage groups visually collapsed', () => {
  const style = document.createElement('style')
  style.textContent = readFileSync(resolve(import.meta.dirname, '../src/smarttest-theme.css'), 'utf8')
  document.head.append(style)
  const stageGroups = document.createElement('div')
  stageGroups.className = 'stage-groups'
  stageGroups.hidden = true
  document.body.append(stageGroups)

  expect(getComputedStyle(stageGroups).display).toBe('none')
})

it('makes product line names larger than Current Stage headings', () => {
  const style = document.createElement('style')
  style.textContent = readFileSync(resolve(import.meta.dirname, '../src/smarttest-theme.css'), 'utf8')
  document.head.append(style)
  const productLine = document.createElement('strong')
  productLine.className = 'kanban-title'
  const stageSummary = document.createElement('summary')
  const stageHeading = document.createElement('strong')
  stageSummary.className = 'stage-summary'
  stageSummary.append(stageHeading)
  document.body.append(productLine, stageSummary)

  expect(getComputedStyle(productLine).fontSize).toBe('1rem')
  expect(getComputedStyle(stageHeading).fontSize).toBe('0.8125rem')
})

it('colors stages independently from project statuses', () => {
  const style = document.createElement('style')
  style.textContent = readFileSync(resolve(import.meta.dirname, '../src/smarttest-theme.css'), 'utf8')
  document.head.append(style)
  const distribution = document.createElement('div')
  distribution.className = 'label-distribution'
  for (const slot of ['1 EVALUATION', '2 IN DEVELOPMENT', '1 EVALUATION', '3 SYSTEM UPGRADE',
    '4 MP MAINTENANCE', '5 MP CLOSE', '6 POC CLOSE', '7 PENDING', '8 CANCEL KICKOFF', '9 CANCEL CLOSE']) {
    const label = document.createElement('span')
    label.className = 'semantic-stage-probe'
    label.dataset.projectStage = slot
    label.textContent = slot
    distribution.append(label)
  }
  document.body.append(distribution)

  const colors = [...distribution.children].map(label => {
    const computed = getComputedStyle(label)
    const theme = getComputedStyle(document.documentElement)
    const resolveColor = value => theme.getPropertyValue(value.slice(4, -1)).trim()
    expect(computed.borderLeftWidth).toBe('3px')
    expect(computed.borderLeftStyle).toBe('solid')
    expect(resolveColor(computed.borderLeftColor)).toBe(resolveColor(computed.color))
    return `${resolveColor(computed.color)}|${resolveColor(computed.background)}`
  })
  expect(colors[2]).toBe(colors[0])
  expect(new Set(colors.filter((_, index) => index !== 2))).toHaveLength(8)
  expect(colors).toEqual([
    '#2457A7|#F0F5FF', '#237A52|#EFFAF4', '#2457A7|#F0F5FF',
    '#A85C00|#FFF5E8', '#806600|#FFFBE8', '#0052CC|#EDF3FF',
    '#00875A|#EDF8F3', '#A54832|#FFF2EF', '#42526E|#F1F3F6', '#42526E|#F1F3F6',
  ])
})

it('colors project statuses by their semantic meaning', () => {
  const style = document.createElement('style')
  style.textContent = readFileSync(resolve(import.meta.dirname, '../src/smarttest-theme.css'), 'utf8')
  document.head.append(style)
  const colors = ['block', 'warning', 'normal'].map(tone => {
    const label = document.createElement('span')
    label.className = 'distribution-label'
    label.dataset.projectStatus = tone.toUpperCase()
    document.body.append(label)
    const computed = getComputedStyle(label)
    const theme = getComputedStyle(document.documentElement)
    const resolveColor = value => theme.getPropertyValue(value.slice(4, -1)).trim()
    return `${resolveColor(computed.color)}|${resolveColor(computed.background)}`
  })

  expect(new Set(colors)).toHaveLength(3)
  expect(colors).toEqual(['#FFFFFF|#BF2600', '#172B4D|#FF991F', '#FFFFFF|#00875A'])
})

it('preserves GridStack content insets instead of forcing widget content to item height', () => {
  const style = document.createElement('style')
  style.textContent = readFileSync(resolve(import.meta.dirname, '../src/smarttest-theme.css'), 'utf8')
  document.head.append(style)
  const workspace = document.createElement('div')
  workspace.className = 'dashboard-workspace'
  workspace.innerHTML = '<div class="grid-stack"><div class="grid-stack-item"><div class="grid-stack-item-content dashboard-widget-card"></div></div></div>'
  document.body.append(workspace)

  expect(getComputedStyle(workspace.querySelector('.dashboard-widget-card')).height).not.toBe('100%')
})

it('keeps the standalone shared widget shell free of an extra card padding layer', () => {
  const style = document.createElement('style')
  style.textContent = readFileSync(resolve(import.meta.dirname, '../src/smarttest-theme.css'), 'utf8')
  document.head.append(style)
  const card = document.createElement('section')
  card.className = 'card page-ranking-widget'
  document.body.append(card)

  expect(getComputedStyle(card).paddingTop).toBe('0px')
  expect(getComputedStyle(card).paddingBottom).toBe('0px')
})

it('does not impose a dashboard-only height or clipping mechanism on shared widget content', () => {
  const style = document.createElement('style')
  style.textContent = readFileSync(resolve(import.meta.dirname, '../src/smarttest-theme.css'), 'utf8')
  document.head.append(style)
  const body = document.createElement('div')
  body.className = 'dashboard-widget-body'
  body.innerHTML = '<div class="segmented-ranking"><div class="workload-chart-scroll"></div></div>'
  document.body.append(body)

  expect(getComputedStyle(body).display).toBe('block')
  expect(getComputedStyle(body).height).not.toBe('calc(100% - 46px)')
  expect(getComputedStyle(body).overflow).not.toBe('hidden')
})

it('lays out shared card toolbar groups on opposite sides of one row', () => {
  const style = document.createElement('style')
  style.textContent = readFileSync(resolve(import.meta.dirname, '../src/smarttest-theme.css'), 'utf8')
  document.head.append(style)
  const toolbar = document.createElement('header')
  toolbar.className = 'report-preview-toolbar'
  toolbar.innerHTML = '<div>Product lines</div><div>Statuses</div>'
  document.body.append(toolbar)

  const computed = getComputedStyle(toolbar)
  expect(computed.display).toBe('flex')
  expect(computed.alignItems).toBe('center')
  expect(computed.justifyContent).toBe('space-between')
  expect(computed.flexDirection).toBe('row')
})

it('lets an open shared selector escape its card without hover stacking damage', () => {
  mountTheme()
  const card = document.createElement('section')
  card.className = 'card'
  card.innerHTML = '<div class="multi-select is-open"><div class="multi-select__dropdown card"></div></div>'
  document.body.append(card)

  const computed = getComputedStyle(card)
  expect(computed.overflow).toBe('visible')
  expect(computed.zIndex).toBe('20')
  expect(computed.transform).toBe('none')
})

it('renders project status summary text without applying the saturated status background', () => {
  mountTheme()
  const block = document.createElement('div')
  block.dataset.projectStatusText = 'BLOCK'
  const warning = document.createElement('div')
  warning.dataset.projectStatusText = 'WARNING'
  document.body.append(block, warning)

  const theme = getComputedStyle(document.documentElement)
  const resolveColor = value => value.startsWith('var(') ? theme.getPropertyValue(value.slice(4, -1)).trim() : value
  expect(resolveColor(getComputedStyle(block).color)).toBe('#BF2600')
  expect(resolveColor(getComputedStyle(warning).color)).toBe('#FF991F')
  expect(getComputedStyle(block).backgroundColor).toBe('rgba(0, 0, 0, 0)')
  expect(getComputedStyle(warning).backgroundColor).toBe('rgba(0, 0, 0, 0)')
})

it('keeps each project status metric label and value on one row', () => {
  mountTheme()
  const row = document.createElement('div')
  row.className = 'summary-metric-row'
  row.innerHTML = '<span>Block Projects</span><strong>5</strong>'
  document.body.append(row)

  const computed = getComputedStyle(row)
  expect(computed.display).toBe('flex')
  expect(computed.alignItems).toBe('center')
  expect(computed.justifyContent).toBe('space-between')
})

it('does not divide adjacent project status metric rows', () => {
  mountTheme()
  const card = document.createElement('div')
  card.innerHTML = '<div class="summary-metric-row"></div><div class="summary-metric-row"></div>'
  document.body.append(card)

  const secondRow = getComputedStyle(card.lastElementChild)
  expect(secondRow.borderTopWidth).not.toBe('1px')
  expect(secondRow.paddingTop).not.toBe('.75rem')
})

it('enlarges summary descriptions and reduces summary numbers', () => {
  mountTheme()
  const row = document.createElement('div')
  row.className = 'summary-metric summary-metric-row'
  row.innerHTML = '<span>Support Projects</span><strong>12</strong>'
  document.body.append(row)

  expect(getComputedStyle(row.querySelector('span')).fontSize).toBe('0.875rem')
  expect(getComputedStyle(row.querySelector('strong')).fontSize).toBe('1.25rem')
})

it('left-aligns every project summary card', () => {
  mountTheme()
  const card = document.createElement('article')
  card.className = 'summary-metric'
  document.body.append(card)

  const computed = getComputedStyle(card)
  expect(computed.textAlign).toBe('left')
  expect(computed.justifyItems).toBe('stretch')
})

it('keeps project preview facts on one horizontal summary row', () => {
  mountTheme()
  const summary = document.createElement('div')
  summary.className = 'project-card-summary'
  summary.innerHTML = `
    <div class="project-list-primary"></div>
    <div class="project-card-fact project-detail-item"></div>
    <div class="project-card-summary-facts"><div class="project-card-fact"></div></div>
    <div class="project-card-badges"></div>`
  document.body.append(summary)

  const facts = summary.querySelector('.project-card-summary-facts')
  const fact = facts.querySelector('.project-card-fact')
  expect(getComputedStyle(facts).flexWrap).toBe('nowrap')
  expect(getComputedStyle(fact).minWidth).toBe('0')
  expect(getComputedStyle(fact).whiteSpace).toBe('nowrap')
})

it('sizes project summary cards according to content density', () => {
  mountTheme()
  const sizes = className => {
    const card = document.createElement('article')
    card.className = `summary-metric ${className}`
    card.innerHTML = '<div class="summary-metric-row"><span>Metric</span><strong>12</strong></div>'
    document.body.append(card)
    return [getComputedStyle(card.querySelector('span')).fontSize, getComputedStyle(card.querySelector('strong')).fontSize]
  }

  expect(sizes('summary-metric-single')).toEqual(['1rem', '1.5rem'])
  expect(sizes('summary-metric-paired')).toEqual(['0.875rem', '1.125rem'])
  expect(sizes('summary-metric-resources')).toEqual(['0.8125rem', '1.125rem'])
  expect(sizes('summary-metric-product-modes')).toEqual(['0.8125rem', '1rem'])
  expect(sizes('summary-metric-average')).toEqual(['0.875rem', '1.25rem'])
})
