import { apiErrorMessage } from './labels'

export type Role = 'blogger' | 'moderator' | 'manager' | 'finance' | 'analyst' | 'admin'
export type PageId =
  | 'overview'
  | 'moderation'
  | 'creators'
  | 'publications'
  | 'readings'
  | 'finance'
  | 'exports'
  | 'support'
  | 'analytics'
  | 'catalog'
  | 'billing'
  | 'security'
  | 'legal'

export type CurrentUser = {
  id: string
  email: string
  role: Role
  status: string
  email_verified_at: string | null
}

export type ApiErrorBody = {
  error?: {
    code?: string
    message?: string
    details?: unknown
  }
  detail?: string
  request_id?: string
}

export class ApiError extends Error {
  status: number
  code: string
  requestId?: string

  constructor(status: number, body: ApiErrorBody) {
    super(apiErrorMessage(status, body.error?.code))
    this.name = 'ApiError'
    this.status = status
    this.code = body.error?.code ?? 'REQUEST_FAILED'
    this.requestId = body.request_id
  }
}

function csrfToken() {
  const item = document.cookie
    .split('; ')
    .find((cookie) => cookie.startsWith('amp_csrf='))
  return item ? decodeURIComponent(item.split('=').slice(1).join('=')) : null
}

async function parseError(response: Response) {
  const body = await response.json().catch(() => ({} as ApiErrorBody))
  return new ApiError(response.status, body)
}

type RequestOptions = RequestInit & {
  retryAuth?: boolean
}

let refreshPromise: Promise<CurrentUser> | null = null
let currentUserPromise: Promise<CurrentUser> | null = null

function refreshSession() {
  if (!refreshPromise) {
    refreshPromise = apiRequest<CurrentUser>('/auth/refresh', {
      method: 'POST',
      retryAuth: false,
    }).finally(() => { refreshPromise = null })
  }
  return refreshPromise
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { retryAuth = true, headers, ...requestOptions } = options
  const method = (requestOptions.method ?? 'GET').toUpperCase()
  const csrf = csrfToken()
  const response = await fetch(`/api/v1${path}`, {
    ...requestOptions,
    credentials: 'include',
    headers: {
      ...(requestOptions.body ? { 'Content-Type': 'application/json' } : {}),
      ...(csrf && !['GET', 'HEAD', 'OPTIONS'].includes(method) ? { 'X-CSRF-Token': csrf } : {}),
      ...headers,
    },
  })

  if (response.status === 401 && retryAuth && path !== '/auth/refresh') {
    try {
      await refreshSession()
      return apiRequest<T>(path, { ...options, retryAuth: false })
    } catch {
      throw await parseError(response)
    }
  }

  if (!response.ok) throw await parseError(response)
  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export function getCurrentUser() {
  if (!currentUserPromise) {
    currentUserPromise = apiRequest<CurrentUser>('/auth/me').finally(() => {
      currentUserPromise = null
    })
  }
  return currentUserPromise
}

export function login(email: string, password: string) {
  return apiRequest<CurrentUser>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
    retryAuth: false,
  })
}

export function logout() {
  return apiRequest<void>('/auth/logout', { method: 'POST', retryAuth: false })
}

export type PagePayload = Record<string, unknown>
export type PageFilters = Record<string, string>

export function loadPage(role: Role, page: PageId, pageNumber = 1, filters: PageFilters = {}): Promise<PagePayload> {
  const pagination = `page=${pageNumber}&pageSize=20`
  if (page === 'overview') {
    return apiRequest(role === 'blogger' ? '/me/dashboard' : '/staff/dashboard')
  }
  if (page === 'analytics') return apiRequest('/staff/analytics')
  if (page === 'moderation') {
    return Promise.all([
      apiRequest<JsonObject>(`/moderation/profiles?${pagination}&status=submitted`),
      apiRequest<JsonObject>(`/moderation/profiles?${pagination}&status=in_review`),
      apiRequest<PagePayload>(`/moderation/publications?${pagination}`),
      apiRequest<PagePayload>(`/moderation/social-accounts?${pagination}&status=pending`),
    ]).then(([submittedProfiles, reviewingProfiles, publications, socialAccounts]) => ({
      profiles: {
        items: [...(submittedProfiles.items ?? []), ...(reviewingProfiles.items ?? [])],
        page: pageNumber,
        page_size: 20,
        total_items: Number(submittedProfiles.total_items ?? 0) + Number(reviewingProfiles.total_items ?? 0),
        total_pages: Math.max(Number(submittedProfiles.total_pages ?? 0), Number(reviewingProfiles.total_pages ?? 0)),
      },
      publications,
      socialAccounts,
    }))
  }
  if (page === 'creators') {
    return role === 'admin'
      ? apiRequest(`/admin/users?${pagination}`)
      : apiRequest(`/moderation/profiles?${pagination}`)
  }
  if (page === 'publications') {
    if (role === 'blogger') return apiRequest(`/me/video-cards?${pagination}`)
    const params = new URLSearchParams(pagination)
    if (!filters.status) params.set('allStatuses', 'true')
    for (const [name, value] of Object.entries(filters)) if (value) params.set(name, value)
    return apiRequest(`/moderation/publications?${params.toString()}`)
  }
  if (page === 'readings') {
    const params = new URLSearchParams(pagination)
    for (const [name, value] of Object.entries(filters)) if (value) params.set(name, value)
    if (role !== 'blogger' && !filters.status) params.set('allStatuses', 'true')
    return role === 'blogger'
      ? apiRequest(`/me/view-readings?${params.toString()}`)
      : apiRequest(`/moderation/view-readings?${params.toString()}`)
  }
  if (page === 'finance') {
    if (role !== 'blogger') return apiRequest(`/staff/payout-requests?${pagination}`)
    return Promise.all([
      apiRequest<JsonObject>(`/me/payout-requests?${pagination}`),
      apiRequest<JsonObject>(`/me/earnings?${pagination}`),
      apiRequest<JsonObject>('/me/balance'),
    ]).then(([payouts, earnings, balance]) => ({
      payouts, earnings, balance,
      total_items: Number(payouts.total_items ?? 0) + Number(earnings.total_items ?? 0),
      total_pages: Math.max(Number(payouts.total_pages ?? 0), Number(earnings.total_pages ?? 0)),
    }))
  }
  if (page === 'exports') return apiRequest(`/staff/exports?${pagination}`)
  if (page === 'support') {
    return role === 'blogger'
      ? apiRequest(`/me/support-tickets?${pagination}`)
      : apiRequest(`/staff/support-tickets?${pagination}`)
  }
  if (page === 'catalog') return apiRequest(`/products?${pagination}&isActive=true`)
  if (page === 'billing') return apiRequest(`/moderation/calculation-periods?${pagination}`)
  if (page === 'security') return apiRequest(`/security-events?${pagination}`)
  if (page === 'legal') return apiRequest(`/admin/legal-documents?${pagination}`)
  return Promise.resolve({})
}

export async function downloadExport(id: string) {
  const response = await fetch(`/api/v1/staff/exports/${id}/download`, {
    credentials: 'include',
  })
  if (!response.ok) throw await parseError(response)
  const blob = await response.blob()
  const disposition = response.headers.get('Content-Disposition') ?? ''
  const filename = disposition.match(/filename="([^"]+)"/)?.[1] ?? 'export'
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}
export type JsonObject = Record<string, any>

export type PayoutMode = 'details' | 'request' | 'requisites'

export type PayoutCommand =
  | 'review'
  | 'approve'
  | 'reject'
  | 'payment'
  | 'receipt'

export function loadEarningsPeriod(period: string) {
  return apiRequest<JsonObject>(`/me/earnings/${period}`)
}

export function loadPayout(id: string, role: Role) {
  const scope = role === 'blogger' ? 'me' : 'staff'
  return apiRequest<JsonObject>(`/${scope}/payout-requests/${id}`)
}

export function loadPayoutDetails() {
  return apiRequest<JsonObject>('/me/payout-details')
}

export function savePayoutDetails(sbpPhone: string, bankName: string) {
  return apiRequest<JsonObject>('/me/payout-details', {
    method: 'PUT',
    body: JSON.stringify({ sbp_phone: sbpPhone, bank_name: bankName || null }),
  })
}

export function createPayoutRequest(idempotencyKey: string) {
  return apiRequest<JsonObject>('/me/payout-requests', {
    method: 'POST',
    body: JSON.stringify({ idempotency_key: idempotencyKey }),
  })
}

export function runPayoutCommand(id: string, command: PayoutCommand, idempotencyKey: string, payload: JsonObject = {}) {
  const routes: Record<PayoutCommand, string> = {
    review: 'reviews', approve: 'approvals', reject: 'rejections',
    payment: 'payments', receipt: 'receipts',
  }
  return apiRequest<JsonObject>(`/staff/payout-requests/${id}/${routes[command]}`, {
    method: 'POST',
    body: JSON.stringify({ idempotency_key: idempotencyKey, ...payload }),
  })
}

export function getCurrentLegalDocuments() {
  return apiRequest<JsonObject>('/legal-documents/current', { retryAuth: false })
}

export function registerAccount(payload: JsonObject) {
  return apiRequest<CurrentUser>('/auth/register', { method: 'POST', body: JSON.stringify(payload), retryAuth: false })
}

export function requestEmailVerification() {
  return apiRequest<void>('/auth/email-verification-requests', { method: 'POST' })
}

export function verifyEmail(token: string) {
  return apiRequest<void>('/auth/email-verifications', { method: 'POST', body: JSON.stringify({ token }), retryAuth: false })
}

export function requestPasswordReset(email: string) {
  return apiRequest<void>('/auth/password-reset-requests', { method: 'POST', body: JSON.stringify({ email }), retryAuth: false })
}

export function resetPassword(token: string, newPassword: string) {
  return apiRequest<void>('/auth/password-resets', { method: 'POST', body: JSON.stringify({ token, new_password: newPassword }), retryAuth: false })
}

export function listNotifications(unreadOnly = false, page = 1) {
  return apiRequest<JsonObject>(`/me/notifications?page=${page}&pageSize=20&unreadOnly=${unreadOnly}`)
}

export function getUnreadNotificationCount() {
  return apiRequest<JsonObject>('/me/notifications/unread-count')
}

export function markNotificationRead(id: string) {
  return apiRequest<JsonObject>(`/me/notifications/${id}/reads`, { method: 'POST' })
}

export function markAllNotificationsRead() {
  return apiRequest<JsonObject>('/me/notifications/read-all', { method: 'POST' })
}

export function listNotificationTemplates() {
  return apiRequest<JsonObject>('/admin/notification-templates?includeInactive=false')
}

export function createNotificationTemplateVersion(code: string, channel: string, payload: JsonObject) {
  return apiRequest<JsonObject>(`/admin/notification-templates/${encodeURIComponent(code)}/${channel}/versions`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function createSupportTicket(payload: JsonObject) {
  return apiRequest<JsonObject>('/me/support-tickets', { method: 'POST', body: JSON.stringify(payload) })
}

export function loadSupportTicket(id: string, role: Role) {
  return apiRequest<JsonObject>(`/${role === 'blogger' ? 'me' : 'staff'}/support-tickets/${id}`)
}

export function postSupportMessage(id: string, role: Role, body: string, idempotencyKey: string) {
  return apiRequest<JsonObject>(`/${role === 'blogger' ? 'me' : 'staff'}/support-tickets/${id}/messages`, {
    method: 'POST', body: JSON.stringify({ body, idempotency_key: idempotencyKey }),
  })
}

export function assignSupportTicket(id: string, assigneeUserId: string | null, reason: string, idempotencyKey: string) {
  return apiRequest<JsonObject>(`/staff/support-tickets/${id}/assignments`, {
    method: 'POST', body: JSON.stringify({ assignee_user_id: assigneeUserId, reason: reason || null, idempotency_key: idempotencyKey }),
  })
}

export function transitionSupportTicket(id: string, toStatus: string, reason: string, idempotencyKey: string) {
  return apiRequest<JsonObject>(`/staff/support-tickets/${id}/status-transitions`, {
    method: 'POST', body: JSON.stringify({ to_status: toStatus, reason: reason || null, idempotency_key: idempotencyKey }),
  })
}

export function decideRecovery(id: string, decision: string, reason: string, idempotencyKey: string) {
  return apiRequest<JsonObject>(`/staff/support-tickets/${id}/recovery-decisions`, {
    method: 'POST', body: JSON.stringify({ decision, reason, idempotency_key: idempotencyKey }),
  })
}

export function createExport(payload: JsonObject) {
  return apiRequest<JsonObject>('/staff/exports', { method: 'POST', body: JSON.stringify(payload) })
}

export function loadExport(id: string) {
  return apiRequest<JsonObject>(`/staff/exports/${id}`)
}

export function loadAdminUser(id: string) {
  return apiRequest<JsonObject>(`/admin/users/${id}`).then(async (user) => user.role === 'blogger'
    ? { ...user, lifecycle: await apiRequest<JsonObject>(`/staff/users/${id}/account-lifecycle`) }
    : user)
}

export function loadStaffBloggerCard(id: string) {
  return apiRequest<JsonObject>(`/staff/bloggers/${id}/card`)
}

export type AnalyticsFilters = {
  periodFrom?: string
  periodTo?: string
  brand?: string
  productId?: string
  platform?: string
}

export function loadStaffAnalytics(filters: AnalyticsFilters = {}) {
  const query = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value) query.set(key, value)
  })
  return apiRequest<JsonObject>(`/staff/analytics?${query.toString()}`)
}

export function changeAdminUserRole(id: string, role: Role, reason: string) {
  return apiRequest<JsonObject>(`/admin/users/${id}/role`, { method: 'PUT', body: JSON.stringify({ role, reason }) })
}

export function changeAdminUserAccess(id: string, isBlocked: boolean, reason: string) {
  return apiRequest<JsonObject>(`/admin/users/${id}/access`, { method: 'PATCH', body: JSON.stringify({ is_blocked: isBlocked, reason }) })
}

export function loadAccountSettings(role: Role) {
  if (role === 'admin') {
    return Promise.all([
      apiRequest<JsonObject>('/me/legal-acceptances?pageSize=50'),
      apiRequest<JsonObject>('/program-settings'),
      apiRequest<JsonObject>('/admin/legal-documents?pageSize=100'),
    ]).then(async ([acceptances, programSettings, documentRegistry]) => {
      const currentSummaries = (documentRegistry.items ?? []).filter((item: JsonObject) => item.is_current)
      const currentDocuments = await Promise.all(
        currentSummaries.map((item: JsonObject) => apiRequest<JsonObject>(`/legal-documents/${item.id}`)),
      )
      return {
        legalStatus: { is_participation_allowed: true, required_acceptances: [] },
        acceptances,
        programSettings,
        legalDocuments: Object.fromEntries(
          currentDocuments.map((item) => [String(item.document_type), item]),
        ),
      }
    })
  }
  if (role !== 'blogger') {
    return Promise.all([
      apiRequest<JsonObject>('/me/legal-acceptances?pageSize=50'),
      apiRequest<JsonObject>('/program-settings'),
    ]).then(([acceptances, programSettings]) => ({
      legalStatus: { is_participation_allowed: true, required_acceptances: [] },
      acceptances,
      programSettings,
      legalDocuments: {},
    }))
  }
  const core = Promise.all([
    apiRequest<JsonObject>('/me/legal-status'),
    apiRequest<JsonObject>('/me/legal-acceptances?pageSize=50'),
    apiRequest<JsonObject>('/program-settings'),
    getCurrentLegalDocuments(),
  ])
  return Promise.all([core, apiRequest<JsonObject>('/me/account-lifecycle'), apiRequest<JsonObject>('/me/account-deletion-requests?pageSize=20')])
    .then(([[legalStatus, acceptances, programSettings, legalDocuments], lifecycle, deletions]) => ({ legalStatus, acceptances, programSettings, legalDocuments, lifecycle, deletions }))
}

export function saveProgramSettings(payload: JsonObject) {
  return apiRequest<JsonObject>('/program-settings', { method: 'PUT', body: JSON.stringify(payload) })
}

export function acceptLegalDocument(documentId: string) {
  return apiRequest<JsonObject>('/me/legal-acceptances', { method: 'POST', body: JSON.stringify({ document_id: documentId, accepted: true }) })
}

export function createAccountDeletion(password: string, idempotencyKey: string) {
  return apiRequest<JsonObject>('/me/account-deletion-requests', { method: 'POST', body: JSON.stringify({ password, idempotency_key: idempotencyKey }) })
}

export function cancelAccountDeletion(id: string, idempotencyKey: string) {
  return apiRequest<JsonObject>(`/me/account-deletion-requests/${id}/cancellations`, { method: 'POST', body: JSON.stringify({ idempotency_key: idempotencyKey }) })
}

export function confirmAccountDeletion(id: string, token: string, idempotencyKey: string) {
  return apiRequest<JsonObject>(`/account-deletion-requests/${id}/confirmations`, { method: 'POST', body: JSON.stringify({ token, idempotency_key: idempotencyKey }), retryAuth: false })
}

export function loadProduct(id: string) { return apiRequest<JsonObject>(`/products/${id}`) }
export function listProducts(pageSize = 100) {
  return apiRequest<JsonObject>(`/products?pageSize=${pageSize}&isActive=true`)
}
export function saveProduct(id: string | undefined, payload: JsonObject) {
  return apiRequest<JsonObject>(id ? `/products/${id}` : '/products', { method: id ? 'PATCH' : 'POST', body: JSON.stringify(payload) })
}
export function loadCalculationPeriod(id: string) {
  return Promise.all([apiRequest<JsonObject>(`/moderation/calculation-periods/${id}`), apiRequest<JsonObject>(`/moderation/calculation-periods/${id}/accruals?pageSize=100`)]).then(([period, accruals]) => ({ ...period, accruals: accruals.items ?? [] }))
}
export function runCalculationPeriod(id: string, command: 'recalculations' | 'confirmations') {
  return apiRequest<JsonObject>(`/moderation/calculation-periods/${id}/${command}`, { method: 'POST' })
}
export function createBillingRate(payload: JsonObject) {
  return apiRequest<JsonObject>('/admin/billing/rates', { method: 'POST', body: JSON.stringify(payload) })
}
export function loadSecurityEvent(id: string) { return apiRequest<JsonObject>(`/security-events/${id}`) }
export function loadLegalDocument(id: string) { return apiRequest<JsonObject>(`/legal-documents/${id}`) }
export function publishLegalDocument(payload: JsonObject) {
  return apiRequest<JsonObject>('/admin/legal-documents', { method: 'POST', body: JSON.stringify(payload) })
}

export async function loadCreatorIdentity() {
  const response = await apiRequest<JsonObject>('/me/profile')
  return response.profile ?? null
}

export function uploadProfileAvatar(file: File) {
  return apiRequest<JsonObject>('/me/profile/avatar', {
    method: 'PUT',
    body: file,
    headers: { 'Content-Type': file.type },
  })
}

export function deleteProfileAvatar() {
  return apiRequest<void>('/me/profile/avatar', { method: 'DELETE' })
}
export async function loadCreatorWorkspace(mode: 'profile' | 'content' | 'reading') {
  if (mode === 'profile') {
    const [profile, socialAccounts] = await Promise.all([
      apiRequest<JsonObject>('/me/profile'),
      apiRequest<JsonObject[]>('/me/social-accounts'),
    ])
    return { profile: profile.profile ?? null, socialAccounts }
  }
  if (mode === 'content') {
    const [socialAccounts, products] = await Promise.all([
      apiRequest<JsonObject[]>('/me/social-accounts'),
      apiRequest<JsonObject>('/products?pageSize=100&isActive=true'),
    ])
    return { socialAccounts, products: products.items ?? [] }
  }
  const cards = await apiRequest<JsonObject>('/me/video-cards?pageSize=100')
  const publicationPages = await Promise.all(
    (cards.items ?? []).map((card: JsonObject) =>
      apiRequest<JsonObject>(`/me/video-cards/${card.id}/publications?pageSize=100`),
    ),
  )
  return {
    cards: cards.items ?? [],
    publications: publicationPages.flatMap((page) => page.items ?? []),
  }
}

export function saveCreatorProfile(payload: JsonObject) {
  return apiRequest<JsonObject>('/me/profile', {
    method: 'PUT',
    body: JSON.stringify(payload),
  })
}

export function addSocialAccount(payload: JsonObject) {
  return apiRequest<JsonObject>('/me/social-accounts', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function createVideoCard(payload: JsonObject) {
  return apiRequest<JsonObject>('/me/video-cards', {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function submitCreatorProfile() {
  return apiRequest<JsonObject>('/me/profile-submissions', { method: 'POST' })
}

export function updateSocialAccount(id: string, payload: JsonObject) {
  return apiRequest<JsonObject>(`/me/social-accounts/${id}`, { method: 'PATCH', body: JSON.stringify(payload) })
}

export function deleteSocialAccount(id: string) {
  return apiRequest<void>(`/me/social-accounts/${id}`, { method: 'DELETE' })
}

export function loadVideoCardDetails(cardId: string) {
  return Promise.all([
    apiRequest<JsonObject>(`/me/video-cards/${cardId}`),
    apiRequest<JsonObject>(`/me/video-cards/${cardId}/publications?pageSize=100`),
    apiRequest<JsonObject[]>('/me/social-accounts'),
  ]).then(([card, publications, socialAccounts]) => ({
    card,
    publications: publications.items ?? [],
    socialAccounts,
  }))
}

export function updateVideoCard(cardId: string, payload: JsonObject) {
  return apiRequest<JsonObject>(`/me/video-cards/${cardId}`, { method: 'PATCH', body: JSON.stringify(payload) })
}

export function updatePublication(publicationId: string, url: string) {
  return apiRequest<JsonObject>(`/me/publications/${publicationId}`, { method: 'PATCH', body: JSON.stringify({ url }) })
}

export function deletePublication(publicationId: string) {
  return apiRequest<void>(`/me/publications/${publicationId}`, { method: 'DELETE' })
}

export function createPublication(cardId: string, payload: JsonObject) {
  return apiRequest<JsonObject>(`/me/video-cards/${cardId}/publications`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export function submitPublication(publicationId: string) {
  return apiRequest<JsonObject>(`/me/publications/${publicationId}/submissions`, {
    method: 'POST',
  })
}

export function createManualReading(publicationId: string, value: number) {
  return apiRequest<JsonObject>(`/me/publications/${publicationId}/view-readings`, {
    method: 'POST',
    body: JSON.stringify({ value }),
  })
}

export function reviewProfile(profileId: string, decision: string, reason?: string) {
  return apiRequest<JsonObject>(`/moderation/profiles/${profileId}/reviews`, {
    method: 'POST',
    body: JSON.stringify({ decision, reason: reason || null }),
  })
}

export function updateManualReading(readingId: string, value: number) {
  return apiRequest<JsonObject>(`/me/view-readings/${readingId}`, {
    method: 'PATCH',
    body: JSON.stringify({ value }),
  })
}

export function reviewSocialAccount(accountId: string, decision: string, reason?: string) {
  return apiRequest<JsonObject>(`/moderation/social-accounts/${accountId}/reviews`, {
    method: 'POST', body: JSON.stringify({ decision, reason: reason || null }),
  })
}

export function loadPublicationModerationWorkspace(publicationId: string) {
  return Promise.all([
    apiRequest<JsonObject>(`/moderation/publications/${publicationId}`),
    apiRequest<JsonObject>('/products?pageSize=100&isActive=true'),
    apiRequest<JsonObject>('/program-settings'),
  ]).then(([detail, products, programSettings]) => ({ detail, products: products.items ?? [], programSettings }))
}

export function deactivatePublication(publicationId: string, reason: string) {
  return apiRequest<JsonObject>(`/moderation/publications/${publicationId}/deactivations`, {
    method: 'POST', body: JSON.stringify({ reason }),
  })
}

export function recordPromoIssuance(publicationId: string, marketplace: string, note?: string) {
  return apiRequest<JsonObject>(`/moderation/publications/${publicationId}/promo-issuances`, {
    method: 'POST', body: JSON.stringify({ marketplace, note: note || null }),
  })
}

export function reviewPublication(publicationId: string, decision: string, reason?: string, resolvedProductId?: string) {
  return apiRequest<JsonObject>(`/moderation/publications/${publicationId}/reviews`, {
    method: 'POST',
    body: JSON.stringify({
      decision,
      reason: reason || null,
      resolved_product_id: decision === 'approve' && resolvedProductId ? resolvedProductId : null,
    }),
  })
}

export function reviewReading(readingId: string, decision: string, reason?: string, acceptedValue?: number) {
  return apiRequest<JsonObject>(`/moderation/view-readings/${readingId}/decisions`, {
    method: 'POST',
    body: JSON.stringify({
      decision,
      reason: reason || null,
      accepted_value: decision === 'correct' ? acceptedValue : null,
    }),
  })
}

export function correctReading(readingId: string, acceptedValue: number, reason: string) {
  return apiRequest<JsonObject>(`/moderation/view-readings/${readingId}/corrections`, {
    method: 'POST', body: JSON.stringify({ accepted_value: acceptedValue, reason }),
  })
}
