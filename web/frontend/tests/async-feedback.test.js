// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { createAsyncFeedback, refreshFeedback } from '../src/async-feedback.js'

describe('AsyncFeedback', () => {
  beforeEach(() => { document.body.innerHTML = '<div id="feedback"></div><button id="cancel">Cancel</button>' })

  it('owns idle, indeterminate, determinate, and terminal ARIA states', () => {
    const feedback = createAsyncFeedback({
      root: document.querySelector('#feedback'), cancelButton: document.querySelector('#cancel')
    })
    expect(document.querySelector('#feedback').hidden).toBe(true)

    feedback.update({ state: 'running', stage: 'fetching_issues', processed: 0, total: 0 })
    let bar = document.querySelector('[role="progressbar"]')
    expect(bar.getAttribute('aria-valuenow')).toBeNull()
    expect(bar.dataset.indeterminate).toBe('true')
    expect(document.querySelector('#feedback').textContent).toContain('Fetching issues')

    feedback.update({ state: 'running', stage: 'rule_auditing', processed: 2, total: 5 })
    bar = document.querySelector('[role="progressbar"]')
    expect(bar.getAttribute('aria-valuenow')).toBe('2')
    expect(bar.getAttribute('aria-valuemax')).toBe('5')
    expect(document.querySelector('#feedback').textContent).toContain('2/5')
    expect(document.querySelector('#feedback').textContent).toContain('Rule auditing')

    for (const state of ['success', 'failed', 'cancelled']) {
      feedback.update({ state, message: state })
      expect(document.querySelector('#feedback').dataset.state).toBe(state)
      expect(document.querySelector('#cancel').hidden).toBe(true)
    }
    feedback.update({ state: 'idle' })
    expect(document.querySelector('#feedback').hidden).toBe(true)
  })

  it('delegates Cancel exactly once while running', async () => {
    const onCancel = vi.fn().mockResolvedValue()
    const feedback = createAsyncFeedback({ root: document.querySelector('#feedback'),
      cancelButton: document.querySelector('#cancel'), onCancel })
    feedback.update({ state: 'running' })
    document.querySelector('#cancel').click()
    document.querySelector('#cancel').click()
    await Promise.resolve()
    expect(onCancel).toHaveBeenCalledOnce()
  })
})


it('reports refresh failure separately from server-authorized data availability', () => {
  expect(refreshFeedback({ state: 'failed', dataState: 'ready' }).message).toContain('Showing saved data')
  expect(refreshFeedback({ state: 'failed', dataState: 'none' }).message).not.toContain('Showing saved data')
  expect(refreshFeedback({ state: 'failed' }).message).not.toContain('Showing saved data')
  expect(refreshFeedback({ state: 'cancelled', dataState: 'ready' }).state).toBe('cancelled')
})
