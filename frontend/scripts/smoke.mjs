import { chromium } from 'playwright-core'

const email = process.env.AMP_E2E_EMAIL
const password = process.env.AMP_E2E_PASSWORD
if (!email || !password) {
  throw new Error('Set AMP_E2E_EMAIL and AMP_E2E_PASSWORD')
}

const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  headless: true,
})

try {
  const page = await browser.newPage({ viewport: { width: 1365, height: 900 } })
  await page.goto(process.env.AMP_WEB_URL || 'http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
  await page.locator('input[type="email"]').fill(email)
  await page.locator('input[type="password"]').fill(password)
  await page.getByRole('button', { name: 'Войти' }).click()
  await page.getByRole('heading', { name: 'Рабочий стол' }).waitFor()
  await page.getByRole('button', { name: 'Выгрузки' }).click()
  await page.getByRole('heading', { name: 'Выгрузки' }).waitFor()
  const apiState = await page.locator('.empty, tbody tr, .request-state--error').first().textContent()
  if (await page.locator('.request-state--error').count()) {
    throw new Error(`Backend page failed: ${apiState}`)
  }
  console.log('E2E smoke passed: login, session, dashboard, exports')
} finally {
  await browser.close()
}

