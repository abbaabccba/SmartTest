import { Chart, registerables } from 'chart.js'
import { initializeChartTheme } from './theme.js'
import ChartDataLabels from 'chartjs-plugin-datalabels'
import { createManualAuditApi, createProjectFactsApi } from './api.js'
import { startAuthenticatedPage } from './authenticated-page.js'
import { preferencesReady } from './main.js'
import { createProjects } from './projects.js'
import { createRoleWorkloadWidget } from './widgets/role-workload.js'

const projectFactsApi = createProjectFactsApi()
const manualAuditApi = createManualAuditApi()
Chart.register(...registerables, ChartDataLabels)
initializeChartTheme(Chart)
startAuthenticatedPage({
  mount: (root, session) => {
    root.innerHTML = `<section class="card page-ranking-widget" data-page-widget="role-workload"><header class="dashboard-widget-head"><strong>Project Human Resource Statistics</strong></header><div data-widget-body></div></section><div data-page-primary></div>`
    const workload = createRoleWorkloadWidget({ chartFactory: (canvas, config) => new Chart(canvas, config) })
    workload.mount(root.querySelector('[data-widget-body]'), { shellTitle: true })
    const projects = createProjects({
      root: root.querySelector('[data-page-primary]'),
      account: session.username,
      api: { ...projectFactsApi, ...manualAuditApi },
      waitForPreferences: async () => {
        await preferencesReady
        await new Promise(resolve => setTimeout(resolve, 0))
      },
      enableReview: false,
      onSnapshot: payload => workload.update({ ownerHierarchy: payload.ownerHierarchy ?? [], productSpaces: payload.productSpaces ?? [], shellTitle: true }),
    })
    root.querySelector('form').after(root.querySelector('[data-page-widget="role-workload"]'))
    root.querySelector('[data-page-widget="role-workload"] > header').after(root.querySelector('[data-async-feedback]'))
    return { start: () => projects.start(), destroy() { workload.destroy(); projects.destroy() } }
  },
})
