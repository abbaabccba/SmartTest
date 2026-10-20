import { icon } from './icons.js'
import { WEB_BRAND_NAME } from './brand.js'

export const navigation = [
  { pageKey: 'dashboard', title: 'Dashboard', url: '/' },
  { pageKey: 'projects', title: 'Projects', url: '/projects.html' },
  { pageKey: 'test-management', title: 'Test Management', url: '/test-management.html' },
  { pageKey: 'jira', title: 'Jira', url: '/jira.html' },
  { pageKey: 'tools', title: 'Tools', url: '/tools.html', children: [{ pageKey: 'audit-email', title: '定期审查', url: '/audit-email.html' }] },
  { pageKey: 'wifi', title: 'Wi-Fi Data', url: '/wifi-database/peak-throughput' },
]

const pageOwners = { dashboard: 'dashboard', projects: 'projects', 'test-management': 'test-management', jira: 'jira', tools: 'tools', settings: 'settings', wifi: 'wifi', 'audit-email': 'tools', inbox: null, analytics: null }
const ownerKey = pageKey => pageOwners[pageKey]
export const themeToggle = () => `<label class="theme-toggle" data-preference-region data-preference-scope="global"><input class="input__check" type="checkbox" role="switch" aria-label="Dark theme" data-preference-key="theme" data-preference-value="dark" ${document.documentElement.classList.contains('dark-theme') ? 'checked' : ''}><span class="slider" aria-hidden="true">${icon('sun', { class: 'st-icon theme-sun' })}${icon('moon', { class: 'st-icon theme-moon' })}</span></label>`

function links(active, mobile = false) {
  return navigation.map(item => {
    const link = `<a href="${item.url}" data-page-key="${item.pageKey}" class="${mobile ? '' : 'nav-link '}${item.pageKey === active ? 'active' : ''}">${item.title}</a>`
    const children = item.children?.length ? item.children.map(child => `<a href="${child.url}" data-page-key="${child.pageKey}"${mobile ? ' class="mobile-nav-child"' : ''}>${child.title}</a>`).join('') : ''
    if (mobile) return link + children
    return `<div class="nav-item">${link}<div class="nav-submenu">${children}</div></div>`
  }).join('')
}

export function createAppShell({ pageKey, root = document.querySelector('[data-app-shell]') }) {
  if (!root) throw new Error('app_shell_host_missing')
  if (!(pageKey in pageOwners)) throw new Error(`app_shell_page_unknown:${pageKey}`)
  const active = ownerKey(pageKey)
  const wifi = active === 'wifi'
  root.innerHTML = `<div class="mobile-menu-overlay"></div><div class="mobile-menu"><div class="mobile-menu-header"><a href="/" class="logo"><span class="logo-label">${WEB_BRAND_NAME}</span></a><button class="mobile-menu-close icon-button" type="button" onclick="closeMobileMenu()" aria-label="Close menu">${icon('close')}</button></div><nav class="mobile-menu-nav">${links(active, true)}</nav><div class="mobile-menu-footer"><div data-user-mobile></div>${themeToggle()}</div></div><div class="app-container"><div class="nav-backdrop" aria-hidden="true"></div><nav class="top-nav"><div class="nav-container"><div class="nav-left"><a href="/" class="logo"><span class="logo-label">${WEB_BRAND_NAME}</span></a><div class="nav-menu">${links(active)}</div></div><div class="nav-right">${themeToggle()}<div class="user-entry"></div><button class="mobile-menu-btn" type="button" onclick="toggleMobileMenu()" aria-label="Open menu">${icon('menu')}</button></div></div></nav><nav class="database-nav" aria-label="Wi-Fi Data" ${wifi ? '' : 'hidden'}><a href="/wifi-database/peak-throughput">Peak Throughput</a><a href="/wifi-database/rvr">RVR</a><a href="/wifi-database/rvo">RVO</a></nav><main class="main-content"></main><footer class="footer"><p>© 2026 ${WEB_BRAND_NAME}</p></footer></div>`
  if (wifi) {
    for (const link of root.querySelectorAll('.database-nav a')) link.classList.toggle('active', link.pathname === window.location.pathname)
  }
  return {
    contentRoot: root.querySelector('main.main-content'),
    desktopHost: root.querySelector('.user-entry'),
    mobileHost: root.querySelector('[data-user-mobile]'),
  }
}
