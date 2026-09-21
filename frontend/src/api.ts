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
    super(body.error?.message ?? body.detail ?? 'Не удалось выполнить запрос')
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
      await apiRequest<CurrentUser>('/auth/refresh', {
        method: 'POST',
        retryAuth: false,
      })
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
  return apiRequest<CurrentUser>('/auth/me')
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

export function loadPage(role: Role, page: PageId): Promise<PagePayload> {
  if (page === 'overview') {
    return apiRequest(role === 'blogger' ? '/me/dashboard' : '/staff/dashboard')
  }
  if (page === 'analytics') return apiRequest('/staff/analytics')
  if (page === 'moderation') {
    return Promise.all([
      apiRequest<PagePayload>('/moderation/profiles?pageSize=50'),
      apiRequest<PagePayload>('/moderation/publications?pageSize=50'),
    ]).then(([profiles, publications]) => ({ profiles, publications }))
  }
  if (page === 'creators') {
    return role === 'admin'
      ? apiRequest('/admin/users?pageSize=50')
      : apiRequest('/moderation/profiles?pageSize=50')
  }
  if (page === 'publications') {
    return role === 'blogger'
      ? apiRequest('/me/video-cards?pageSize=50')
      : apiRequest('/moderation/publications?pageSize=50')
  }
  if (page === 'readings') {
    return role === 'blogger'
      ? apiRequest('/me/view-readings?pageSize=50')
      : apiRequest('/moderation/view-readings?pageSize=50')
  }
  if (page === 'finance') {
    return role === 'blogger'
      ? apiRequest('/me/payout-requests?pageSize=50')
      : apiRequest('/staff/payout-requests?pageSize=50')
  }
  if (page === 'exports') return apiRequest('/staff/exports?pageSize=50')
  if (page === 'support') {
    return role === 'blogger'
      ? apiRequest('/me/support-tickets?pageSize=50')
      : apiRequest('/staff/support-tickets?pageSize=50')
  }
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