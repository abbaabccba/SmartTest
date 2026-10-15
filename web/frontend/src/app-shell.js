import { WEB_BRAND_NAME } from './brand.js'

export const navigation = [
  { pageKey: 'dashboard', title: 'Dashboard', url: '/' },
  { pageKey: 'projects', title: 'Projects', url: '/projects.html' },
  { pageKey: 'test-management', title: 'Test Management', url: '/test-management.html' },
  { pageKey: 'jira', title: 'Jira', url: '/jira.html' },
  { pageKey: 'tools', title: 'Tools', url: '/tools.html' },
  { pageKey: 'wifi', title: 'Wi-Fi Data', url: '/wifi-database/peak-throughput' },
]

const pageOwners = { dashboard: 'dashboard', projects: 'projects', 'test-management': 'test-management', jira: 'jira', tools: 'tools', settings: 'settings', wifi: 'wifi', 'audit-email': 'tools', inbox: null, analytics: null }
const ownerKey = pageKey => pageOwners[pageKey]
export const themeToggle = () => `<label class="theme-toggle" data-preference-region data-preference-scope="global"><input class="input__check" type="checkbox" role="switch" aria-label="Dark theme" data-preference-key="theme" data-preference-value="dark" ${document.documentElement.classList.contains('dark-theme') ? 'checked' : ''}><span class="slider" aria-hidden="true"><svg class="theme-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"></circle><path d="M12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72 1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"></path></svg><svg class="theme-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"></path></svg></span></label>`

function links(active, mobile = false) {
  return navigation.map(item => `<${mobile ? '' : 'div class="nav-item"><'}a href="${item.url}" data-page-key="${item.pageKey}" class="${mobile ? '' : 'nav-link '}${item.pageKey === active ? 'active' : ''}">${item.title}</a>${mobile ? '' : '</div>'}`).join('')
}

export function createAppShell({ pageKey, root = document.querySelector('[data-app-shell]') }) {
  if (!root) throw new Error('app_shell_host_missing')
  if (!(pageKey in pageOwners)) throw new Error(`app_shell_page_unknown:${pageKey}`)
  const active = ownerKey(pageKey)
  const wifi = active === 'wifi'
  root.innerHTML = `<div class="mobile-menu-overlay"></div><div class="mobile-menu"><div class="mobile-menu-header"><a href="/" class="logo"><span class="logo-label">${WEB_BRAND_NAME}</span></a><button class="mobile-menu-close" type="button" onclick="closeMobileMenu()">×</button></div><nav class="mobile-menu-nav">${links(active, true)}</nav><div class="mobile-menu-footer"><div data-user-mobile></div>${themeToggle()}</div></div><div class="app-container"><nav class="top-nav"><div class="nav-container"><div class="nav-left"><a href="/" class="logo"><span class="logo-label">${WEB_BRAND_NAME}</span></a><div class="nav-menu">${links(active)}</div></div><div class="nav-right">${themeToggle()}<div class="user-entry"></div><button class="mobile-menu-btn" type="button" onclick="toggleMobileMenu()">☰</button></div></div></nav><nav class="database-nav" aria-label="Wi-Fi Data" ${wifi ? '' : 'hidden'}><a href="/wifi-database/peak-throughput">Peak Throughput</a><a href="/wifi-database/rvr">RVR</a><a href="/wifi-database/rvo">RVO</a></nav><main class="main-content"></main><footer class="footer"><p>© 2026 ${WEB_BRAND_NAME}</p></footer></div>`
  if (wifi) {
    for (const link of root.querySelectorAll('.database-nav a')) link.classList.toggle('active', link.pathname === window.location.pathname)
  }
  return {
    contentRoot: root.querySelector('main.main-content'),
    desktopHost: root.querySelector('.user-entry'),
    mobileHost: root.querySelector('[data-user-mobile]'),
  }
}
