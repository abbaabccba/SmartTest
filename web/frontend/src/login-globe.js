import { createGlobalGlobe } from './global-globe.js'

const selector = '.login-globe'
export function initializeLoginGlobe({ ResizeObserver = globalThis.ResizeObserver, root = document } = {}) {
  const surfaces = new Map()
  const media = matchMedia('(prefers-reduced-motion: reduce)')
  let frameId
  let stopped = false
  function draw(state, now, dpr) {
    const { context, canvas } = state
    if (canvas.width !== Math.ceil(state.width * dpr) || canvas.height !== Math.ceil(state.height * dpr)) {
      canvas.width = Math.ceil(state.width * dpr)
      canvas.height = Math.ceil(state.height * dpr)
    }
    context.setTransform(dpr, 0, 0, dpr, 0, 0)
    context.clearRect(0, 0, state.width, state.height)
    const dark = document.documentElement.classList.contains('dark-theme')
    if (!media.matches) state.globeTime = now
    state.globe(state.width, state.height, state.globeTime || 0, dark)
    state.dirty = false
  }
  function hasWork() {
    return !stopped && document.visibilityState === 'visible' && ([...surfaces.values()].some(state => state.visible && (state.width > 0 && state.height > 0) &&
      (state.dirty || !media.matches)))
  }
  function schedule() { if (!frameId && hasWork()) frameId = requestAnimationFrame(frame) }
  function frame(now) {
    frameId = undefined
    const dpr = devicePixelRatio || 1
    for (const state of surfaces.values()) {
      if (!state.visible || !(state.width > 0 && state.height > 0)) continue
      if (state.dirty || !media.matches) draw(state, now, dpr)
    }
    schedule()
  }
  function changed() {
    for (const state of surfaces.values()) {
      state.dirty = true
    }
    if (frameId) { cancelAnimationFrame(frameId); frameId = undefined }
    schedule()
  }
  const intersection = new IntersectionObserver(entries => {
    for (const entry of entries) {
      const state = surfaces.get(entry.target)
      if (state) { state.visible = entry.isIntersecting; state.dirty = true }
    }
    if (frameId && !hasWork()) { cancelAnimationFrame(frameId); frameId = undefined }
    schedule()
  })
  const resize = new ResizeObserver(entries => {
    for (const { target, contentRect } of entries) {
      const state = surfaces.get(target) || surfaces.get(target.closest(selector)) || surfaces.get(target.parentElement)
      if (!state) continue
      const surface = state.element
      state.width = surface.clientWidth || contentRect.width
      state.viewportHeight = surface.clientHeight || contentRect.height
      layoutSurface(state)
    }
    schedule()
  })
  function layoutSurface(state) {
    const background = state.canvas
    background.style.height = '0px'
    state.height = Math.max(state.element.clientHeight || state.viewportHeight, state.element.scrollHeight)
    background.style.height = `${state.height}px`
    state.dirty = true
  }
  function reconcile() {
    const eligible = new Set([...root.querySelectorAll(selector)])
    for (const [element, state] of surfaces) {
      if (eligible.has(element)) continue
      resize.unobserve(element)
      for (const content of state.content || []) resize.unobserve(content)
      intersection.unobserve(element)
      state.canvas.remove()
      surfaces.delete(element)
    }
    for (const element of eligible) {
      if (surfaces.has(element)) {
        const state = surfaces.get(element)
        observeContent(state)
        continue
      }
      const canvas = document.createElement('canvas')
      canvas.className = 'login-globe-canvas'
      canvas.setAttribute('aria-hidden', 'true')
      const state = { element, canvas, context: canvas.getContext('2d'), visible: true, dirty: true, width: 0, height: 0 }
      state.globe = createGlobalGlobe(state.context, { framing: 'login' })
      element.prepend(canvas)
      surfaces.set(element, state)
      resize.observe(element)
      observeContent(state)
      intersection.observe(element)
    }
    changed()
  }
  function observeContent(state) {
    const content = new Set([...state.element.children].filter(child => child !== state.canvas))
    for (const child of [...content]) for (const canvas of child.querySelectorAll('canvas')) content.add(canvas)
    for (const child of state.content || []) if (!content.has(child)) resize.unobserve(child)
    for (const child of content) if (!state.content?.has(child)) resize.observe(child)
    state.content = content
    if (state.width > 0) layoutSurface(state)
  }
  const mutations = new MutationObserver(records => {
    if (records.some(record => record.type === 'childList' && (surfaces.has(record.target) || [...record.addedNodes, ...record.removedNodes].some(node => node.nodeType === 1 && (node.matches(selector) || node.querySelector(selector)))))) reconcile()
    if (records.some(record => record.type === 'attributes' && [document.documentElement, document.body].includes(record.target))) changed()
  })
  reconcile()
  mutations.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['class'] })
  document.addEventListener('visibilitychange', changed)
  window.addEventListener('resize', changed)
  media.addEventListener('change', changed)
  return () => {
    stopped = true
    if (frameId) cancelAnimationFrame(frameId)
    mutations.disconnect()
    resize.disconnect()
    intersection.disconnect()
    document.removeEventListener('visibilitychange', changed)
    window.removeEventListener('resize', changed)
    media.removeEventListener('change', changed)
    for (const state of surfaces.values()) state.canvas.remove()
  }
}
