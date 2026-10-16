import { createRankingCard } from './ranking-card.js'

function readableName(person) {
  const name = String(person?.name ?? '').trim()
  const identity = String(person?.identity ?? '').trim()
  return name || identity || 'Unknown'
}

export const ROLE_WORKLOAD_LAYOUT = Object.freeze({ defaultW: 24 })

export function createRoleWorkloadWidget({ chartFactory } = {}) {
  const ranking = createRankingCard({ chartFactory })
  let data = { ownerHierarchy: [], productSpaces: [] }

  function presentation(config) {
    const roles = (config.ownerHierarchy ?? []).filter(role => role.people?.length)
    return {
      headingHtml: config.shellTitle ? '' : '<div><strong>Project Resource Statistics</strong><div class="report-preview-meta">Project assignments per QA member</div></div>',
      productLines: (config.productSpaces ?? []).map(item => ({ value: item.value, label: item.label, productLine: item.label })),
      modes: roles.map(role => ({ value: role.role, label: role.role })),
      error: config.error,
      emptyText: 'No assignments in this product line.',
      datasetLabel: 'Projects',
      rowsFor: (productLine, role) => (roles.find(item => item.role === role)?.people ?? []).map(person => ({
        name: readableName(person),
        count: (person.projects ?? []).filter(project => project.space_key === productLine).length,
      })),
    }
  }

  return {
    mount(target, config = {}) { data = config; ranking.mount(target, presentation(data)) },
    update(config = {}) { data = config; ranking.update(presentation(data)) },
    destroy() { ranking.destroy() },
  }
}
