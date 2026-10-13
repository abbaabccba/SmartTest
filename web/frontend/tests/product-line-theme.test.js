// @vitest-environment jsdom
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, expect, it, onTestFinished, vi } from 'vitest'

function mountProductLineTheme() {
  const source = readFileSync(resolve(import.meta.dirname, '../src/product-line-theme.css'), 'utf8')
  const wirelessRule = source.match(/\[data-product-line="Wireless Connection"\]\[data-product-surface\] \{[^}]+\}/)?.[0]
  const darkWirelessRule = source.match(/\.dark-theme \[data-product-line="Wireless Connection"\]\[data-product-surface\] \{[^}]+\}/)?.[0]
  const compactRule = source.match(/\[data-product-line="Wireless Connection"\]\[data-product-surface\]:is\([^}]+\}/)?.[0]
  const layoutRule = source.match(/\[data-product-line="Wireless Connection"\]\[data-product-surface="layout"\] \{[^}]+\}/)?.[0]
  const nestedRule = source.match(/\[data-product-surface="layout"\] \[data-product-line\]\[data-product-surface\]:not\(\[data-product-surface="layout"\]\) \{[^}]+\}/)?.[0]
  const transparentDisclosureRule = source.match(/\[data-product-surface="layout"\] \[data-product-surface="disclosure"\] \{[^}]+\}/)?.[0]
  const transparentGroupRule = source.match(/\[data-product-surface="layout"\] :is\(\.stage-group, \.launch-os-group, \.stage-summary, \.launch-os-summary\) \{[^}]+\}/)?.[0]
  const motionRule = source.match(/@keyframes product-ofdm-flow \{[\s\S]*?\n\}/)?.[0]
  expect(wirelessRule && darkWirelessRule && compactRule && layoutRule && nestedRule && transparentDisclosureRule && transparentGroupRule && motionRule).toBeTruthy()
  const style = document.createElement('style')
  style.textContent = `.stage-summary { background: rgb(255, 255, 255); }\n${wirelessRule}\n${compactRule}\n${layoutRule}\n${darkWirelessRule}\n${nestedRule}\n${transparentDisclosureRule}\n${transparentGroupRule}\n${motionRule}`
  document.head.append(style)
  return style.sheet
}

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.head.innerHTML = ''
  document.body.innerHTML = ''
  document.documentElement.className = ''
})

it('bounds TV artwork and shares its pause, resize, replacement and cleanup lifecycle', async () => {
  const environment = canvasEnvironment()
  const { initializeProductLineMotion } = await import('../src/product-line-motion.js')
  document.body.innerHTML = '<section data-product-line="TV" data-product-surface="layout"><button data-product-line="TV" data-product-surface="button">Content</button></section>'
  const layout = document.querySelector('section')
  const stop = initializeProductLineMotion(environment)
  onTestFinished(stop)
  environment.resize([{ target: layout, contentRect: { width: 1300, height: 430 } }])
  const layer = layout.querySelector('.tv-art')
  expect(layer).not.toBeNull()
  expect(layer.children).toHaveLength(3)
  expect(document.querySelectorAll('.tv-art')).toHaveLength(1)
  expect(new Set([...layer.children].map(tv => tv.style.getPropertyValue('--tv-period'))).size).toBe(3)
  expect(layer.getAttribute('aria-hidden')).toBe('true')
  environment.intersection([{ target: layout, isIntersecting: false }])
  expect(layer.classList.contains('smart-motion-paused')).toBe(true)
  environment.intersection([{ target: layout, isIntersecting: true }])
  expect(layer.classList.contains('smart-motion-paused')).toBe(false)
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
  document.dispatchEvent(new Event('visibilitychange'))
  expect(layer.classList.contains('smart-motion-paused')).toBe(true)
  visibility.mockReturnValue('visible')
  document.dispatchEvent(new Event('visibilitychange'))
  expect(layer.classList.contains('smart-motion-paused')).toBe(false)
  environment.media.matches = true
  environment.media.dispatchEvent(new Event('change'))
  expect(layer.classList.contains('smart-motion-paused')).toBe(true)
  layout.dataset.productLine = 'Smart Device'
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(layout.querySelector('.tv-art')).toBeNull()
  expect(layout.querySelector('.smart-device-art')).not.toBeNull()
  layout.dataset.productLine = 'TV'
  await new Promise(resolve => setTimeout(resolve, 0))
  environment.resize([{ target: layout, contentRect: { width: 140, height: 42 } }])
  expect(layout.querySelector('.tv-art').children).toHaveLength(1)
  expect(layout.querySelector('button').textContent).toBe('Content')
  stop()
  expect(layout.querySelector('.tv-art')).toBeNull()
  expect(environment.observed.size).toBe(0)
})

it('uses the Wireless OFDM motion across every supported product surface', () => {
  const sheet = mountProductLineTheme()
  const surfaces = ['layout', 'card', 'button', 'badge', 'disclosure'].map(surface => {
    const element = document.createElement(surface === 'button' ? 'button' : 'div')
    element.dataset.productLine = 'Wireless Connection'
    element.dataset.productSurface = surface
    document.body.append(element)
    return element
  })

  for (const [index, surface] of surfaces.entries()) {
    const computed = getComputedStyle(surface)
    expect(computed.getPropertyValue('--product-motion').trim()).toBe('product-ofdm-flow 9.5s linear infinite')
    const expectedSize = index < 2 ? '480px 120px' : '240px 60px'
    expect(computed.getPropertyValue('--product-motion-size').trim()).toBe(expectedSize)
    expect(computed.backgroundRepeat).toBe('repeat-x')
  }
  const motion = [...sheet.cssRules].find(rule => rule.name === 'product-ofdm-flow')
  expect([...motion.cssRules].map(rule => [rule.keyText, rule.style.getPropertyValue('background-position')])).toEqual([
    ['from', '0 50%'],
    ['to', 'var(--product-ofdm-shift) 50%'],
  ])

  const child = document.createElement('article')
  child.dataset.productLine = 'Wireless Connection'
  child.dataset.productSurface = 'disclosure'
  surfaces[0].append(child)
  expect(getComputedStyle(child).getPropertyValue('--product-motion').trim()).toBe('none')
  expect(getComputedStyle(child).backgroundImage).toBe('none')
  expect(getComputedStyle(child).backgroundColor).toBe('rgba(0, 0, 0, 0)')

  const stageGroup = document.createElement('section')
  stageGroup.className = 'stage-group'
  const stageSummary = document.createElement('summary')
  stageSummary.className = 'stage-summary'
  stageGroup.append(stageSummary)
  surfaces[0].append(stageGroup)
  expect(getComputedStyle(stageGroup).backgroundColor).toBe('rgba(0, 0, 0, 0)')
  expect(getComputedStyle(stageSummary).backgroundColor).toBe('rgba(0, 0, 0, 0)')

  document.documentElement.classList.add('dark-theme')
  expect(getComputedStyle(surfaces[0]).getPropertyValue('--product-motion-pattern')).toContain('wireless-ofdm-dark.svg')
  expect(getComputedStyle(child).getPropertyValue('--product-motion-pattern').trim()).toBe('none')
  expect(getComputedStyle(child).backgroundImage).toBe('none')
})

it('draws one clipped global globe and stops, resumes and removes it with the surface', async () => {
  const environment = canvasEnvironment()
  const { initializeProductLineMotion } = await import('../src/product-line-motion.js')
  document.body.innerHTML = '<button data-product-line="Global Operator & STB" data-product-surface="button">Global</button>'
  const button = document.querySelector('button')
  const stop = initializeProductLineMotion(environment)
  onTestFinished(stop)
  const canvas = button.querySelector('.global-globe')
  expect(canvas).not.toBeNull()
  environment.resize([{ target: button, contentRect: { width: 140, height: 42 } }])
  environment.frame(1000)
  expect(environment.contexts.get(canvas).labels).toHaveLength(0)
  button.disabled = true
  await new Promise(resolve => setTimeout(resolve, 0))
  environment.frame(2000)
  expect(environment.frames.size).toBe(0)
  button.disabled = false
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(environment.frames.size).toBe(1)
  environment.intersection([{ target: button, isIntersecting: false }])
  expect(environment.frames.size).toBe(0)
  environment.intersection([{ target: button, isIntersecting: true }])
  expect(environment.frames.size).toBe(1)
  stop()
  expect(document.querySelector('canvas')).toBeNull()
})

it('mounts the login full globe through the shared lifecycle and disposes it when replaced', async () => {
  const environment = canvasEnvironment()
  vi.stubGlobal('ResizeObserver', environment.ResizeObserver)
  const { applyLoginBackground, disposeLoginBackground } = await import('../src/login-background.js')
  document.body.innerHTML = '<main data-login-board><form><input></form></main>'
  const storage = { getItem: () => 'globe', setItem() {} }
  expect(applyLoginBackground({ storage })).toBe('globe')
  await vi.dynamicImportSettled()
  const surface = document.querySelector('[data-login-board]')
    const globe = surface.querySelector('[data-globe-framing="login"]')
  expect(globe).not.toBeNull()
  environment.resize([{ target: globe, contentRect: { width: 900, height: 800 } }])
  environment.frame(1000)
  expect(surface.querySelectorAll('canvas')).toHaveLength(1)
  applyLoginBackground({ storage })
  expect(surface.querySelectorAll('canvas')).toHaveLength(1)
  environment.media.matches = true
  environment.media.dispatchEvent(new Event('change'))
  environment.frame(2000)
  expect(environment.frames.size).toBe(0)
  applyLoginBackground({ storage: { getItem: () => 'pcb' } })
  expect(surface.querySelector('canvas')).toBeNull()
  expect(environment.observed.size).toBe(0)
  expect(surface.querySelector('.pcb-motion')).not.toBeNull()
  expect(surface.querySelector('input')).not.toBeNull()
  disposeLoginBackground(surface)
  expect(surface.querySelector('.pcb-motion')).toBeNull()
})

it('keeps nested global controls on one static reduced-motion globe and releases detached layouts', async () => {
  const environment = canvasEnvironment(true)
  const { initializeProductLineMotion } = await import('../src/product-line-motion.js')
  document.body.innerHTML = '<section data-product-line="Global Operator & STB" data-product-surface="layout"><button data-product-line="Global Operator & STB" data-product-surface="button">Content</button></section>'
  const layout = document.querySelector('section')
  const stop = initializeProductLineMotion(environment)
  onTestFinished(stop)
  environment.resize([{ target: layout, contentRect: { width: 900, height: 430 } }])
  expect(document.querySelectorAll('canvas')).toHaveLength(1)
  environment.frame(1000)
  expect(environment.frames.size).toBe(0)
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
  document.dispatchEvent(new Event('visibilitychange'))
  expect(environment.frames.size).toBe(0)
  visibility.mockReturnValue('visible')
  document.dispatchEvent(new Event('visibilitychange'))
  environment.frame(2000)
  expect(environment.frames.size).toBe(0)
  environment.media.matches = false
  environment.media.dispatchEvent(new Event('change'))
  expect(environment.frames.size).toBe(1)
  layout.remove()
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(environment.frames.size).toBe(0)
  expect(environment.observed.has(layout)).toBe(false)
})

function canvasEnvironment(reduced = false) {
  const frames = new Map()
  let nextFrame = 0
  vi.stubGlobal('requestAnimationFrame', callback => { frames.set(++nextFrame, callback); return nextFrame })
  vi.stubGlobal('cancelAnimationFrame', id => frames.delete(id))
  const media = Object.assign(new EventTarget(), { matches: reduced })
  vi.stubGlobal('matchMedia', () => media)
  vi.stubGlobal('Image', class {
    naturalWidth = 32
    naturalHeight = 32
    listeners = new Map()
    addEventListener(type, callback) { this.listeners.set(type, callback) }
    set src(value) { this.url = value; this.listeners.get('load')?.() }
    decode() { return Promise.resolve() }
  })
  const contexts = new Map()
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function () {
    if (!contexts.has(this)) {
      const draws = []
      const labels = []
      const gradient = { addColorStop() {} }
      const context = { draws, labels, globalAlpha: 1, clearRect() { draws.length = 0; labels.length = 0 }, setTransform() {}, fillRect() {}, drawImage(...args) { draws.push({ alpha: this.globalAlpha, args }) }, getImageData() { return { data: new Uint8ClampedArray([8, 190, 244, 255]) } }, putImageData() {}, beginPath() {}, moveTo() {}, lineTo() {}, closePath() {}, arc() {}, fill() {}, stroke() {}, save() {}, restore() {}, clip() {}, createRadialGradient() { return gradient }, fillText(text) { labels.push(text) }, measureText(text) { return { width: text.length * 6 } } }
      contexts.set(this, context)
    }
    return contexts.get(this)
  })
  let resize
  let intersection
  const observed = new Set()
  class ResizeObserver {
    constructor(callback) { resize = callback }
    observe(element) { observed.add(element) }
    unobserve(element) { observed.delete(element) }
    disconnect() { observed.clear() }
  }
  vi.stubGlobal('IntersectionObserver', class {
    constructor(callback) { intersection = callback }
    observe() {} unobserve() {} disconnect() {}
  })
  return { ResizeObserver, contexts, observed, media,
    resize: entries => resize(entries), intersection: entries => intersection(entries),
    frames, frame: time => { const [id, callback] = frames.entries().next().value; frames.delete(id); callback(time) } }
}

it('draws independent cached operator sprites in one canvas and releases detached surfaces', async () => {
  const environment = canvasEnvironment()
  const { initializeProductLineMotion } = await import('../src/product-line-motion.js')
  document.body.innerHTML = '<section data-product-line="China Operator" data-product-surface="layout"><button data-product-line="China Operator" data-product-surface="button">Content</button></section><article data-product-line="TV" data-product-surface="card"></article>'
  let sample = 0
  vi.spyOn(Math, 'random').mockImplementation(() => (sample++ % 90 + 1) / 100)
  const stop = initializeProductLineMotion(environment)
  const layout = document.querySelector('section')
  environment.resize([{ target: layout, contentRect: { width: 200, height: 80 } }])
  const canvas = layout.querySelector('canvas')
  expect(canvas).not.toBeNull()
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
  environment.frame(1500)
  const draws = environment.contexts.get(canvas).draws
  expect(draws.length).toBeGreaterThanOrEqual(10)
  expect(new Set(draws.map(draw => draw.alpha)).size).toBeGreaterThan(2)
  expect(new Set(draws.map(draw => draw.args[1])).size).toBe(5)
  expect(document.querySelectorAll('canvas')).toHaveLength(1)
  expect(layout.querySelector('button').textContent).toBe('Content')
  expect(canvas.getAttribute('aria-hidden')).toBe('true')
  environment.intersection([{ target: layout, isIntersecting: false }])
  expect(environment.frames.size).toBe(0)
  layout.remove()
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(environment.observed.has(layout)).toBe(false)
  stop()
  expect(environment.frames.size).toBe(0)
})

it('renders reduced-motion operator backgrounds once instead of continuously scheduling frames', async () => {
  const environment = canvasEnvironment(true)
  const { initializeProductLineMotion } = await import('../src/product-line-motion.js')
  document.body.innerHTML = '<article data-product-line="China Operator" data-product-surface="card"></article>'
  const stop = initializeProductLineMotion(environment)
  environment.resize([{ target: document.querySelector('article'), contentRect: { width: 80, height: 40 } }])
  await Promise.resolve()
  await Promise.resolve()
  await Promise.resolve()
  environment.frame(1500)
  expect(environment.contexts.get(document.querySelector('canvas')).draws).toHaveLength(2)
  expect(environment.frames.size).toBe(0)
  environment.media.matches = false
  environment.media.dispatchEvent(new Event('change'))
  expect(environment.frames.size).toBe(1)
  stop()
  expect(environment.frames.size).toBe(0)
})
it('exposes artwork decoding errors and does not animate a replacement', async () => {
  const environment = canvasEnvironment()
  vi.stubGlobal('Image', class { decode() { return Promise.reject(new Error('decoding failed')) } })
  const { initializeProductLineMotion } = await import('../src/product-line-motion.js')
  document.body.innerHTML = '<article data-product-line="China Operator" data-product-surface="card"></article>'
  const stop = initializeProductLineMotion(environment)
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(document.querySelector('[role="alert"]').textContent).toContain('artwork could not load')
  expect(environment.frames.size).toBe(0)
  stop()
  expect(document.querySelector('[role="alert"]')).toBeNull()
})

it('resumes the operator canvas after a disabled button is re-enabled', async () => {
  const environment = canvasEnvironment()
  const { initializeProductLineMotion } = await import('../src/product-line-motion.js')
  document.body.innerHTML = '<button disabled data-product-line="China Operator" data-product-surface="button">China Operator</button>'
  const button = document.querySelector('button')
  const stop = initializeProductLineMotion(environment)
  environment.resize([{ target: button, contentRect: { width: 80, height: 40 } }])
  await new Promise(resolve => setTimeout(resolve, 0))
  environment.frame(1500)
  expect(environment.frames.size).toBe(0)
  button.disabled = false
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(environment.frames.size).toBe(1)
  environment.frame(1600)
  expect(environment.contexts.get(document.querySelector('canvas')).draws.length).toBeGreaterThan(2)
  stop()
})

it('mounts bounded independent Smart Device products and pauses their lifecycle without a canvas', async () => {
  const environment = canvasEnvironment()
  const { initializeProductLineMotion } = await import('../src/product-line-motion.js')
  document.body.innerHTML = '<section data-product-line="Smart Device" data-product-surface="layout"><button data-product-line="Smart Device" data-product-surface="button">Content</button></section>'
  const layout = document.querySelector('section')
  const stop = initializeProductLineMotion(environment)
  environment.resize([{ target: layout, contentRect: { width: 1334, height: 436 } }])
  const layer = layout.querySelector('.smart-device-art')
  expect(layer).not.toBeNull()
  expect(new Set([...layer.querySelectorAll('[data-smart-device]')].map(svg => svg.dataset.smartDevice)).size).toBe(13)
  expect(new Set([...layer.children].map(product => product.style.getPropertyValue('--device-period'))).size).toBeGreaterThan(1)
  expect(document.querySelectorAll('.smart-device-art')).toHaveLength(1)
  expect(document.querySelector('canvas')).toBeNull()
  expect(layer.getAttribute('aria-hidden')).toBe('true')
  environment.intersection([{ target: layout, isIntersecting: false }])
  expect(layer.classList.contains('smart-motion-paused')).toBe(true)
  environment.intersection([{ target: layout, isIntersecting: true }])
  expect(layer.classList.contains('smart-motion-paused')).toBe(false)
  environment.media.matches = true
  environment.media.dispatchEvent(new Event('change'))
  expect(layer.classList.contains('smart-motion-paused')).toBe(true)
  layout.remove()
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(environment.observed.has(layout)).toBe(false)
  stop()
})

it('replaces China canvas with Smart Device artwork when the same surface changes product line', async () => {
  const environment = canvasEnvironment()
  const { initializeProductLineMotion } = await import('../src/product-line-motion.js')
  document.body.innerHTML = '<button data-product-line="China Operator" data-product-surface="button">Product</button>'
  const button = document.querySelector('button')
  const stop = initializeProductLineMotion(environment)
  expect(button.querySelector('canvas')).not.toBeNull()
  button.dataset.productLine = 'Smart Device'
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(button.querySelector('canvas')).toBeNull()
  environment.resize([{ target: button, contentRect: { width: 140, height: 42 } }])
  const layer = button.querySelector('.smart-device-art')
  expect(layer.children).toHaveLength(1)
  button.disabled = true
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(layer.classList.contains('smart-motion-paused')).toBe(true)
  button.disabled = false
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(layer.classList.contains('smart-motion-paused')).toBe(false)
  stop()
  expect(button.querySelector('.smart-device-art')).toBeNull()
})
