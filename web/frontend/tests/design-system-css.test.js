// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { beforeEach, describe, expect, it } from 'vitest'

const css = readFileSync(resolve(import.meta.dirname, '../src/smarttest-theme.css'), 'utf8')

function mountTheme(markup = '') {
  const style = document.createElement('style')
  style.textContent = css
  document.head.append(style)
  document.body.innerHTML = markup
}

function token(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function resolved(element, property) {
  let value = getComputedStyle(element)[property]
  for (let index = 0; index < 4 && value.startsWith('var('); index += 1) {
    value = token(value.slice(4, -1))
  }
  return value
}

describe('shared Web design system behavior', () => {
  beforeEach(() => {
    document.documentElement.className = ''
    document.head.innerHTML = ''
    document.body.className = ''
    document.body.innerHTML = ''
  })

  it('resolves shared foundations onto real shell and content elements', () => {
    mountTheme('<nav class="top-nav"></nav><main class="main-content"><section class="card"></section></main>')
    const root = getComputedStyle(document.documentElement)
    const nav = getComputedStyle(document.querySelector('.top-nav'))

    expect(token('--st-space-1')).toBe('.25rem')
    expect(token('--st-space-7')).toBe('3rem')
    expect(token('--st-surface-content')).toBe('#ffffff')
    expect(getComputedStyle(document.body).fontFamily).toContain('-apple-system')
    expect(resolved(document.querySelector('.card'), 'borderRadius')).toBe(token('--st-radius-card'))
    expect(resolved(document.querySelector('.card'), 'backgroundColor')).toBe('#ffffff')
    expect(nav.position).toBe('sticky')
    expect(resolved(document.querySelector('.top-nav'), 'background')).toBe('rgba(250, 250, 252, .82)')
    expect(root.colorScheme).toBe('light')
  })

  it('styles the public active segmented state and shared controls consistently', () => {
    mountTheme(`<div class="role-segments"><button>First</button><button class="active">Second</button></div>
      <button class="button button-primary">Apply</button><input class="form-control">`)
    const inactive = getComputedStyle(document.querySelector('.role-segments button'))
    const active = getComputedStyle(document.querySelector('.role-segments button.active'))

    expect(inactive.boxShadow).toBe('')
    expect(resolved(document.querySelector('.role-segments button.active'), 'background')).toBe('#ffffff')
    expect(active.boxShadow).not.toBe('')
    expect(resolved(document.querySelector('.button'), 'minHeight')).toBe(token('--st-control-height'))
    expect(resolved(document.querySelector('.form-control'), 'minHeight')).toBe(token('--st-control-height'))
    expect(resolved(document.querySelector('.button'), 'borderRadius')).toBe(token('--st-radius-control'))
    expect(resolved(document.querySelector('.form-control'), 'borderRadius')).toBe(token('--st-radius-control'))
  })

  it('resolves dark theme surfaces and text through the same component owners', () => {
    document.documentElement.className = 'dark-theme'
    mountTheme('<nav class="top-nav"></nav><section class="card"><input class="form-control"></section>')
    expect(token('--st-surface-content')).toBe('#1c1c1e')
    expect(token('--st-text-primary')).toBe('#f5f5f7')
    expect(getComputedStyle(document.documentElement).colorScheme).toBe('dark')
    expect(resolved(document.querySelector('.card'), 'backgroundColor')).toBe('#1c1c1e')
    expect(resolved(document.querySelector('.form-control'), 'background')).toBe('#242426')
  })

  it('uses one content surface for base, dashboard, filter, detail and product cards', () => {
    mountTheme(`<section class="card" data-test="base"></section>
      <section class="card dashboard-widget-card" data-test="dashboard"></section>
      <aside class="card filter-panel" data-test="filter"></aside>
      <aside class="card jira-detail-panel" data-test="detail"></aside>
      <section class="product-space-group">
        <div class="stage-groups"><details class="stage-group"><div class="project-list">
          <article class="kanban-card project-card"></article>
        </div></details></div>
      </section>`)
    const cards = [...document.querySelectorAll('[data-test], .project-card')]
    const expected = {
      backgroundColor: token('--st-surface-content'),
      borderRadius: token('--st-radius-card'),
      boxShadow: token('--st-shadow-card'),
    }

    for (const card of cards) {
      expect(resolved(card, 'backgroundColor')).toBe(expected.backgroundColor)
      expect(resolved(card, 'borderRadius')).toBe(expected.borderRadius)
      expect(resolved(card, 'boxShadow')).toBe(expected.boxShadow)
      expect(getComputedStyle(card).transform).not.toContain('translate')
      expect(getComputedStyle(card).backgroundImage).not.toContain('linear-gradient')
    }
  })
})

it('keeps modal details viewport centered despite the global reset and card cascade', () => {
  const detailsStyle = document.createElement('style')
  detailsStyle.textContent = readFileSync(resolve(import.meta.dirname, '../src/data-details.css'), 'utf8')
  mountTheme('<dialog class="data-details" open></dialog>')
  document.head.append(detailsStyle)
  const dialog = getComputedStyle(document.querySelector('dialog'))
  expect(dialog.position).toBe('fixed')
  expect(dialog.inset).toBe('0')
  expect(dialog.marginTop).toBe('auto')
  expect(dialog.marginRight).toBe('auto')
  expect(dialog.marginBottom).toBe('auto')
  expect(dialog.marginLeft).toBe('auto')
})

it('shares navigation glass across popup surfaces and navigation blur across backdrop surfaces', () => {
  mountTheme(`<nav class="top-nav"></nav><div class="nav-submenu"></div><div class="user-dropdown"></div>
    <div class="jira-more-menu"></div><div class="multi-select__dropdown"></div><div class="mobile-menu"></div>
    <div class="dashboard-widget-picker"></div><div class="nav-backdrop"></div><div class="mobile-menu-overlay"></div>`)
  const glass = token('--st-glass-filter')
  const overlay = token('--st-overlay-filter')
  expect(glass).toBe('saturate(160%) blur(22px)')
  expect(overlay).toBe('blur(6px)')
  const filter = element => {
    let value = ''
    for (const sheet of document.styleSheets) for (const rule of sheet.cssRules) {
      const declared = rule.style?.getPropertyValue('backdrop-filter')
      if (declared && rule.selectorText.split(',').filter(selector => !selector.includes('::')).some(selector => element.matches(selector))) value = declared
    }
    return value.startsWith('var(') ? token(value.slice(4, -1)) : value
  }
  for (const selector of ['.top-nav', '.nav-submenu', '.user-dropdown', '.jira-more-menu', '.multi-select__dropdown', '.mobile-menu', '.dashboard-widget-picker']) {
    expect(filter(document.querySelector(selector))).toBe(glass)
  }
  for (const selector of ['.nav-backdrop', '.mobile-menu-overlay']) {
    expect(filter(document.querySelector(selector))).toBe(overlay)
  }
})
