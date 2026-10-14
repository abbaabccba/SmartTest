import mobile from '../public/icons/operators/china-mobile.svg?url'
import telecom from '../public/icons/operators/china-telecom.svg?url'
import unicom from '../public/icons/operators/china-unicom.svg?url'
import { smartDeviceArtwork, createSmartDeviceProduct } from './smart-device-artwork.js'
import { createTvProduct } from './tv-artwork.js'
import { createGlobalGlobe } from './global-globe.js'

const selector = ':is([data-product-line="China Operator"], [data-product-line="Smart Device"], [data-product-line="TV"], [data-product-line="Global Operator & STB"])[data-product-surface]'
const colors = ['#0064b4', '#0096ff', '#64c8ff', '#ff69b4', '#ffffff']
const ease = value => (1 - Math.cos(Math.PI * value)) / 2

export function initializeProductLineMotion({ ResizeObserver = globalThis.ResizeObserver, root = document } = {}) {
  const surfaces = new Map()
  const media = matchMedia('(prefers-reduced-motion: reduce)')
  let images
  let sprites
  let cachedDpr
  let frameId
  let stopped = false
  let loadError
  function prepareSprites(dpr) {
    sprites = images.map(image => colors.map((color, tone) => {
      const shape = document.createElement('canvas')
      shape.width = shape.height = Math.ceil(32 * dpr)
      const paint = shape.getContext('2d')
      const scale = Math.min(shape.width / image.naturalWidth, shape.height / image.naturalHeight)
      const width = image.naturalWidth * scale
      const height = image.naturalHeight * scale
      paint.drawImage(image, (shape.width - width) / 2, (shape.height - height) / 2, width, height)
      paint.globalCompositeOperation = 'source-in'
      paint.fillStyle = color
      paint.fillRect(0, 0, shape.width, shape.height)
      function raster(glow) {
        const cached = document.createElement('canvas')
        cached.width = cached.height = Math.ceil(64 * dpr)
        const context = cached.getContext('2d')
        if (glow) { context.shadowColor = color; context.shadowBlur = (tone === 4 ? 7 : 5) * dpr }
        context.drawImage(shape, 16 * dpr, 16 * dpr)
        return cached
      }
      return raster(tone >= 2)
    }))
    cachedDpr = dpr
  }
  function draw(state, now, dpr) {
    const { context, canvas, cells, columns } = state
    if (canvas.width !== Math.ceil(state.width * dpr) || canvas.height !== Math.ceil(state.height * dpr)) {
      canvas.width = Math.ceil(state.width * dpr)
      canvas.height = Math.ceil(state.height * dpr)
    }
    context.setTransform(dpr, 0, 0, dpr, 0, 0)
    context.clearRect(0, 0, state.width, state.height)
    const dark = document.documentElement.classList.contains('dark-theme')
    const animate = !media.matches && !state.element.matches(':disabled')
    if (state.globe) {
      if (animate) state.globeTime = now
      state.globe(state.width, state.height, state.globeTime || 0, dark)
      state.dirty = false
      return
    }
    for (const [index, cell] of cells.entries()) {
      if (index % columns % 3 !== 1 || Math.floor(index / columns) % 3 !== 1) continue
      const x = index % columns * 40 - 12
      const y = Math.floor(index / columns) * 40 - 12
      const cached = sprites[cell.logo]
      context.globalAlpha = dark ? .4 : .22
      context.drawImage(cached[dark ? 1 : 0], x, y, 64, 64)
      if (!animate) continue
      const phase = (now / cell.duration + cell.phase) % 1
      const blue = phase < .3 ? ease(phase / .3) : phase < .5 ? 1 - ease((phase - .3) / .2) : 0
      const pink = phase < .3 || phase >= .7 ? 0 : phase < .5 ? ease((phase - .3) / .2) : 1 - ease((phase - .5) / .2)
      const white = phase < .5 ? 0 : phase < .7 ? ease((phase - .5) / .2) : 1 - ease((phase - .7) / .3)
      for (const [tone, alpha] of [blue, pink, white].entries()) {
        if (alpha <= 0) continue
        context.globalAlpha = alpha
        context.drawImage(cached[tone + 2], x, y, 64, 64)
      }
    }
    context.globalAlpha = 1
    state.dirty = false
  }
  function hasWork() {
    return !stopped && document.visibilityState === 'visible' && ([...surfaces.values()].some(state => state.visible && (state.globe ? state.width > 0 && state.height > 0 : images && state.cells.length) &&
      (state.dirty || !media.matches && !state.element.matches(':disabled'))))
  }
  function schedule() { if (!frameId && hasWork()) frameId = requestAnimationFrame(frame) }
  function frame(now) {
    frameId = undefined
    const dpr = devicePixelRatio || 1
    if (images && dpr !== cachedDpr) prepareSprites(dpr)
    for (const state of surfaces.values()) {
      if (!state.visible || !(state.globe ? state.width > 0 && state.height > 0 : images && state.cells.length)) continue
      if (state.dirty || !media.matches && !state.element.matches(':disabled')) draw(state, now, dpr)
    }
    schedule()
  }
  function changed() {
    for (const state of surfaces.values()) {
      state.dirty = true
      if (state.deviceLayer) updateDeviceMotion(state)
    }
    if (frameId) { cancelAnimationFrame(frameId); frameId = undefined }
    schedule()
  }
  function updateDeviceMotion(state) {
    state.deviceLayer.classList.toggle('smart-motion-paused', !state.visible || document.visibilityState !== 'visible' || media.matches || state.element.matches(':disabled'))
  }
  function layoutDevice(state) {
    const tv = state.line === 'TV'
    const width = Math.min(state.width, 800)
    const height = Math.min(state.viewportHeight, 300)
    const count = tv ? (width >= 600 && height >= 160 ? 3 : width >= 300 && height >= 100 ? 2 : 1) : width >= 600 && height >= 160 ? smartDeviceArtwork.length : width >= 300 && height >= 100 ? 5 : 1
    const columns = Math.ceil(state.width / width)
    const rows = Math.ceil(state.height / height)
    while (state.deviceLayer.children.length > columns * rows) state.deviceLayer.lastElementChild.remove()
    for (let index = 0; index < columns * rows; index++) {
      let tile = state.deviceLayer.children[index]
      if (!tile) { tile = document.createElement('div'); tile.style.position = 'absolute'; state.deviceLayer.append(tile) }
      tile.style.left = `${index % columns * width}px`
      tile.style.top = `${Math.floor(index / columns) * height}px`
      tile.style.width = `${width}px`
      tile.style.height = `${height}px`
      while (tile.children.length > count) tile.lastElementChild.remove()
      while (tile.children.length < count) {
        const product = tile.children.length
        tile.append(tv ? createTvProduct(product) : createSmartDeviceProduct(smartDeviceArtwork[product], product))
      }
    }
    state.deviceLayer.style.setProperty(tv ? '--tv-scale' : '--device-scale', count === 1 ? Math.min(1, height / 70) : Math.min(1, height / 300, width / 800))
    state.deviceLayer.classList.toggle(tv ? 'tv-compact' : 'smart-device-compact', count === 1)
    updateDeviceMotion(state)
  }
  const intersection = new IntersectionObserver(entries => {
    for (const entry of entries) {
      const state = surfaces.get(entry.target)
      if (state) { state.visible = entry.isIntersecting; state.dirty = true; if (state.deviceLayer) updateDeviceMotion(state) }
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
    const background = state.deviceLayer || state.canvas
    background.style.height = '0px'
    state.height = Math.max(state.element.clientHeight || state.viewportHeight, state.element.scrollHeight)
    background.style.height = `${state.height}px`
    if (state.deviceLayer) layoutDevice(state)
    else if (!state.globe) {
      state.columns = Math.ceil(state.width / 40)
      const count = state.columns * Math.ceil(state.height / 40)
      state.cells.length = Math.min(state.cells.length, count)
      while (state.cells.length < count) state.cells.push({ logo: Math.floor(Math.random() * 3), duration: 2800 + Math.random() * 3100, phase: Math.random() })
    }
    state.dirty = true
  }
  function showError(state) {
    if (state.error) return
    state.error = document.createElement('p')
    state.error.className = 'operator-load-error'
    state.error.setAttribute('role', 'alert')
    state.error.textContent = loadError.message
    state.element.append(state.error)
  }
  function reconcile() {
    const eligible = new Set([...root.querySelectorAll(selector)].filter(element => !element.parentElement.closest('[data-product-surface="layout"]')))
    for (const [element, state] of surfaces) {
      if (eligible.has(element) && state.line === element.dataset.productLine) continue
      resize.unobserve(element)
      for (const content of state.content || []) resize.unobserve(content)
      intersection.unobserve(element)
      ;(state.deviceLayer || state.canvas).remove()
      state.error?.remove()
      element.classList.remove('operator-matrix-surface', 'smart-device-surface', 'tv-surface', 'global-globe-surface')
      surfaces.delete(element)
    }
    for (const element of eligible) {
      if (surfaces.has(element)) {
        const state = surfaces.get(element)
        observeContent(state)
        continue
      }
      if (['Smart Device', 'TV'].includes(element.dataset.productLine)) {
        const deviceLayer = document.createElement('div')
        deviceLayer.className = element.dataset.productLine === 'TV' ? 'tv-art' : 'smart-device-art'
        deviceLayer.setAttribute('aria-hidden', 'true')
        const state = { element, line: element.dataset.productLine, deviceLayer, cells: [], visible: true, dirty: true }
        element.classList.add(element.dataset.productLine === 'TV' ? 'tv-surface' : 'smart-device-surface')
        element.prepend(deviceLayer)
        surfaces.set(element, state)
        resize.observe(element)
        observeContent(state)
        intersection.observe(element)
        updateDeviceMotion(state)
        continue
      }
      const canvas = document.createElement('canvas')
      canvas.className = element.dataset.productLine === 'Global Operator & STB' ? 'global-globe' : 'operator-matrix'
      canvas.setAttribute('aria-hidden', 'true')
      const state = { element, line: element.dataset.productLine, canvas, context: canvas.getContext('2d'), cells: [], visible: true, dirty: true, width: 0, height: 0 }
      if (state.line === 'Global Operator & STB') state.globe = createGlobalGlobe(state.context, { framing: element.dataset.globeFraming })
      element.classList.add(state.globe ? 'global-globe-surface' : 'operator-matrix-surface')
      element.prepend(canvas)
      surfaces.set(element, state)
      resize.observe(element)
      observeContent(state)
      intersection.observe(element)
      if (loadError && !state.globe) showError(state)
    }
    changed()
  }
  function observeContent(state) {
    const content = new Set([...state.element.children].filter(child => child !== (state.deviceLayer || state.canvas)))
    for (const child of [...content]) for (const canvas of child.querySelectorAll('canvas')) content.add(canvas)
    for (const child of state.content || []) if (!content.has(child)) resize.unobserve(child)
    for (const child of content) if (!state.content?.has(child)) resize.observe(child)
    state.content = content
    if (state.width > 0) layoutSurface(state)
  }
  const mutations = new MutationObserver(records => {
    if (records.some(record => record.type === 'attributes' && record.attributeName !== 'class' || record.type === 'childList' && surfaces.has(record.target) || [...record.addedNodes, ...record.removedNodes].some(node => node.nodeType === 1 && (node.matches(selector) || node.querySelector(selector))))) reconcile()
    if (records.some(record => record.type === 'attributes' && [document.documentElement, document.body].includes(record.target))) changed()
  })
  reconcile()
  mutations.observe(document.documentElement, { childList: true, subtree: true, attributes: true, attributeFilter: ['data-product-line', 'data-product-surface', 'class', 'disabled'] })
  document.addEventListener('visibilitychange', changed)
  window.addEventListener('resize', changed)
  media.addEventListener('change', changed)
  // A scoped login globe does not need operator logo decoding or sprite caches.
  if (root === document || root.querySelector('[data-product-line="China Operator"]')) Promise.all([mobile, telecom, unicom].map(async url => {
    const image = new Image()
    image.src = url
    try { await image.decode() } catch { throw new Error(`China Operator artwork could not load: ${url}`) }
    return image
  })).then(loaded => { if (!stopped) { images = loaded; changed() } }).catch(error => {
    if (stopped) return
    loadError = error
    for (const state of surfaces.values()) if (state.line === 'China Operator') showError(state)
  })
  return () => {
    stopped = true
    if (frameId) cancelAnimationFrame(frameId)
    mutations.disconnect()
    resize.disconnect()
    intersection.disconnect()
    document.removeEventListener('visibilitychange', changed)
    window.removeEventListener('resize', changed)
    media.removeEventListener('change', changed)
    for (const [element, state] of surfaces) { (state.deviceLayer || state.canvas).remove(); state.error?.remove(); element.classList.remove('operator-matrix-surface', 'smart-device-surface', 'tv-surface', 'global-globe-surface') }
  }
}
