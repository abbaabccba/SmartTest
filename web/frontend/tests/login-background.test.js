// @vitest-environment jsdom
import { describe, expect, it, vi } from 'vitest'

import { applyLoginBackground, selectLoginBackground } from '../src/login-background.js'

function memoryStorage(initial = {}) {
  const values = new Map(Object.entries(initial))
  return {
    getItem: key => values.get(key) ?? null,
    setItem: vi.fn((key, value) => values.set(key, value)),
  }
}

describe('login background selection', () => {
  it('reuses the session selection on repeated application without changing the form', () => {
    document.body.innerHTML = '<main data-login-board><input value="kept"></main>'
    const storage = memoryStorage()
    expect(applyLoginBackground({ storage, random: () => 0 })).toBe('pcb')
    expect(applyLoginBackground({ storage, random: () => 0.99 })).toBe('pcb')
    expect(document.querySelector('input').value).toBe('kept')
    expect(document.querySelectorAll('.pcb-motion')).toHaveLength(1)
  })

  const backgrounds = [{ id: 'pcb' }, { id: 'lab' }, { id: 'silicon' }]

  it('keeps the selected background for the browser session', () => {
    const storage = memoryStorage()

    expect(selectLoginBackground({ backgrounds, storage, random: () => 0.6 })).toBe('lab')
    expect(selectLoginBackground({ backgrounds, storage, random: () => 0.99 })).toBe('lab')
    expect(storage.setItem).toHaveBeenCalledTimes(1)
  })

  it('replaces a stored background that is no longer available', () => {
    const storage = memoryStorage({ 'smarttest.login.background': 'retired' })

    expect(selectLoginBackground({ backgrounds, storage, random: () => 0 })).toBe('pcb')
    expect(storage.setItem).toHaveBeenLastCalledWith('smarttest.login.background', 'pcb')
  })

  it('applies the selected background to the login surface', () => {
    document.body.innerHTML = '<main data-login-board></main>'

    expect(applyLoginBackground({ root: document, backgrounds: [{ id: 'pcb' }], storage: memoryStorage() })).toBe('pcb')
    expect(document.querySelector('[data-login-board]').dataset.loginBackground).toBe('pcb')
  })
})
