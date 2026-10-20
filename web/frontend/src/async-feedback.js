const TERMINAL = new Set(['success', 'failed', 'cancelled'])

function formatStage(stage) {
  const value = String(stage ?? '').trim().replaceAll('_', ' ')
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : ''
}

// The server owns dataState; a browser payload is not evidence of valid data.
export function refreshFeedback({ state = 'idle', dataState, message = '', error = '' } = {}) {
  if (state === 'invalid_credentials' && !message && !error) message = 'Sync credentials were rejected.'
  state = ({ queued: 'running', loading: 'running', completed: 'success', ready: 'success',
    invalid_credentials: 'failed' })[state] ?? state
  if (state === 'cancelled' && error === 'query_cancelled' && dataState === 'ready') error = ''
  const text = message || error || ({ success: 'Completed', failed: 'Refresh failed.',
    cancelled: 'Refresh cancelled.' }[state] ?? '')
  return { state, message: ['failed', 'cancelled'].includes(state) && dataState === 'ready'
    ? `${text} Showing saved data.` : text }
}

export function createAsyncFeedback({ root, cancelButton, onCancel } = {}) {
  root.innerHTML = `<div class="async-feedback__head"><strong data-async-message></strong><span data-async-count></span></div>
    <div class="async-feedback__track" role="progressbar" aria-label="Task progress"><div class="async-feedback__fill"></div></div>`
  const message = root.querySelector('[data-async-message]')
  const count = root.querySelector('[data-async-count]')
  const track = root.querySelector('[role="progressbar"]')
  const fill = root.querySelector('.async-feedback__fill')
  let state = 'idle'
  let cancelling = false

  async function cancel() {
    if (state !== 'running' || cancelling || !onCancel) return
    cancelling = true
    cancelButton.disabled = true
    try { await onCancel() } finally { cancelling = false }
  }
  cancelButton?.addEventListener('click', cancel)

  function update(next = {}) {
    next = { ...next, ...refreshFeedback(next) }
    state = next.state || 'idle'
    const processed = Math.max(0, Number(next.processed) || 0)
    const total = Math.max(0, Number(next.total) || 0)
    const running = state === 'running'
    const determinate = running && total > 0
    root.hidden = state === 'idle'
    root.dataset.state = state
    message.textContent = next.message || ({ success: 'Completed', failed: 'Task failed',
      cancelled: 'Cancelled' }[state] ?? (formatStage(next.stage) || (running ? 'Working…' : '')))
    count.textContent = determinate ? `${processed}/${total}` : ''
    track.hidden = !running
    track.dataset.indeterminate = String(running && !determinate)
    track.removeAttribute('aria-valuenow'); track.removeAttribute('aria-valuemax')
    if (determinate) {
      track.setAttribute('aria-valuemin', '0')
      track.setAttribute('aria-valuenow', String(Math.min(processed, total)))
      track.setAttribute('aria-valuemax', String(total))
      fill.style.width = `${Math.min(100, processed / total * 100)}%`
    } else {
      track.removeAttribute('aria-valuemin')
      fill.style.width = ''
    }
    if (cancelButton) {
      cancelButton.hidden = !running
      cancelButton.disabled = !running || cancelling
    }
    if (TERMINAL.has(state)) root.setAttribute('aria-live', state === 'failed' ? 'assertive' : 'polite')
    else root.setAttribute('aria-live', 'polite')
  }

  update()
  return { update, get state() { return state } }
}
