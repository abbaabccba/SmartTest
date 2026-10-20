export function createAuthShell({ root = document, desktopHost, mobileHost, api,
  onChanging = () => {}, onSession = () => {}, navigate = path => window.location.assign(path) }) {
  let state = { authenticated: false }
  let generation = 0
  let confirmed = false
  const signIn = () => navigate(`/login.html?next=${encodeURIComponent(`${window.location.pathname}${window.location.search}${window.location.hash}`)}`)
  const userNode = () => {
    const label = `${state.displayName || state.username || ''}`
    const menu = document.createElement('div'); menu.className = 'user-menu'
    const trigger = document.createElement('button'); trigger.className = 'user-trigger'; trigger.dataset.userTrigger = ''; trigger.type = 'button'
    const avatar = document.createElement('span'); avatar.className = 'user-avatar'; avatar.dataset.userAvatar = ''
    if (state.avatarUrl) {
      const image = document.createElement('img'); image.setAttribute('src', `${state.avatarUrl}`); image.alt = ''; avatar.append(image)
    } else {
      avatar.textContent = (label.trim()[0] || '?').toUpperCase()
    }
    const name = document.createElement('span'); name.dataset.userName = ''; name.textContent = label
    const dropdown = document.createElement('div'); dropdown.className = 'user-dropdown'; dropdown.dataset.userDropdown = ''; dropdown.hidden = true
    const settings = document.createElement('a'); settings.href = '/settings.html'; settings.className = 'menu-item'; settings.textContent = 'Settings'
    const logout = document.createElement('button'); logout.className = 'user-logout menu-item'; logout.dataset.logout = ''; logout.type = 'button'; logout.textContent = 'Sign out'
    dropdown.append(settings, logout)
    trigger.append(avatar, name); menu.append(trigger, dropdown)
    return menu
  }
  const render = () => {
    desktopHost.replaceChildren(userNode()); mobileHost.replaceChildren(userNode())
    const greeting = root.querySelector?.('#greeting')
    if (greeting) {
      const base = greeting.dataset.greetingBase || greeting.textContent.trim()
      greeting.dataset.greetingBase = base
      greeting.textContent = state.authenticated && state.username ? `${base}, ${state.username}` : base
    }
  }
  root.addEventListener('click', async event => {
    const trigger = event.target.closest('[data-user-trigger]')
    if (trigger) { const dropdown = trigger.parentElement.querySelector('[data-user-dropdown]'); dropdown.hidden = !dropdown.hidden }
    if (event.target.closest('[data-logout]') && api) { await api.logout(); generation++; signIn() }
  })
  async function start() {
    const current = ++generation
    let next = { authenticated: false }
    if (api) { try { next = await api.session() } catch { /* Unauthenticated shell. */ } }
    if (current !== generation) return
    const unchanged = confirmed && state.authenticated === next.authenticated && state.username === next.username
    state = next
    if (state.authenticated) render()
    confirmed = true
    if (!unchanged) await onSession(state)
    if (!state.authenticated) signIn()
    return state
  }
  function changed(event) {
    if (event.type === 'storage' && event.key !== 'smarttest:identity-change') return
    confirmed = false
    onChanging()
    void start()
  }
  window.addEventListener('storage', changed)
  window.addEventListener('auth:changed', changed)
  return { start, destroy() { generation++; window.removeEventListener('storage', changed); window.removeEventListener('auth:changed', changed) } }
}
