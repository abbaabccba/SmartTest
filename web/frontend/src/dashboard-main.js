import { Chart, registerables } from 'chart.js'
import { initializeChartTheme } from './theme.js'
import ChartDataLabels from 'chartjs-plugin-datalabels'
import { GridStack } from 'gridstack'
import 'gridstack/dist/gridstack.min.css'
import { createJiraStatisticsCardApi, createPreferenceApi, createProjectFactsApi } from './api.js'
import { startAuthenticatedPage } from './authenticated-page.js'
import { createDashboardGrid } from './dashboard/dashboard-grid.js'
import { createWidgetRegistry } from './dashboard/widget-registry.js'
import { createRoleWorkloadWidget, ROLE_WORKLOAD_LAYOUT } from './widgets/role-workload.js'
import { createJiraStatisticsCard, JIRA_STATISTICS_CARDS, JIRA_STATISTICS_CARD_LAYOUT } from './widgets/jira-statistics-card.js'

if (!window.location.pathname.startsWith('/wifi-database/')) {
  Chart.register(...registerables, ChartDataLabels)
  initializeChartTheme(Chart)
  const preferenceApi = createPreferenceApi()
  const projectFactsApi = createProjectFactsApi()
  startAuthenticatedPage({ mount: (root, session) => {
    const registry = createWidgetRegistry()
    registry.register({
      type: 'role-workload', title: 'Project Human Resource Statistics', ...ROLE_WORKLOAD_LAYOUT,
      create: () => createRoleWorkloadWidget({ chartFactory: (canvas, config) => new Chart(canvas, config) }),
    })
    for (const [cardKey, metadata] of Object.entries(JIRA_STATISTICS_CARDS)) {
      registry.register({
        type: `jira-statistics-${cardKey}`, title: metadata.title, ...JIRA_STATISTICS_CARD_LAYOUT, cardKey,
        create: () => createJiraStatisticsCard({ chartFactory: (canvas, config) => new Chart(canvas, config) }),
      })
    }
    return createDashboardGrid({
      root, registry, preferenceApi,
      gridFactory: (options, element) => GridStack.init(options, element),
      widgetConfig: async type => {
        const cardKey = registry.get(type)?.cardKey
        if (cardKey) {
          return { api: createJiraStatisticsCardApi({ cardKey }), account: session.username, cardKey }
        }
        if (type !== 'role-workload') return {}
        try {
          const payload = await projectFactsApi.getProjectFacts()
          return { ownerHierarchy: payload.ownerHierarchy ?? [], productSpaces: payload.productSpaces ?? [], shellTitle: true }
        } catch {
          return { error: 'Local project facts API is unavailable.', shellTitle: true }
        }
      },
    })
  } })
}
