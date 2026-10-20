import { describe, expect, it } from 'vitest'

import config from '../vite.config.js'
import { JSDOM } from 'jsdom'

describe('Vite development server', () => {
  it('restores the theme before styles for every HTML entry without waiting for a session', () => {
    const plugin = config.plugins.find(item => item.name === 'smarttest-theme-first-paint')
    const [script] = plugin.transformIndexHtml.handler()
    const dom = new JSDOM(`<head><script>${script.children}</script><link rel="stylesheet" href="theme.css"></head>`, {
      url: 'https://smarttest.local', runScripts: 'dangerously',
      beforeParse(window) { window.localStorage.setItem('smarttest:theme', 'dark') },
    })
    expect(script.injectTo).toBe('head-pre')
    expect(dom.window.document.documentElement.classList.contains('dark-theme')).toBe(true)
    dom.window.SmartTestTheme.apply('light')
    expect(dom.window.localStorage.getItem('smarttest:theme')).toBe('light')
    dom.window.SmartTestTheme.clear()
    expect(dom.window.localStorage.getItem('smarttest:theme')).toBeNull()
    dom.window.close()
  })
  it('forwards API requests to the FastAPI development server', () => {
    expect(config.server.proxy['/api'].target).toBe('http://127.0.0.1:8000')
  })

  it('builds Projects as the only project page', () => {
    expect(config.build.rollupOptions.input.projects).toMatch(/projects\.html$/)
    expect(config.build.rollupOptions.input).not.toHaveProperty('confluence')
  })

  it('builds the Tools page', () => {
    expect(config.build.rollupOptions.input.tools).toMatch(/tools\.html$/)
  })

  it('builds the Test Management page', () => {
    expect(config.build.rollupOptions.input.testManagement).toMatch(/test-management\.html$/)
  })

  it('injects the shared Web brand into every HTML entry', () => {
    const plugin = config.plugins.find(item => item.name === 'smarttest-web-brand')
    expect(plugin.transformIndexHtml('<title>Page - __SMARTTEST_WEB_BRAND__</title>'))
      .toBe('<title>Page - FAE QA Data Center</title>')
  })
})
