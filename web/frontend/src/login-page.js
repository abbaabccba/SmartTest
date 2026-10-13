export function safeReturnPath(value) {
  const path = `${value || ''}`
  return path.startsWith('/') && !path.startsWith('//') ? path : '/'
}

export function createLoginPage({ root = document, api, preferences, navigate = path => window.location.assign(path) }) {
  const form = root.querySelector('[data-login-form]')
  const status = form.querySelector('[data-auth-status]')
  const destination = safeReturnPath(new URLSearchParams(window.location.search).get('next'))
  let selectedTheme
  for (const button of root.querySelectorAll('.login-theme-toggle .theme-btn')) {
    button.addEventListener('click', () => {
      selectedTheme = button.classList.contains('theme-btn-dark') ? 'dark' : 'light'
    })
  }

  form.addEventListener('submit', async event => {
    event.preventDefault()
    const submit = form.querySelector('[type="submit"]')
    status.textContent = 'Signing in…'; submit.disabled = true
    let authenticated = false
    try {
      await api.login(form.elements.username.value, form.elements.password.value)
      authenticated = true
      form.elements.password.value = ''
      if (selectedTheme) {
        await preferences.put('global', { theme: selectedTheme })
        globalThis.SmartTestTheme.apply(selectedTheme)
      }
      navigate(destination)
    } catch {
      form.elements.password.value = ''
      status.textContent = authenticated
        ? 'Theme settings have not synced. Check your connection and try again.'
        : 'Sign in failed. Check your account or connection.'
    } finally {
      submit.disabled = false
    }
  })

  return {
    async start() {
      try {
        const session = await api.session()
        if (session.authenticated) navigate(destination)
      } catch {
        // The form remains usable and reports any subsequent login failure.
      }
    }
  }
}
