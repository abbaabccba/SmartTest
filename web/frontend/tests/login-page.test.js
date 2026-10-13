// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

import { initializeTheme } from '../src/theme.js'
import { createLoginPage, safeReturnPath } from '../src/login-page.js'

describe('SmartTest login page', () => {
  beforeEach(() => {
    const html = readFileSync(resolve(import.meta.dirname, '../login.html'), 'utf8')
    document.body.innerHTML = new DOMParser().parseFromString(html, 'text/html').body.innerHTML
    window.history.replaceState({}, '', '/login.html')
    initializeTheme()
    const setTheme = new Function(`${readFileSync(resolve(import.meta.dirname, '../public/smarttest-portal.js'), 'utf8')}\nreturn setTheme`)()
    for (const button of document.querySelectorAll('.theme-btn')) {
      button.removeAttribute('onclick')
      button.addEventListener('click', () => setTheme(button.classList.contains('theme-btn-dark') ? 'dark' : 'light'))
    }
  })

  it('authenticates once and returns to the requested internal page', async () => {
    window.history.replaceState({}, '', '/login.html?next=%2Fprojects.html%3Fview%3Downers')
    const api = { session: vi.fn().mockResolvedValue({ authenticated: false }), login: vi.fn().mockResolvedValue({ authenticated: true }) }
    const navigate = vi.fn()
    await createLoginPage({ root: document, api, navigate }).start()
    const form = document.querySelector('form')
    expect(form.elements.username.autocomplete).toBe('username')
    expect(form.elements.password.autocomplete).toBe('current-password')
    form.elements.username.value = 'coco'; form.elements.password.value = 'secret'
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith('/projects.html?view=owners'))
    expect(api.login).toHaveBeenCalledWith('coco', 'secret')
    expect(form.elements.password.value).toBe('')
  })

  it('does not allow an external return URL', () => {
    expect(safeReturnPath('https://evil.example/path')).toBe('/')
    expect(safeReturnPath('//evil.example/path')).toBe('/')
    expect(safeReturnPath('/jira.html')).toBe('/jira.html')
  })

  it('shows a safe failure and remains on the login page', async () => {
    const api = { session: vi.fn().mockResolvedValue({ authenticated: false }), login: vi.fn().mockRejectedValue(new Error('LDAP detail')) }
    const navigate = vi.fn()
    await createLoginPage({ root: document, api, navigate }).start()
    const form = document.querySelector('form')
    form.elements.username.value = 'coco'; form.elements.password.value = 'bad'
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }))
    await vi.waitFor(() => expect(document.querySelector('[data-auth-status]').textContent).toContain('Sign in failed'))
    expect(navigate).not.toHaveBeenCalled()
    expect(form.elements.password.value).toBe('')
  })
  it.each(['dark', 'light'])('saves an explicitly selected %s theme before navigating', async theme => {
    let finish
    const preferences = { put: vi.fn(() => new Promise(resolve => { finish = resolve })) }
    const api = { login: vi.fn(async () => { globalThis.SmartTestTheme.clear(); return { authenticated: true } }) }
    const navigate = vi.fn()
    createLoginPage({ root: document, api, preferences, navigate })
    document.querySelector(`.theme-btn-${theme}`).click()
    document.querySelector('form').dispatchEvent(new Event('submit', { cancelable: true }))
    await vi.waitFor(() => expect(preferences.put).toHaveBeenCalledWith('global', { theme }))
    expect(navigate).not.toHaveBeenCalled()
    finish()
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith('/'))
    expect(document.documentElement.classList.contains('dark-theme')).toBe(theme === 'dark')
  })

  it('does not save a first-paint hint or an authenticated redirect as a new selection', async () => {
    localStorage.setItem('smarttest:theme', 'dark')
    const preferences = { put: vi.fn() }
    const navigate = vi.fn()
    const api = { session: vi.fn().mockResolvedValue({ authenticated: true }), login: vi.fn().mockResolvedValue({ authenticated: true }) }
    await createLoginPage({ root: document, api, preferences, navigate }).start()
    document.querySelector('form').dispatchEvent(new Event('submit', { cancelable: true }))
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledTimes(2))
    expect(preferences.put).not.toHaveBeenCalled()
  })

  it('reports a theme save failure and does not navigate after successful authentication', async () => {
    const preferences = { put: vi.fn().mockRejectedValue(new Error('offline')) }
    const api = { login: vi.fn().mockResolvedValue({ authenticated: true }) }
    const navigate = vi.fn()
    createLoginPage({ root: document, api, preferences, navigate })
    document.querySelector('.theme-btn-dark').click()
    document.querySelector('form').dispatchEvent(new Event('submit', { cancelable: true }))
    await vi.waitFor(() => expect(document.querySelector('[data-auth-status]').textContent).toContain('Theme settings'))
    expect(navigate).not.toHaveBeenCalled()
  })

})
