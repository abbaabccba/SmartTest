// @vitest-environment jsdom
import { afterEach, expect, it, vi } from 'vitest'
afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
  document.head.innerHTML = ''
  document.body.innerHTML = ''
  document.documentElement.className = ''
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
  environment.intersection([{ target: globe, isIntersecting: false }])
  expect(environment.frames.size).toBe(0)
  environment.intersection([{ target: globe, isIntersecting: true }])
  expect(environment.frames.size).toBe(1)
  const visibility = vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
  document.dispatchEvent(new Event('visibilitychange'))
  expect(environment.frames.size).toBe(0)
  visibility.mockReturnValue('visible')
  document.dispatchEvent(new Event('visibilitychange'))
  expect(environment.frames.size).toBe(1)
  environment.media.matches = true
  environment.media.dispatchEvent(new Event('change'))
  environment.frame(2000)
  expect(environment.frames.size).toBe(0)
  document.documentElement.classList.add('dark-theme')
  await new Promise(resolve => setTimeout(resolve, 0))
  expect(environment.frames.size).toBe(1)
  environment.frame(2100)
  expect(environment.frames.size).toBe(0)
  applyLoginBackground({ storage: { getItem: () => 'pcb' } })
  expect(surface.querySelector('canvas')).toBeNull()
  expect(environment.observed.size).toBe(0)
  expect(surface.querySelector('.pcb-motion')).not.toBeNull()
  expect(surface.querySelector('input')).not.toBeNull()
  disposeLoginBackground(surface)
  expect(surface.querySelector('.pcb-motion')).toBeNull()
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

