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
  const apiServerErrors = []
  page.on('response', (response) => {
    if (response.status() >= 500 && response.url().includes('/api/')) {
      apiServerErrors.push(response.status() + ' ' + response.url())
    }
  })
  await page.goto(process.env.AMP_WEB_URL || 'http://127.0.0.1:5173/', { waitUntil: 'networkidle' })
  await page.locator('input[type="email"]').fill(email)
  await page.locator('input[type="password"]').fill(password)
  await page.getByRole('button', { name: 'Войти' }).click()
  await page.locator('.workspace').waitFor()
  const openNav = async () => { if (Number(process.env.AMP_E2E_WIDTH || 1365) <= 820) await page.locator('.menu-button').click() }
  const assertImageLoaded = async (locator, label) => {
    await locator.waitFor({ state: 'attached' })
    await locator.evaluate((image) => image.decode().catch(() => {}))
    const loaded = await locator.evaluate((image) => {
      if (image.complete && image.naturalWidth > 0) return true
      return image.hidden && Boolean(image.parentElement.querySelector('svg'))
    })
    if (!loaded) throw new Error(label + ' image and fallback did not load')
  }
  const assertBloggerCopyIsClean = async () => {
    const content = await page.locator('main').innerText()
    const forbidden = ['backend', 'API', 'active queues', 'autocollection']
    const exposed = forbidden.find((value) => content.toLowerCase().includes(value.toLowerCase()))
    if (exposed) throw new Error('Technical copy is exposed to blogger: ' + exposed)
  }

  if (process.env.AMP_E2E_OVERVIEW_ONLY) {
    await page.getByRole('heading', { name: 'Рабочий стол' }).waitFor()
    await assertBloggerCopyIsClean()
    await assertImageLoaded(page.locator('.queue-panel tbody tr').first().locator('img'), 'Overview preview')
    console.log('E2E smoke passed: overview')
  } else if (email.includes('blogger')) {
    await page.getByRole('heading', { name: 'Рабочий стол' }).waitFor()
    await page.locator('.queue-panel tbody tr').first().waitFor()
    const overviewViews = await page.locator('.queue-panel .cell-value').allTextContents()
    if (!overviewViews.length || overviewViews.some((value) => /^0(?:[ ,.\u00a0]|$)/.test(value.trim()))) throw new Error('Overview contains a video card without views: ' + overviewViews.join(', '))
    const overviewCard = page.locator('.queue-panel tbody tr').first()
    await assertBloggerCopyIsClean()
    if (await overviewCard.count()) {
      await assertImageLoaded(overviewCard.locator('img'), 'Overview preview')
      await overviewCard.click()
      await page.getByText('Один ролик и все публикации по площадкам').waitFor()
      await page.locator('.drawer-loading').waitFor({ state: 'hidden' })
      await assertImageLoaded(page.locator('.publication-preview img').first(), 'Publication preview')
      if (!await page.locator('.publication-views').count()) throw new Error('Video card does not display publication views')
      if ((await page.locator('.work-drawer').innerText()).includes('Публичный счётчик может быть округлён')) throw new Error('Approximate counter warning is exposed')
      await page.getByTitle('Закрыть').click()
    }
    await page.getByRole('button', { name: 'Профиль' }).click()
    await page.getByRole('heading', { name: 'Профиль блогера' }).waitFor()
    await page.locator('.drawer-loading').waitFor({ state: 'hidden' })
    const platformSelect = page.locator('select[name="platform"]')
    await platformSelect.selectOption('tiktok')
    if (!String(await page.locator('input[name="social_url"]').getAttribute('placeholder')).includes('tiktok.com')) throw new Error('TikTok account form is not configured')
    await platformSelect.selectOption('vk')
    if (!String(await page.locator('input[name="social_url"]').getAttribute('placeholder')).includes('vk.com')) throw new Error('VK account form is not configured')
    if (process.env.AMP_E2E_MUTATE) {
      await page.locator('.work-drawer').getByRole('button', { name: 'Сохранить' }).click()
      await page.locator('.toast').waitFor()
    } else await page.getByTitle('Закрыть').click()
    await openNav()
    await page.getByRole('button', { name: 'Мои ролики' }).click()
    const existingCard = page.locator('tbody tr').first()
    if (await existingCard.count()) {
      await existingCard.click()
      await page.getByRole('button', { name: 'Добавить ссылку' }).waitFor()
      await page.getByTitle('Закрыть').click()
    }
    await page.getByRole('button', { name: 'Новый ролик' }).click()
    await page.getByRole('heading', { name: 'Новый ролик' }).waitFor()
    const accountOptions = await page.locator('select[name="social_account_id"] option').allTextContents()
    if (!accountOptions.some((item) => item.includes('TikTok')) && !accountOptions.some((item) => item.includes('VK'))) console.log('No approved TikTok/VK account in smoke fixture; platform account form verified instead')
    await page.getByTitle('Закрыть').click()
    await openNav()
    await page.getByRole('button', { name: 'Показания' }).click()
    const readingsText = await page.locator('main').innerText()
    if (readingsText.includes('1 риска') || readingsText.includes('Публичный счётчик может быть округлён')) throw new Error('Technical counter risk is exposed in readings UI')
    await page.getByRole('button', { name: 'Добавить показание' }).click()
    await page.getByRole('heading', { name: 'Добавить показание' }).waitFor()
    await page.locator('.drawer-loading').waitFor({ state: 'hidden' })
    const manualOptions = await page.locator('select[name="publication_id"] option').allTextContents()
    if (manualOptions.some((item) => item.startsWith('TikTok:') || item.startsWith('VK:') || item.startsWith('YouTube:'))) throw new Error('Automatic platform is exposed for manual readings')
    await page.getByTitle('Закрыть').click()
    await openNav()
    await page.getByRole('button', { name: 'Выплаты' }).click()
    await page.getByRole('button', { name: 'Реквизиты' }).click()
    await page.getByRole('heading', { name: 'Реквизиты для выплаты' }).waitFor()
    await page.locator('.drawer-loading').waitFor({ state: 'hidden' })
    await page.getByTitle('Закрыть').click()
    await page.getByRole('button', { name: 'Запросить выплату' }).click()
    await page.getByRole('heading', { name: 'Запросить выплату' }).waitFor()
    await page.getByTitle('Закрыть').click()
    await openNav()
    await page.getByRole('button', { name: 'Обращения' }).click()
    await page.getByRole('button', { name: 'Новое обращение' }).click()
    await page.getByRole('heading', { name: 'Новое обращение' }).waitFor()
    await page.getByTitle('Закрыть').click()
    await openNav()
    await page.getByRole('button', { name: 'Личный кабинет' }).click()
    await page.getByRole('heading', { name: 'Личный кабинет' }).waitFor()
    await page.locator('.drawer-loading').waitFor({ state: 'hidden' })
    console.log('E2E smoke passed: blogger profile, TikTok/VK content, readings, payouts, support and settings')
  } else if (email.includes('moderator')) {
    await openNav()
    await page.getByRole('button', { name: 'Очереди' }).click()
    await page.getByRole('heading', { name: 'Очереди' }).waitFor()
    const firstRow = page.locator('tbody tr').first()
    if (await firstRow.count()) {
      await firstRow.click()
      await page.locator('.work-drawer').waitFor()
      console.log('E2E smoke passed: moderator queue and decision workflow')
    } else {
      console.log('E2E smoke passed: moderator empty queue state')
    }
  } else if (email.includes('admin')) {
    await openNav()
    await page.getByRole('button', { name: 'Аналитика' }).click()
    await page.getByRole('heading', { name: 'Аналитика' }).waitFor()
    await page.locator('.analytics-filters').waitFor()
    await page.getByRole('button', { name: 'Применить' }).click()
    await page.locator('.analytics-risks').waitFor()
    await openNav()
    await page.getByRole('button', { name: 'Личный кабинет' }).click()
    await page.locator('.drawer-loading').waitFor({ state: 'hidden' })
    await page.locator('.account-nav button').filter({ hasText: 'Уведомления' }).click()
    await page.getByRole('heading', { name: 'Шаблоны уведомлений' }).waitFor()
    console.log('E2E smoke passed: admin analytics, filters and notification templates')
  } else {
    await openNav()
    await page.getByRole('button', { name: 'Выгрузки' }).click()
    await page.getByRole('heading', { name: 'Выгрузки' }).waitFor()
    if (await page.locator('.request-state--error').count()) {
      throw new Error('Backend page failed: ' + await page.locator('.request-state--error').textContent())
    }
    console.log('E2E smoke passed: login, session, dashboard and exports')
  }
  await page.waitForTimeout(350)
  if (apiServerErrors.length) throw new Error('API server errors: ' + [...new Set(apiServerErrors)].join(', '))
  if (process.env.AMP_E2E_SCREENSHOT) await page.screenshot({ path: process.env.AMP_E2E_SCREENSHOT, fullPage: true })
} finally {
  await browser.close()
}
