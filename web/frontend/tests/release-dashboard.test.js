// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from 'vitest'

import { createReleaseDashboard } from '../src/release-dashboard.js'


const payload = {
  state: 'ready',
  facets: [
    { key: 'productLine', label: 'Product Line', options: [{ value: 'DOPL', label: 'Digital Operator' }] },
    { key: 'stage', label: 'Current Stage', options: [{ value: 'EVT', label: 'EVT' }] },
  ],
  summary: { currentReleases: 1, block: 1, warning: 0, openP0P1: 2, dataIncomplete: 0 },
  releases: [{
    projectId: 'P100', projectName: 'Orion', releaseName: 'Android 16', currentStage: 'EVT',
    launchTime: '2026-09-30', daysToLaunch: 26, nextTarget: 'DVT exit', nextTargetDate: '2026-09-20',
    projectOwners: 'Alice', majorFaeQa: 'Bob', issueCounts: { open: 4, p0: 1, p1: 1, versionPending: 2 },
    health: { state: 'BLOCK', reasons: ['1 个未解决 P0'] }, confluenceUrl: 'https://wiki/P100', cachedAt: '2026-09-04',
  }],
  sourceFreshness: { confluence: '2026-09-04', jira: '2026-09-04' }, syncState: 'idle',
}

beforeEach(() => { document.body.innerHTML = '<main></main>' })

it('replays the SQLite snapshot on entry and renders explainable release health', async () => {
  const api = { getDashboardReleases: vi.fn().mockResolvedValue(payload), syncDashboardReleases: vi.fn() }
  const page = createReleaseDashboard({ root: document.querySelector('main'), api })

  await page.start()

  expect(api.getDashboardReleases).toHaveBeenCalledWith()
  expect(document.querySelector('[data-summary="currentReleases"]').textContent).toBe('1')
  expect(document.querySelector('[data-release-row]').textContent).toContain('Android 16')
  document.querySelector('[data-release-row]').click()
  expect(document.querySelector('[data-release-detail]').textContent).toContain('1 个未解决 P0')
  expect(document.querySelector('[data-jira-drilldown]').href).toContain('snapshot=dashboard&projectId=P100')
})

it('does not own a second filter and uses explicit sync only from the Sync button', async () => {
  const api = {
    getDashboardReleases: vi.fn().mockResolvedValue(payload),
    syncDashboardReleases: vi.fn().mockResolvedValue(payload),
  }
  const page = createReleaseDashboard({ root: document.querySelector('main'), api })
  await page.start()
  expect(document.querySelector('[data-release-filters]')).toBeNull()
  expect(api.syncDashboardReleases).not.toHaveBeenCalled()
  document.querySelector('[data-sync]').click()
  await vi.waitFor(() => expect(api.syncDashboardReleases).toHaveBeenCalledOnce())
})

it('keeps cached releases visible and reports a downstream sync failure', async () => {
  const api = {
    getDashboardReleases: vi.fn().mockResolvedValue(payload),
    syncDashboardReleases: vi.fn().mockResolvedValue({ ...payload, dataState: 'ready', syncState: 'loading', taskId: 'task-1' }),
    getDashboardReleaseSync: vi.fn().mockResolvedValue({ state: 'completed', syncState: 'failed' }),
  }
  const page = createReleaseDashboard({ root: document.querySelector('main'), api })
  await page.start()

  document.querySelector('[data-sync]').click()

  await vi.waitFor(() => expect(document.querySelector('[data-release-feedback]').textContent)
    .toContain('Showing saved data'))
  expect(document.querySelector('[data-release-row]').textContent).toContain('Android 16')
  expect(api.getDashboardReleaseSync).toHaveBeenCalledWith('task-1')
})
