import { chromium } from 'playwright-core'

const baseUrl = process.env.AMP_WEB_URL || 'http://127.0.0.1:5173/'
const password = process.env.AMP_E2E_PASSWORD
if (!password) {
  throw new Error('AMP_E2E_PASSWORD is required')
}
const chrome = process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'
const allRoles = ['blogger', 'moderator', 'manager', 'finance', 'analyst', 'admin']
const selectedRoles = (process.env.AMP_AUDIT_ROLES || allRoles.join(','))
  .split(',').map((value) => value.trim()).filter(Boolean)

const pages = {
  blogger: [
    ['Обзор', 'Рабочий стол.'], ['Каталог', 'Каталог'], ['Мои ролики', 'Мои ролики'],
    ['Показания', 'Показания'], ['Выплаты', 'Выплаты'], ['Обращения', 'Обращения'],
  ],
  moderator: [
    ['Обзор', 'Рабочий стол'], ['Очереди', 'Очереди'], ['Блогеры', 'Блогеры'],
    ['Каталог', 'Каталог'], ['Публикации', 'Публикации'], ['Показания', 'Показания'],
    ['Расчёты', 'Расчёты'], ['Выгрузки', 'Выгрузки'], ['Обращения', 'Обращения'],
  ],
  manager: [
    ['Обзор', 'Рабочий стол'], ['Каталог', 'Каталог'], ['Публикации', 'Публикации'],
    ['Показания', 'Показания'], ['Расчёты', 'Расчёты'], ['Выплаты', 'Выплаты'],
    ['Выгрузки', 'Выгрузки'], ['Обращения', 'Обращения'],
  ],
  finance: [['Расчёты', 'Расчёты'], ['Выплаты', 'Выплаты'], ['Выгрузки', 'Выгрузки']],
  analyst: [['Аналитика', 'Аналитика'], ['Выгрузки', 'Выгрузки']],
  admin: [
    ['Обзор', 'Рабочий стол'], ['Очереди', 'Очереди'], ['Блогеры', 'Блогеры'],
    ['Каталог', 'Каталог'], ['Публикации', 'Публикации'], ['Показания', 'Показания'],
    ['Расчёты', 'Расчёты'], ['Выплаты', 'Выплаты'], ['Выгрузки', 'Выгрузки'],
    ['Обращения', 'Обращения'], ['Аналитика', 'Аналитика'],
    ['Безопасность', 'Безопасность'], ['Документы', 'Документы'],
  ],
}

const forbiddenCopy = [
  'unusual_growth', 'preliminary', 'retry_wait', 'missing_current_reading',
  'baseline_only', 'approximate_public_counter', '[object Object]', 'undefined',
]

async function auditRole(browser, role, viewport) {
  const context = await browser.newContext({ viewport })
  const page = await context.newPage()
  const failures = []
  const serverErrors = []
  const clientErrors = []
  const browserErrors = []
  let authenticated = false

  page.on('pageerror', (error) => browserErrors.push(`pageerror: ${error.message}`))
  page.on('console', (message) => {
    if (message.type() === 'error' && !message.text().includes('Failed to load resource')) {
      browserErrors.push(`console: ${message.text()}`)
    }
  })
  page.on('response', (response) => {
    if (!response.url().includes('/api/')) return
    const entry = `${response.status()} ${response.request().method()} ${response.url()}`
    if (response.status() >= 500) serverErrors.push(entry)
    else if (authenticated && response.status() >= 400) clientErrors.push(entry)
  })

  const mobile = viewport.width <= 820
  const openNav = async () => {
    if (mobile) await page.locator('.menu-button').click()
  }
  const waitForData = async () => {
    await page.waitForTimeout(250)
    await page.waitForFunction(() => {
      const state = document.querySelector('.request-state')
      return !state || state.classList.contains('request-state--error')
    }, null, { timeout: 10_000 })
  }
  const inspectPage = async (label) => {
    const requestError = page.locator('.request-state--error')
    if (await requestError.count()) failures.push(`${label}: ${await requestError.innerText()}`)
    const text = await page.locator('main').innerText()
    const exposed = forbiddenCopy.find((value) => text.toLowerCase().includes(value.toLowerCase()))
    if (exposed) failures.push(`${label}: technical copy "${exposed}"`)
    const overflow = await page.evaluate(() => ({
      document: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      main: document.querySelector('main')
        ? document.querySelector('main').scrollWidth - document.querySelector('main').clientWidth
        : 0,
      offenders: (() => {
        const main = document.querySelector('main')
        if (!main) return []
        const boundary = main.getBoundingClientRect().right
        return [...main.querySelectorAll('*')]
          .filter((element) => {
            const rect = element.getBoundingClientRect()
            return rect.width > 0 && rect.height > 0 && rect.right > boundary + 2
          })
          .slice(0, 8)
          .map((element) => `${element.tagName.toLowerCase()}.${element.className || '-'}`)
      })(),
    }))
    if (overflow.document > 2) failures.push(`${label}: document overflow ${overflow.document}px`)
    if (overflow.main > 2) {
      failures.push(`${label}: main overflow ${overflow.main}px (${overflow.offenders.join(', ')})`)
    }
    const brokenImages = await page.locator('main img:not([hidden])').evaluateAll((images) => images
      .filter((image) => image.complete && image.naturalWidth === 0)
      .map((image) => image.currentSrc || image.src))
    if (brokenImages.length) failures.push(`${label}: broken images ${brokenImages.join(', ')}`)
  }
  const closeSurface = async () => {
    const close = page.locator('button[title="Закрыть"]:visible, button[title="Назад"]:visible').last()
    if (await close.count()) {
      await close.click()
      await page.waitForTimeout(150)
    }
  }
  const inspectFirstCard = async (label) => {
    const row = page.locator('main tbody tr').first()
    if (!await row.count()) return
    await row.click()
    await page.waitForTimeout(350)
    const loading = page.locator('.drawer-loading')
    if (await loading.count()) await loading.waitFor({ state: 'hidden', timeout: 10_000 })
    const surfaceError = page.locator('.auth-error:visible, .request-state--error:visible')
    if (await surfaceError.count()) failures.push(`${label} card: ${await surfaceError.first().innerText()}`)
    await closeSurface()
  }

  try {
    await page.goto(baseUrl, { waitUntil: 'networkidle' })
    await page.locator('input[type="email"]').fill(`demo-${role}@ampgroup.pro`)
    await page.locator('input[type="password"]').fill(password)
    await page.getByRole('button', { name: /Войти/ }).click()
    await page.locator('.workspace').waitFor({ timeout: 10_000 })
    authenticated = true

    for (const [navLabel, heading] of pages[role]) {
      await openNav()
      await page.getByRole('button', { name: navLabel, exact: true }).click()
      await page.waitForFunction((expected) => (
        document.querySelector('main h1')?.textContent?.trim() === expected
      ), heading, { timeout: 10_000 })
      const pageHeading = page.locator('main h1').first()
      const actualHeading = (await pageHeading.innerText()).trim()
      if (actualHeading !== heading) {
        failures.push(`${role}/${navLabel}: expected heading "${heading}", got "${actualHeading}" at ${page.url()}`)
      }
      await waitForData()
      const label = `${role}/${navLabel}/${viewport.width}`
      await inspectPage(label)
      if (role === 'admin' && navLabel === 'Аналитика') {
        const productLink = page.locator('.analytics-product-ranking button').first()
        if (await productLink.count()) {
          const productName = (await productLink.locator('span strong').innerText()).trim()
          await productLink.click()
          await page.getByRole('heading', { name: 'Публикации', exact: true }).waitFor()
          await waitForData()
          const productFilter = page.locator('input[name="product"]')
          if ((await productFilter.inputValue()).trim() !== productName) {
            failures.push(`${label}: product drill-down did not preserve the selected product`)
          }
          await inspectPage(`${label}/product-drill-down`)
        }
      }
      if (!['Обзор', 'Аналитика'].includes(navLabel)) await inspectFirstCard(label)
    }

    await openNav()
    await page.getByRole('button', { name: 'Личный кабинет', exact: true }).click()
    await page.getByRole('heading', { name: 'Личный кабинет', exact: true }).waitFor()
    const loading = page.locator('.drawer-loading')
    if (await loading.count()) await loading.waitFor({ state: 'hidden', timeout: 10_000 })
    const tabs = page.locator('.account-nav button:not(.account-signout)')
    for (let index = 0; index < await tabs.count(); index += 1) {
      await tabs.nth(index).click()
      await page.waitForTimeout(150)
      const error = page.locator('.account-content .auth-error:visible')
      if (await error.count()) failures.push(`${role}/cabinet: ${await error.first().innerText()}`)
    }
    await closeSurface()

    if (serverErrors.length) failures.push(...[...new Set(serverErrors)])
    if (clientErrors.length) failures.push(...[...new Set(clientErrors)])
    if (browserErrors.length) failures.push(...[...new Set(browserErrors)])
    if (failures.length) throw new Error(failures.join('\n'))
    console.log(`PASS ${role} ${viewport.width}x${viewport.height}`)
  } finally {
    await context.close()
  }
}

const browser = await chromium.launch({ executablePath: chrome, headless: true })
try {
  for (const role of selectedRoles) await auditRole(browser, role, { width: 1440, height: 1000 })
  for (const role of selectedRoles.filter((value) => ['blogger', 'admin'].includes(value))) {
    await auditRole(browser, role, { width: 390, height: 844 })
  }
  console.log('Production UI audit passed')
} finally {
  await browser.close()
}
