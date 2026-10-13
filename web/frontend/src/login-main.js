import { createAuthApi } from './api.js'
import { applyLoginBackground, disposeLoginBackground } from './login-background.js'
import { createLoginPage } from './login-page.js'

applyLoginBackground()
window.addEventListener('pagehide', () => disposeLoginBackground(document.querySelector('[data-login-board]')))
window.addEventListener('pageshow', event => { if (event.persisted) applyLoginBackground() })
createLoginPage({ root: document, api: createAuthApi() }).start()
