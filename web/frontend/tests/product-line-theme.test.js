// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, expect, it, vi } from 'vitest'
import { bindProductLine } from '../src/product-line-semantics.js'
import { createRankingCard } from '../src/widgets/ranking-card.js'

const lines = ['China Operator', 'Smart Device', 'TV', 'Global Operator & STB', 'Wireless Connection']
function mountStyles() {
  const style = document.createElement('style')
  style.textContent = readFileSync(resolve(import.meta.dirname, '../src/product-line-theme.css'), 'utf8')
  document.head.append(style)
}
afterEach(() => {
  vi.restoreAllMocks()
  document.head.innerHTML = ''
  document.body.innerHTML = ''
  document.documentElement.className = ''
})

it.each(['light', 'dark'])('keeps all five identities solid on every %s surface', theme => {
  mountStyles()
  if (theme === 'dark') document.documentElement.classList.add('dark-theme')
  const tones = new Set()
  for (const line of lines) {
    for (const surface of ['layout', 'card', 'button', 'badge', 'disclosure']) {
      const element = bindProductLine(document.createElement(surface === 'button' ? 'button' : 'div'), line, surface)
      document.body.append(element)
      const style = getComputedStyle(element)
      tones.add(style.getPropertyValue('--product-line-tone'))
      expect(style.backgroundImage).toBe('none')
      expect(style.getPropertyValue('--product-line-background')).toBeTruthy()
      expect(style.getPropertyValue('--product-line-background')).not.toMatch(/gradient|url/)
      expect(['', 'none']).toContain(style.animationName)
      expect(style.getPropertyValue('--product-line-icon')).toContain('/icons/')
    }
  }
  expect(tones.size).toBe(5)
})

it('retains identity selection and disabled feedback without creating background work', () => {
  mountStyles()
  const schedule = vi.spyOn(window, 'requestAnimationFrame')
  const root = document.createElement('section')
  document.body.append(root)
  const card = createRankingCard()
  const rowsFor = vi.fn(() => [])
  card.mount(root, { productLines: lines.map(line => ({ value: line, label: line, productLine: line })),
    modes: [{ value: 'basic', label: 'Bugs' }], rowsFor })
  const buttons = [...root.querySelectorAll('[data-product-line-segments] button')]
  expect(buttons.map(button => button.dataset.productLine)).toEqual(lines)
  for (const button of buttons) {
    button.click()
    expect(button.getAttribute('aria-pressed')).toBe('true')
    expect(rowsFor).toHaveBeenLastCalledWith(button.dataset.productLine, 'basic')
    expect(getComputedStyle(button).getPropertyValue('--product-line-foreground')).toMatch(/#(243342|123c56)/)
    button.disabled = true
    expect(getComputedStyle(button).opacity).toBe('0.5')
    expect(getComputedStyle(button).cursor).toBe('default')
    button.disabled = false
  }
  expect(schedule).not.toHaveBeenCalled()
  expect(root.querySelectorAll('canvas:not([data-ranked-chart]), svg')).toHaveLength(0)
  card.destroy()
})


it('reuses the two product palettes across opposite theme selection states', () => {
  mountStyles()
  for (const line of lines) {
    const button = bindProductLine(document.createElement('button'), line, 'button')
    document.body.append(button)
    const appearance = (dark, selected) => {
      document.documentElement.classList.toggle('dark-theme', dark)
      button.setAttribute('aria-pressed', String(selected))
      const style = getComputedStyle(button)
      return { background: style.backgroundColor, color: style.color,
        light: style.getPropertyValue('--product-line-background'),
        dark: style.getPropertyValue('--product-line-selected-background') }
    }
    const lightIdle = appearance(false, false)
    const lightSelected = appearance(false, true)
    expect(lightIdle.background).not.toBe(lightSelected.background)
    expect(lightIdle.color).not.toBe(lightSelected.color)
    expect(appearance(true, false)).toEqual(lightSelected)
    expect(appearance(true, true)).toEqual(lightIdle)
    expect(appearance(false, false)).toEqual(lightIdle)
  }
})
