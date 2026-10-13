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
  it('switches the preview selection repeatedly and remembers it without changing the form', () => {
    document.body.innerHTML = '<select data-login-background-switch><option value="pcb">PCB</option><option value="lab">Lab</option></select><main data-login-board><input value="kept"></main>'
    const storage = memoryStorage({ 'smarttest.login.background': 'pcb' })
    applyLoginBackground({ backgrounds: [{ id: 'pcb' }, { id: 'lab' }], storage })
    const control = document.querySelector('select')
    for (const id of ['lab', 'pcb', 'lab']) {
      control.value = id
      control.dispatchEvent(new Event('change'))
      expect(document.querySelector('[data-login-board]').dataset.loginBackground).toBe(id)
      expect(storage.getItem('smarttest.login.background')).toBe(id)
      expect(document.querySelector('input').value).toBe('kept')
    }
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
