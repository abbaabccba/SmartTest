// @vitest-environment jsdom
import { expect, it } from 'vitest'
import { icon } from '../src/icons.js'

it('keeps interface icons decorative while the action owns its accessible name', () => {
  document.body.innerHTML = `<button aria-label="Copy current JQL">${icon('copy')}</button>`
  const button = document.querySelector('button')
  const symbol = button.querySelector('svg')
  expect(button.getAttribute('aria-label')).toBe('Copy current JQL')
  expect(symbol.getAttribute('aria-hidden')).toBe('true')
  expect(symbol.getAttribute('focusable')).toBe('false')
  expect(symbol.getAttribute('stroke')).toBe('currentColor')
})

it('switches copy feedback through the same semantic icon owner', () => {
  const button = document.createElement('button')
  button.innerHTML = icon('copy')
  const initial = button.innerHTML
  button.innerHTML = icon('check')
  expect(button.querySelectorAll('svg')).toHaveLength(1)
  expect(button.innerHTML).not.toBe(initial)
  expect(button.querySelector('svg').getAttribute('aria-hidden')).toBe('true')
})
