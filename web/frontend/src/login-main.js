import { applyLoginBackground, disposeLoginBackground } from './login-background.js'
import { createAuthApi, createPreferenceApi } from './api.js'
import { icon } from './icons.js'
import { createLoginPage } from './login-page.js'

applyLoginBackground()
window.addEventListener('pagehide', () => disposeLoginBackground(document.querySelector('[data-login-board]')))
window.addEventListener('pageshow', event => { if (event.persisted) applyLoginBackground() })

for (const element of document.querySelectorAll('[data-icon]')) element.innerHTML = icon(element.dataset.icon)
createLoginPage({ root: document, api: createAuthApi(), preferences: createPreferenceApi() }).start()
