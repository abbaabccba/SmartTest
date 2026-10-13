import './login-motion.css'

export const LOGIN_BACKGROUNDS = [
  { id: 'pcb' },
  { id: 'globe' },
]

const mounted = new WeakMap()

export function disposeLoginBackground(surface) {
  mounted.get(surface)?.dispose()
  mounted.delete(surface)
}

const STORAGE_KEY = 'smarttest.login.background'

export function selectLoginBackground({ backgrounds = LOGIN_BACKGROUNDS, storage = sessionStorage, random = Math.random } = {}) {
  if (!backgrounds.length) return ''
  let stored
  try { stored = storage.getItem(STORAGE_KEY) } catch { /* Storage may be unavailable in hardened browsers. */ }
  if (backgrounds.some(background => background.id === stored)) return stored
  const selected = backgrounds[Math.min(backgrounds.length - 1, Math.floor(random() * backgrounds.length))].id
  try { storage.setItem(STORAGE_KEY, selected) } catch { /* The selected background still works for this page load. */ }
  return selected
}

export function applyLoginBackground({ root = document, backgrounds = LOGIN_BACKGROUNDS, storage, random } = {}) {
  const surface = root.querySelector('[data-login-board]')
  if (!surface) return ''
  const id = selectLoginBackground({ backgrounds, storage: storage ?? sessionStorage, random })
  const control = root.querySelector('[data-login-background-switch]')
  if (control) {
    control.value = id
    const onChange = () => {
      const selected = backgrounds.filter(background => background.id === control.value)
      applyLoginBackground({ root, backgrounds: selected, storage, random })
      // Restore the complete choices for the next user selection.
      control.onchange = onChange
    }
    control.onchange = onChange
  }
  if (mounted.get(surface)?.id === id) return id
  disposeLoginBackground(surface)
  surface.dataset.loginBackground = id
  const dispose = id === 'pcb' ? mountLoginMotion(surface) : id === 'globe' ? mountLoginGlobe(surface) : () => {}
  mounted.set(surface, { id, dispose })
  return id
}

function mountLoginGlobe(surface) {
  const layer = document.createElement('div')
  layer.className = 'login-globe'
  layer.dataset.productLine = 'Global Operator & STB'
  layer.dataset.productSurface = 'background'
  layer.dataset.globeFraming = 'login'
  layer.setAttribute('aria-hidden', 'true')
  surface.prepend(layer)
  let disposed = false
  let stop
  // Keep geography data and renderer out of the PCB's initial module graph.
  import('./product-line-motion.js').then(({ initializeProductLineMotion }) => {
    if (!disposed) stop = initializeProductLineMotion({ root: surface })
  }).catch(error => {
    if (disposed) return
    layer.removeAttribute('aria-hidden')
    layer.setAttribute('role', 'alert')
    layer.textContent = `Globe could not load: ${error.message}`
  })
  return () => { disposed = true; stop?.(); layer.remove() }
}

const PCB_MARKUP = `<svg class="pcb-motion" viewBox="0 0 1120 942" aria-hidden="true">
      <image class="pcb-light" href="/images/login-pcb-white-cutout.png" width="1120" height="941"/>
      <image class="pcb-dark" href="/images/login-pcb-dark-cutout.png" width="1120" height="942"/>
      <!-- Coordinates follow the shared artwork's cyan traces, ending at chip pins. -->
      <g class="trace-motion" fill="none" stroke-linecap="round" stroke-linejoin="round">
        <path d="M 111 205 L 251 248 L 296 336 L 352 353" pathLength="100"/>
        <path d="M 392 199 L 383 232 L 424 278 L 417 293" pathLength="100"/>
        <path d="M 645 280 L 638 301 L 570 336 L 563 350" pathLength="100"/>
        <path d="M 901 375 L 726 316 L 672 344 L 627 329 L 603 343" data-light-path="M 901 375 L 726 316 L 672 344 L 627 329 L 603 343" data-dark-path="M 906 379 L 733 321 L 682 348 L 631 329 L 602 345" pathLength="100"/>
        <path d="M 18 477 L 124 511 L 260 432 L 327 454" pathLength="100"/>
        <path d="M 115 634 L 138 562 L 268 484 L 314 499" pathLength="100"/>
        <path d="M 521 736 L 535 685 L 482 616 L 491 585" pathLength="100"/>
        <path d="M 593 649 L 569 607 L 597 537 L 581 508" data-light-path="M 593 649 L 569 607 L 597 537 L 581 508" data-dark-path="M 594 642 L 576 610 L 593 550 L 604 537 L 596 530 L 587 520" pathLength="100"/>
      </g>
      <path class="chip-breath" d="M 389 286 L 609 354 L 539 583 L 313 515 Z"/>
      <path class="chip-trigger" d="M 389 286 L 609 354 L 539 583 L 313 515 Z"/>
    </svg>`

export function mountLoginMotion(surface) {
  if (surface.querySelector('.pcb-motion')) return
  surface.insertAdjacentHTML('afterbegin', PCB_MARKUP)
  surface.classList.add('login-motion')
  const themePaths = surface.querySelectorAll('[data-light-path]')
  const syncPaths = () => {
    const key = document.documentElement.classList.contains('dark-theme') ? 'darkPath' : 'lightPath'
    for (const path of themePaths) path.setAttribute('d', path.dataset[key])
  }
  window.addEventListener('theme:changed', syncPaths)
  syncPaths()
  const syncVisibility = () => surface.classList.toggle('motion-hidden', document.hidden)
  document.addEventListener('visibilitychange', syncVisibility)
  syncVisibility()
  return () => {
    window.removeEventListener('theme:changed', syncPaths)
    document.removeEventListener('visibilitychange', syncVisibility)
    surface.querySelector('.pcb-motion')?.remove()
    surface.classList.remove('login-motion', 'motion-hidden')
  }
}
