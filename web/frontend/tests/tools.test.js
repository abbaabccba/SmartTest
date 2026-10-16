// @vitest-environment jsdom
import { beforeEach, describe, expect, it } from 'vitest'
import { createTools } from '../src/tools.js'

describe('Tools page', () => {
  beforeEach(() => { document.body.innerHTML = '<main></main>' })

  it('leaves scheduled review navigation to the shared shell', async () => {
    const page = createTools({ root: document.querySelector('main') })
    await page.start()

    expect(document.querySelector('[data-jira-filter]')).toBeNull()
    expect(document.querySelector('[data-jira-review]')).toBeNull()
    expect(document.querySelector('[data-confluence-review]')).toBeNull()
    expect(document.querySelectorAll('.settings-section')).toHaveLength(0)
    expect(document.querySelector('h1')?.textContent).toBe('Tools')
    expect(document.querySelector('a[href="/audit-email.html"]')).toBeNull()
  })
})
