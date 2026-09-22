import { chromium } from 'playwright-core'

const email = process.env.AMP_E2E_EMAIL
const password = process.env.AMP_E2E_PASSWORD
if (!email || !password) throw new Error('Set AMP_E2E_EMAIL and AMP_E2E_PASSWORD')

const browser = await chromium.launch({
  executablePath: process.env.CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  headless: true,
})

try {
  const page = await browser.newPage({ viewport: { width: Number(process.env.AMP_E2E_WIDTH || 1365), height: Number(process.env.AMP_E2E_HEIGHT || 900) } })
  await page.goto(process.env.AMP_WEB_URL || 'http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
  await page.locator('input[type="email"]').fill(email)
  await page.locator('input[type="password"]').fill(password)
  await page.getByRole('button', { name: 'Войти' }).click()
  await page.locator('.workspace').waitFor()
  const openNav = async () => { if (Number(process.env.AMP_E2E_WIDTH || 1365) <= 820) await page.locator('.menu-button').click() }

  if (email.includes('blogger')) {
    await page.getByRole('heading', { name: 'Рабочий стол' }).waitFor()
    await page.getByRole('button', { name: 'Профиль' }).click()
    await page.getByRole('heading', { name: 'Профиль блогера' }).waitFor()
    await page.locator('.drawer-loading').waitFor({ state: 'hidden' })
    if (process.env.AMP_E2E_MUTATE) {
      await page.locator('.work-drawer').getByRole('button', { name: 'Сохранить' }).click()
      await page.locator('.toast').waitFor()
    } else await page.getByTitle('Закрыть').click()
    await openNav()
    await page.getByRole('button', { name: 'Мои ролики' }).click()
    await page.getByRole('button', { name: 'Новый ролик' }).click()
    await page.getByRole('heading', { name: 'Новый ролик' }).waitFor()
    await page.getByTitle('Закрыть').click()
    await openNav()
    await page.getByRole('button', { name: 'Показания' }).click()
    await page.getByRole('button', { name: 'Добавить показание' }).click()
    await page.getByRole('heading', { name: 'Добавить показание' }).waitFor()
    await page.locator('.drawer-loading').waitFor({ state: 'hidden' })
    console.log('E2E smoke passed: blogger profile, content and readings workflows')
  } else if (email.includes('moderator')) {
    await openNav()
    await page.getByRole('button', { name: 'Очереди' }).click()
    await page.getByRole('heading', { name: 'Очереди' }).waitFor()
    const firstRow = page.locator('tbody tr').first()
    if (await firstRow.count()) {
      await firstRow.click()
      await page.locator('.work-drawer').waitFor()
    }
    console.log('E2E smoke passed: moderator queue and decision workflow')
  } else {
    await page.getByRole('heading', { name: 'Рабочий стол' }).waitFor()
    await openNav()
    await page.getByRole('button', { name: 'Выгрузки' }).click()
    await page.getByRole('heading', { name: 'Выгрузки' }).waitFor()
    if (await page.locator('.request-state--error').count()) {
      throw new Error('Backend page failed: ' + await page.locator('.request-state--error').textContent())
    }
    console.log('E2E smoke passed: login, session, dashboard and exports')
  }
  if (process.env.AMP_E2E_SCREENSHOT) await page.screenshot({ path: process.env.AMP_E2E_SCREENSHOT, fullPage: true })
} finally {
  await browser.close()
}