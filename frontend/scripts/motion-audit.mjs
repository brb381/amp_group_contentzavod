import { chromium } from 'playwright-core'

const email = process.env.AMP_E2E_EMAIL
const password = process.env.AMP_E2E_PASSWORD
const mockApi = process.env.AMP_MOCK_API === '1'
if (!mockApi && (!email || !password)) throw new Error('Set AMP_E2E_EMAIL and AMP_E2E_PASSWORD')

const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  headless: true,
})

try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
  const browserErrors = []
  page.on('pageerror', (error) => browserErrors.push(error.message))

  if (mockApi) {
    await page.route('**/api/v1/**', async (route) => {
      const path = new URL(route.request().url()).pathname
      const body = path.endsWith('/auth/me')
        ? { id: 'motion-audit', email: 'motion-audit@example.test', role: 'admin', status: 'active', email_verified_at: '2026-01-01T00:00:00Z' }
        : { items: [], page: 1, page_size: 20, total_items: 0, total_pages: 1 }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    })
  }

  await page.goto(process.env.AMP_WEB_URL || 'http://127.0.0.1:5173/', { waitUntil: 'domcontentloaded' })
  if (!mockApi) {
    await page.locator('input[type="email"]').waitFor({ timeout: 15_000 })
    await page.locator('input[type="email"]').fill(email)
    await page.locator('input[type="password"]').fill(password)
    await page.locator('.auth-submit').click()
  }
  await page.locator('.workspace').waitFor({ timeout: 15_000 })

  const navigation = page.locator('.nav__item')
  const results = []
  for (let index = 1; index < Math.min(await navigation.count(), 6); index += 1) {
    const label = (await navigation.nth(index).innerText()).trim()
    const movement = await page.evaluate(async (targetIndex) => {
      const target = document.querySelectorAll('.nav__item')[targetIndex]
      const samples = []
      const disruptiveAnimations = new Set()
      const takeSample = () => {
        const sidebar = document.querySelector('.sidebar').getBoundingClientRect()
        const topbar = document.querySelector('.topbar').getBoundingClientRect()
        const main = document.querySelector('main').getBoundingClientRect()
        samples.push({
          sidebarX: sidebar.x,
          sidebarWidth: sidebar.width,
          topbarX: topbar.x,
          topbarWidth: topbar.width,
          mainX: main.x,
          mainY: main.y,
          mainWidth: main.width,
          viewportWidth: document.documentElement.clientWidth,
        })
        document.getAnimations().forEach((animation) => {
          const name = animation.animationName
          if (['page-enter', 'content-settle', 'view-old', 'view-new'].includes(name)) disruptiveAnimations.add(name)
        })
      }
      const spread = (key) => Math.max(...samples.map((sample) => sample[key])) - Math.min(...samples.map((sample) => sample[key]))

      takeSample()
      target.click()
      const startedAt = performance.now()
      while (performance.now() - startedAt < 350) {
        await new Promise(requestAnimationFrame)
        takeSample()
      }

      return {
        sidebar: Math.max(spread('sidebarX'), spread('sidebarWidth')),
        topbar: Math.max(spread('topbarX'), spread('topbarWidth')),
        main: Math.max(spread('mainX'), spread('mainY'), spread('mainWidth')),
        viewport: spread('viewportWidth'),
        disruptiveAnimations: [...disruptiveAnimations],
      }
    }, index)
    results.push({ label, ...movement })
  }

  console.log(JSON.stringify(results, null, 2))
  const unstable = results.filter((result) => (
    Math.max(result.sidebar, result.topbar, result.main, result.viewport) > 0.5
    || result.disruptiveAnimations.length > 0
  ))
  if (browserErrors.length || unstable.length) {
    throw new Error([...browserErrors, ...unstable.map((result) => `${result.label}: layout moved ${JSON.stringify(result)}`)].join('\n'))
  }
  console.log('Navigation motion audit passed')
} finally {
  await browser.close()
}
