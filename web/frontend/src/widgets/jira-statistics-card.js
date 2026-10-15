import { createRankingCard } from './ranking-card.js'
import { createDisposableDisplayCache } from '../disposable-display.js'

export const JIRA_STATISTICS_CARD_LAYOUT = Object.freeze({ defaultW: 24 })
export const JIRA_STATISTICS_CARDS = Object.freeze({
  'self-test': Object.freeze({ title: 'Self-Test Jira Statistics by Product Line', firstMetricLabel: 'Bugs' }),
  task: Object.freeze({ title: 'Task Jira Statistics by Product Line', firstMetricLabel: 'Tasks' }),
  'customer-feedback': Object.freeze({ title: 'Customer Feedback Jira Statistics by Product Line', firstMetricLabel: 'Component/s' }),
})

const PERIODS = [{ value: 'week', label: 'Weekly' }, { value: 'month', label: 'Monthly' },
  { value: 'quarter', label: 'Quarterly' }, { value: 'year', label: 'Yearly' }]
let periodInstance = 0
function periodMarkup() {
  const name = `jira-period-${++periodInstance}`
  return `<fieldset class="jira-period-fieldset" data-jira-periods aria-label="Statistics period">${PERIODS.map(item => `<div class="jira-period-button-group"><input type="radio" name="${name}" id="${name}-${item.value}" value="${item.value}" data-jira-period="${item.value}" ${item.value === 'month' ? 'checked' : ''}><label for="${name}-${item.value}">${item.label}</label></div>`).join('')}</fieldset>`
}

function metrics(firstMetricLabel) { return Object.freeze([
  Object.freeze({ value: 'bugCount', label: firstMetricLabel }),
  Object.freeze({ value: 'commentCount', label: 'Comments' }),
  Object.freeze({ value: 'verifyCount', label: 'Verify' }),
  Object.freeze({ value: 'invalidCount', label: 'Invalid' }),
]) }

export function createJiraStatisticsCard({ pollDelay = 800, chartFactory } = {}) {
  let root = null
  let stopped = false
  let timer = null
  let displayed = false
  let displaySignature = ''
  let displayCache
  let api
  let generation = 0
  let rankingHost, periods
  let period = 'month'
  let cardKey = 'self-test'
  let cardMetrics = metrics('Bugs')
  const ranking = createRankingCard({ chartFactory })

  function message(text, kind = '') {
    let status = root.querySelector('[data-jira-statistics-state]')
    if (!status) {
      status = document.createElement('div')
      status.dataset.jiraStatisticsState = ''
      ;(rankingHost.querySelector('.workload-chart-scroll') ?? root).append(status)
    }
    const empty = rankingHost.querySelector('[data-ranked-empty]')
    if (empty) empty.hidden = true
    status.className = `jira-statistics-state${kind ? ` ${kind}` : ''}`
    status.textContent = text
  }

  function render(payload) {
    if (payload.period) period = payload.period
    selectPeriod()
    if (payload.state === 'no_snapshot') {
      root.prepend(periods)
      ranking.destroy(); displayed = false; displaySignature = ''
      displayCache.write(null)
      message('Apply a Jira query to display statistics.')
      return false
    }
    if (payload.productLines || payload.current?.productLines) draw(payload)
    if (['failed', 'cancelled'].includes(payload.state)) { message(payload.error || 'Jira query did not complete.', 'inline-status-error'); return false }
    if (payload.state !== 'ready') {
      const progress = payload.task?.progress
      message(progress?.total ? `Loading Jira issues… ${progress.processed}/${progress.total}` : 'Loading Jira issues…')
      return true
    }
    root.querySelector('[data-jira-statistics-state]')?.remove()
    return false
  }

  function draw(payload) {
    if (payload.period) period = payload.period
    const current = payload.current ?? payload
    const availability = payload.availability
    const metricReady = (period, metric) => {
      const layer = metric === 'verifyCount' ? 'verify' : metric === 'commentCount' ? 'comment' : 'basic'
      return !availability || Boolean(availability[period]?.[layer] ?? (layer !== 'basic' && availability[period]?.activity))
    }
    const previous = payload.previous
    const comparison = Boolean(previous || payload.comparison)
    const previousByLine = new Map((previous?.productLines ?? []).map(line => [line.id, line]))
    const combinedLines = (current.productLines ?? []).map(line => {
      const priorComponents = previousByLine.get(line.id)?.components ?? []
      const componentsByName = new Map((line.components ?? []).map(component => [component.name, { ...component }]))
      for (const component of priorComponents) {
        const current = componentsByName.get(component.name) ?? { name: component.name, count: 0 }
        componentsByName.set(component.name, { ...current, previousCount: component.count })
      }
      const priorPeople = previousByLine.get(line.id)?.people ?? []
      const priorByIdentity = new Map(priorPeople.map(person => [person.identity || person.displayName, person]))
      const currentByIdentity = new Map((line.people ?? []).map(person => [person.identity || person.displayName, person]))
      const currentKeys = new Set((line.people ?? []).map(person => person.identity || person.displayName))
      const people = [...(line.people ?? []), ...priorPeople.filter(person => !currentKeys.has(person.identity || person.displayName))]
      return { id: line.id, label: line.label, components: [...componentsByName.values()],
        issueCount: line.issueCount, invalidCount: line.invalidCount, people: people.map(person => {
        const key = person.identity || person.displayName
        const currentPerson = currentByIdentity.get(key) ?? {}
        const prior = priorByIdentity.get(key) ?? {}
        return { displayName: person.displayName,
          ...Object.fromEntries(cardMetrics.flatMap(metric => comparison ? [
            [metric.value, Number(currentPerson[metric.value] || 0)],
            [`previous_${metric.value}`, Number(prior[metric.value] ?? person[`previous_${metric.value}`] ?? 0)],
          ] : [[metric.value, Number(currentPerson[metric.value] || 0)]])),
        }
      }) }
    })
    const display = { state: payload.state, availability, period, ranges: payload.ranges, comparison, teamTotal: current.teamTotal,
      previousTeamTotal: previous?.teamTotal ?? payload.previousTeamTotal,
      unmappedCount: Number(current.unmappedCount || 0), unassignedCount: Number(current.unassignedCount || 0),
      productLines: combinedLines,
    }
    const signature = JSON.stringify(display)
    if (displayed && signature === displaySignature) return
    const byLine = new Map(combinedLines.map(line => [line.id, line]))
    const pendingLabel = ({ failed: 'Failed', cancelled: 'Cancelled' })[payload.state] ?? (payload.state === 'ready' ? 'Waiting for scheduled refresh' : 'Loading…')
    const presentation = {
      palette: 'jira',
      productLines: combinedLines.map(line => ({ value: line.id, label: line.label, productLine: line.label })),
      modes: cardMetrics.map(metric => ({ ...metric,
        disabled: !metricReady('current', metric.value),
        label: !metricReady('current', metric.value) ? `${metric.label} (${pendingLabel})`
          : comparison && !(cardKey === 'customer-feedback' && metric.value === 'invalidCount')
            && !metricReady('previous', metric.value) ? `${metric.label} (Previous: ${pendingLabel})` : metric.label,
      })),
      emptyTextFor: metric => metricReady('current', metric) ? '' : 'Statistics for this metric are not available yet.',
      emptyText: 'No issues in this product line.',
      summaryRowsFor: (productLine, metric) => {
        if (cardKey !== 'customer-feedback' || metric !== 'invalidCount' || !metricReady('current', metric)) return null
        const line = byLine.get(productLine)
        const total = line?.issueCount ?? 0
        const invalid = line?.invalidCount ?? 0
        return [{ name: 'Total', count: total, valueLabel: String(total) },
          { name: 'Invalid', count: invalid, valueLabel: `${invalid} · ${total ? Number((invalid / total * 100).toFixed(2)) : 0}%` }]
      },
      rowsFor: (productLine, metric) => {
        if (!metricReady('current', metric)) return []
        if (cardKey === 'customer-feedback' && metric === 'bugCount') {
          return (byLine.get(productLine)?.components ?? []).map(component => ({ name: component.name, count: component.count,
            ...(comparison && metricReady('previous', metric) ? { previousCount: component.previousCount ?? 0 } : {}),
          }))
        }
        return (byLine.get(productLine)?.people ?? []).map(person => ({
          name: person.displayName, count: Number(person[metric] || 0),
          ...(comparison && metricReady('previous', metric) ? { previousCount: Number(person[`previous_${metric}`] || 0) } : {}),
        }))
      },
      datasetLabel: 'Current period',
      comparisonLabel: 'Previous period',
      ranges: payload.ranges,
    }
    if (displayed) ranking.update(presentation)
    else ranking.mount(rankingHost, presentation)
    root.querySelector('.report-preview-toolbar').after(periods)
    selectPeriod()
    displayed = true
    displaySignature = signature
    if (payload.state === 'ready') displayCache.write(display)
  }

  function selectPeriod() {
    for (const input of periods.querySelectorAll('input')) input.checked = input.value === period
  }

  async function load(request = ++generation, query = false, selectedPeriod) {
    clearTimeout(timer)
    try {
      const payload = await (query ? api.queryStatistics(selectedPeriod, selectedPeriod ? 'reuse' : 'refresh') : api.getStatistics())
      if (stopped || request !== generation) return
      const again = render(payload)
      if (again && !stopped) timer = setTimeout(() => { void load() }, pollDelay)
    } catch (error) {
      if (!stopped && request === generation) message(error.message || 'Jira statistics card could not be loaded.', 'inline-status-error')
    }
  }

  return {
    mount(target, config = {}) {
      root = target; stopped = false
      cardKey = config.cardKey ?? 'self-test'
      const metadata = JIRA_STATISTICS_CARDS[cardKey]
      if (!metadata) throw new Error(`Unknown Jira statistics card: ${cardKey}`)
      cardMetrics = metrics(metadata.firstMetricLabel)
      period = 'month'
      root.innerHTML = `${periodMarkup()}<div data-jira-ranking></div>`
      rankingHost = root.querySelector('[data-jira-ranking]')
      periods = root.querySelector('[data-jira-periods]')
      periods.addEventListener('change', event => {
        const selected = event.target.closest('[data-jira-period]')?.dataset.jiraPeriod
        if (selected && selected !== period && api) {
          period = selected; selectPeriod(); void load(++generation, true, period)
        }
      })
      displayed = false
      displayCache = createDisposableDisplayCache('jiraStatistics', config.account, cardKey)
      const cached = displayCache.read()
      if (cached?.productLines && (cardKey !== 'customer-feedback'
        || cached.productLines.every(line => Array.isArray(line.components) && Number.isFinite(line.issueCount) && Number.isFinite(line.invalidCount)))) draw(cached)
      else {
        draw({ state: 'loading', productLines: [],
          availability: { current: { basic: false, activity: false }, previous: { basic: false, activity: false } } })
        message('Loading Jira issues…')
      }
      if (!config.api) { message('Jira statistics card API is unavailable.', 'inline-status-error'); return }
      api = config.api
      void load()
    },
    update({ query = false } = {}) { if (!stopped && api) void load(++generation, query) },
    destroy() {
      stopped = true; clearTimeout(timer); ranking.destroy()
      root = null; displayed = false
    },
  }
}
