export const roleLabels: Record<string, string> = {
  blogger: 'Блогер', moderator: 'Модератор', manager: 'Менеджер',
  finance: 'Финансы', analyst: 'Аналитик', admin: 'Администратор',
}

export const statusLabels: Record<string, string> = {
  active: 'Активен', approved: 'Одобрено', awaiting_confirmation: 'Ожидает подтверждения',
  awaiting_receipt: 'Ожидается чек', blocked: 'Заблокирован', cancelled: 'Отменено',
  changes_required: 'Нужны исправления', closed: 'Закрыто', completed: 'Готово',
  confirmed: 'Подтверждено', corrected: 'Исправлено', deleted: 'Удалено', draft: 'Черновик',
  email_pending: 'Почта не подтверждена', expired: 'Срок истёк', failed: 'Ошибка',
  in_progress: 'В работе', in_review: 'На проверке', inactive: 'Неактивно',
  new: 'Новое', not_applicable: 'Не требуется', not_started: 'Расчёт ещё не начат',
  open: 'Открыто', overdue: 'Просрочено', paid: 'Выплачено',
  partially_approved: 'Одобрено частично', pending: 'Ожидает проверки',
  pending_payment: 'Ожидает выплаты', pending_review: 'На проверке',
  preliminary: 'Предварительный расчёт', processing: 'Выполняется', queued: 'В очереди',
  ready: 'Готово', received: 'Получен', re_review_required: 'Нужна повторная проверка',
  rejected: 'Отклонено', requested: 'Заявка создана', resolved: 'Решено',
  retry_wait: 'Повторная попытка позже', submitted: 'Отправлено на проверку',
  succeeded: 'Выполнено', suspended: 'Приостановлено', under_review: 'На проверке',
  waiting_blogger: 'Ожидает ответа блогера', accepted: 'Принято',
}

export const documentLabels: Record<string, string> = {
  program_terms: 'Условия программы',
  personal_data_consent: 'Согласие на обработку данных',
  privacy_policy: 'Политика конфиденциальности',
}

export const recipientLabels: Record<string, string> = {
  self_employed: 'Самозанятый', individual: 'Физическое лицо',
}

export const supportCategoryLabels: Record<string, string> = {
  general: 'Общий вопрос', content: 'Контент и публикации', payment: 'Выплаты',
  technical: 'Техническая проблема', account_recovery: 'Восстановление аккаунта',
}

export const exportTypeLabels: Record<string, string> = {
  bloggers: 'Блогеры', social_accounts: 'Социальные аккаунты', publications: 'Публикации',
  view_readings: 'Показания просмотров', moderation_history: 'История модерации',
  accruals: 'Начисления', payout_register: 'Реестр выплат', payout_history: 'История выплат',
  support_tickets: 'Обращения', audit_log: 'Журнал безопасности',
}

export const riskFlagLabels: Record<string, string> = {
  views_decreased: 'Количество просмотров уменьшилось',
  unusual_growth: 'Необычно быстрый рост просмотров',
  large_growth: 'Резкий рост просмотров',
  random_review: 'Выбрано для выборочной проверки',
  approximate_public_counter: 'Данные получены из открытого счётчика',
}

const exclusionReasonLabels: Record<string, string> = {
  missing_current_reading: 'Нет показаний за текущий период',
  baseline_only: 'Пока недостаточно данных для расчёта',
}

const payoutEventLabels: Record<string, string> = {
  requested: 'Заявка создана', review_started: 'Проверка начата',
  approved: 'Выплата одобрена', rejected: 'Выплата отклонена',
  paid: 'Выплата проведена', receipt_received: 'Чек получен',
}

const profileEventLabels: Record<string, string> = {
  created: 'Профиль создан', updated: 'Профиль обновлён', submitted: 'Профиль отправлен на проверку',
  reviewed: 'Профиль проверен', social_account_created: 'Площадка добавлена',
  social_account_restored: 'Площадка восстановлена', social_account_updated: 'Площадка обновлена',
  social_account_deleted: 'Площадка удалена', social_account_approved: 'Площадка одобрена',
  social_account_rejected: 'Площадка отклонена',
}

const readingEventLabels: Record<string, string> = {
  created: 'Показание добавлено', edited: 'Показание изменено', accepted: 'Показание принято',
  rejected: 'Показание отклонено', corrected: 'Показание исправлено',
  auto_accepted: 'Принято автоматически',
}

const integrationErrorLabels: Record<string, string> = {
  external_id_missing: 'Не удалось определить ролик', stale_command: 'Обновление устарело',
  youtube_response_invalid: 'YouTube вернул некорректные данные',
  tiktok_response_invalid: 'TikTok вернул некорректные данные',
  vk_response_invalid: 'VK вернул некорректные данные',
  rutube_response_invalid: 'RUTUBE вернул некорректные данные',
  youtube_unreachable: 'YouTube временно недоступен', tiktok_unreachable: 'TikTok временно недоступен',
  vk_unreachable: 'VK временно недоступен', rutube_unreachable: 'RUTUBE временно недоступен',
  youtube_not_found: 'Ролик YouTube не найден или недоступен',
  tiktok_not_found: 'Ролик TikTok не найден или недоступен',
  vk_not_found: 'Ролик VK не найден или недоступен',
  rutube_not_found: 'Ролик RUTUBE не найден или недоступен',
  vk_rate_limited: 'VK временно ограничил обновление данных',
  vk_api_unavailable: 'Сервис VK временно недоступен',
  youtube_video_id_mismatch: 'Получены данные другого ролика',
  tiktok_video_id_mismatch: 'Получены данные другого ролика',
  vk_video_id_mismatch: 'Получены данные другого ролика',
  rutube_video_id_mismatch: 'Получены данные другого ролика',
  publication_not_active: 'Публикация больше не активна', video_unavailable: 'Ролик недоступен',
  reading_period_financially_closed: 'Расчётный период уже закрыт',
  worker_lease_expired: 'Время обновления истекло, попытка будет повторена',
  broker_publish_failed: 'Не удалось запустить обновление данных',
  export_too_large: 'Слишком много данных для одного файла',
  export_generation_failed: 'Не удалось сформировать файл',
}

const securityActionLabels: Record<string, string> = {
  'auth.login_succeeded': 'Вход в аккаунт', 'auth.login_failed': 'Неудачная попытка входа',
  'auth.logout': 'Выход из аккаунта', 'auth.password_changed': 'Пароль изменён',
  'content.publication_created': 'Публикация добавлена',
  'content.publication_updated': 'Публикация обновлена',
  'content.publication_reviewed': 'Публикация проверена',
  'payout.requested': 'Выплата запрошена', 'payout.approved': 'Выплата одобрена',
  'payout.paid': 'Выплата проведена',
}

const securityResultLabels: Record<string, string> = {
  success: 'Успешно', failure: 'Ошибка', denied: 'Отказано',
}

const apiErrorLabels: Record<string, string> = {
  invalid_credentials: 'Неверная почта или пароль', authentication_required: 'Необходимо войти в аккаунт',
  email_not_verified: 'Сначала подтвердите электронную почту', forbidden: 'Недостаточно прав для этого действия',
  not_found: 'Запрошенные данные не найдены', conflict: 'Данные уже были изменены. Обновите страницу',
  rate_limit_exceeded: 'Слишком много запросов. Попробуйте немного позже',
  validation_error: 'Проверьте заполнение полей', csrf_failed: 'Сессия устарела. Обновите страницу',
  legal_acceptance_required: 'Примите актуальные документы в личном кабинете',
  profile_incomplete: 'Заполните обязательные данные профиля',
  profile_not_editable: 'Сейчас профиль нельзя изменить',
  social_account_not_approved: 'Сначала дождитесь одобрения площадки',
  publication_url_invalid: 'Проверьте ссылку на публикацию',
  publication_url_already_exists: 'Эта публикация уже добавлена',
  reading_window_closed: 'Период добавления показаний уже закрыт',
  reading_is_automatic: 'Просмотры для этой площадки обновляются автоматически',
  no_available_balance: 'Сейчас нет доступной суммы для выплаты',
  payout_already_active: 'Заявка на выплату уже обрабатывается',
  payout_blocked_by_overdue_receipt: 'Сначала загрузите чек по предыдущей выплате',
  export_not_ready: 'Файл ещё формируется',
  support_ticket_closed: 'Обращение уже закрыто',
  invalid_or_expired_token: 'Ссылка недействительна или устарела',
}

const lifecycleActivityLabels: Record<string, string> = {
  login: 'Вход в личный кабинет', profile_updated: 'Обновление профиля',
  publication_created: 'Добавление публикации', publication_updated: 'Обновление публикации',
  support_message: 'Сообщение в обращении', payout_requested: 'Запрос выплаты',
}

const lifecycleTransitionLabels: Record<string, string> = {
  inactive: 'Переход в неактивный статус', suspended: 'Приостановка аккаунта',
  archived: 'Перенос в архив', reactivation: 'Возобновление работы',
}

const label = (labels: Record<string, string>, value: unknown, fallback: string) =>
  typeof value === 'string' && labels[value] ? labels[value] : fallback

export const roleLabel = (value: unknown) => label(roleLabels, value, 'Пользователь')
export const statusLabel = (value: unknown, fallback = 'Статус уточняется') => label(statusLabels, value, fallback)
export const documentLabel = (value: unknown) => label(documentLabels, value, 'Документ')
export const recipientLabel = (value: unknown) => label(recipientLabels, value, 'Получатель')
export const supportCategoryLabel = (value: unknown) => label(supportCategoryLabels, value, 'Обращение')
export const exportTypeLabel = (value: unknown) => label(exportTypeLabels, value, 'Выгрузка данных')
export const riskFlagLabel = (value: unknown) => label(riskFlagLabels, value, 'Требуется дополнительная проверка')
export const exclusionReasonLabel = (value: unknown) => label(exclusionReasonLabels, value, 'Не учтено в расчёте')
export const payoutEventLabel = (value: unknown) => label(payoutEventLabels, value, 'Статус выплаты изменён')
export const profileEventLabel = (value: unknown) => label(profileEventLabels, value, 'Данные профиля изменены')
export const readingEventLabel = (value: unknown) => label(readingEventLabels, value, 'Показание изменено')
export const integrationErrorLabel = (value: unknown) => label(integrationErrorLabels, value, 'Не удалось обновить данные')
export const securityActionLabel = (value: unknown) => label(securityActionLabels, value, 'Системное действие')
export const securityResultLabel = (value: unknown) => label(securityResultLabels, value, 'Результат не определён')
export const lifecycleActivityLabel = (value: unknown) => label(lifecycleActivityLabels, value, 'Действие в личном кабинете')
export const lifecycleTransitionLabel = (value: unknown) => label(lifecycleTransitionLabels, value, 'Изменение статуса аккаунта')
export const exportFormatLabel = (value: unknown) => value === 'xlsx' ? 'Excel' : value === 'csv' ? 'CSV' : 'Файл'

export function apiErrorMessage(status: number, code: unknown) {
  const normalized = typeof code === 'string' ? code.toLowerCase() : ''
  if (apiErrorLabels[normalized]) return apiErrorLabels[normalized]
  if (status === 400 || status === 422) return 'Проверьте заполнение полей'
  if (status === 401) return 'Необходимо войти в аккаунт'
  if (status === 403) return 'Недостаточно прав для этого действия'
  if (status === 404) return 'Запрошенные данные не найдены'
  if (status === 409) return 'Данные уже были изменены. Обновите страницу'
  if (status === 429) return 'Слишком много запросов. Попробуйте немного позже'
  return 'Не удалось выполнить запрос. Попробуйте ещё раз'
}
