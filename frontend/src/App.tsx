import { FormEvent, ReactNode, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle, ArrowLeft, ArrowRight, BarChart3, Check, CircleDollarSign, Download,
  Eye, FileSpreadsheet, Gauge, HelpCircle, LayoutDashboard, LoaderCircle,
  LogOut, Menu, MessageSquareText, RefreshCw, Search, Settings,
  SlidersHorizontal, UsersRound, Video, WalletCards, WifiOff, X, Plus, Save, Link2, ClipboardCheck,
  Package, Calculator, ShieldCheck, ScrollText, Trash2,
} from 'lucide-react'
import {
  ApiError, CurrentUser, PageFilters, PageId, PagePayload, Role,
  getCurrentUser, loadCreatorIdentity, loadPage, logout, correctReading, createManualReading, createPublication,
  createVideoCard, deactivatePublication, loadCreatorWorkspace, loadPublicationModerationWorkspace, recordPromoIssuance, reviewProfile, reviewPublication, reviewReading,
  reviewSocialAccount, submitPublication,
} from './api'
import { CreatorOverview } from './CreatorOverview'
import { PayoutDrawer } from './PayoutDrawer'
import type { PayoutMode } from './api'
import { SupportDrawer } from './SupportDrawer'
import { ExportDrawer } from './ExportDrawer'
import { AdminUserDrawer } from './AdminUserDrawer'
import { NotificationMenu } from './NotificationMenu'
import { SettingsDrawer } from './SettingsDrawer'
import { AuthPortal } from './AuthPortal'
import { OperationKind, OperationsDrawer } from './OperationsDrawer'
import { EntityDrawer } from './EntityDrawer'
import { BloggerDrawer } from './BloggerDrawer'
import { AnalyticsPage } from './AnalyticsPage'
import { EarningsDrawer } from './EarningsDrawer'
import { availabilityLabel, enrichmentLabel, platformConfig, platformIds, readingSourceLabel, visibleRiskFlags } from './platforms'
import {
  documentLabel, exportFormatLabel, exportTypeLabel, integrationErrorLabel, recipientLabel, riskFlagLabel,
  roleLabel, roleLabels, securityActionLabel, securityObjectLabel, securityResultLabel, statusLabel, statusLabels,
  supportCategoryLabel,
} from './labels'

type Tone = 'red' | 'amber' | 'green' | 'blue' | 'gray'
type Json = Record<string, any>
type Row = {
  id: string; rawId?: string; title: string; meta: string; status: string;
  rawStatus: string; tone: Tone; date: string; value: string; initials: string; payload?: Json; thumbnailUrl?: string;
}
type NavItem = { id: PageId; label: string; icon: typeof LayoutDashboard }

const rolePages: Record<Role, PageId[]> = {
  blogger: ['overview', 'catalog', 'publications', 'readings', 'finance', 'support'],
  moderator: ['overview', 'moderation', 'creators', 'catalog', 'publications', 'readings', 'billing', 'exports', 'support'],
  manager: ['overview', 'catalog', 'publications', 'readings', 'billing', 'finance', 'exports', 'support'],
  finance: ['billing', 'finance', 'exports'],
  analyst: ['analytics', 'exports'],
  admin: ['overview', 'moderation', 'creators', 'catalog', 'publications', 'readings', 'billing', 'finance', 'exports', 'support', 'analytics', 'security', 'legal'],
}
const navGroups: { label: string; items: NavItem[] }[] = [
  { label: 'Работа', items: [
    { id: 'overview', label: 'Обзор', icon: LayoutDashboard },
    { id: 'moderation', label: 'Очереди', icon: Gauge },
    { id: 'creators', label: 'Блогеры', icon: UsersRound },
    { id: 'publications', label: 'Публикации', icon: Video },
    { id: 'readings', label: 'Показания', icon: Eye },
    { id: 'catalog', label: 'Каталог', icon: Package },
  ]},
  { label: 'Деньги и данные', items: [
    { id: 'finance', label: 'Выплаты', icon: WalletCards },
    { id: 'exports', label: 'Выгрузки', icon: FileSpreadsheet },
    { id: 'analytics', label: 'Аналитика', icon: BarChart3 },
    { id: 'billing', label: 'Расчёты', icon: Calculator },
  ]},
  { label: 'Связь', items: [{ id: 'support', label: 'Обращения', icon: MessageSquareText }]},
  { label: 'Система', items: [{ id: 'security', label: 'Безопасность', icon: ShieldCheck }, { id: 'legal', label: 'Документы', icon: ScrollText }]},
]
const pageMeta: Record<PageId, [string, string]> = {
  overview: ['Рабочий стол', 'Текущие показатели и ближайшие действия'],
  moderation: ['Очереди', 'Задачи, которые требуют решения сотрудника'],
  creators: ['Блогеры', 'Профили, аккаунты и статусы участия'],
  publications: ['Публикации', 'Контент и карточки роликов по площадкам'],
  readings: ['Показания', 'Значения просмотров и подозрительные изменения'],
  finance: ['Выплаты', 'Запросы, реквизиты и фиксация оплаты'],
  exports: ['Выгрузки', 'Отчёты и платёжные реестры'],
  support: ['Обращения', 'Диалоги, вопросы и запросы восстановления'],
  analytics: ['Аналитика', 'Контент, аудитория и выплаты по программе'],
  catalog: ['Каталог', 'Товары и обязательные требования к публикациям'],
  billing: ['Расчёты', 'Периоды, начисления и подтверждение результатов'],
  security: ['Безопасность', 'Неизменяемый журнал действий и отказов'],
  legal: ['Документы', 'Версии юридических документов программы'],
}
const queueLabels: Record<string, string> = {
  new_profiles: 'Новые заявки блогеров', profiles_re_review: 'Профили после исправлений',
  publications_pending: 'Новые публикации', publications_re_submitted: 'Публикации после исправлений',
  manual_readings_pending: 'Показания на проверке', suspicious_readings: 'Подозрительные приросты',
  publications_changes_required: 'Публикации требуют правок', unavailable_publications: 'Недоступные публикации',
  calculations_preliminary: 'Расчёты на подтверждение', payouts_requested: 'Запросы выплат',
  payouts_approved: 'Выплаты к обработке', unverified_requisites: 'Непроверенные реквизиты',
  overdue_receipts: 'Просроченные чеки', new_support_tickets: 'Новые обращения',
  youtube_enrichment_errors: 'Ошибки метаданных YouTube', youtube_view_errors: 'Ошибки просмотров YouTube',
  tiktok_enrichment_errors: 'Ошибки метаданных TikTok', tiktok_view_errors: 'Ошибки просмотров TikTok',
  vk_enrichment_errors: 'Ошибки метаданных VK', vk_view_errors: 'Ошибки просмотров VK',
}

function queueTarget(code: string): { page: PageId; filters: PageFilters } {
  if (['new_profiles', 'profiles_re_review', 'publications_pending', 'publications_re_submitted', 'publications_changes_required'].includes(code)) return { page: 'moderation', filters: {} }
  if (['manual_readings_pending', 'suspicious_readings'].includes(code)) return { page: 'readings', filters: {} }
  if (code === 'calculations_preliminary') return { page: 'billing', filters: {} }
  if (['payouts_requested', 'payouts_approved', 'unverified_requisites', 'overdue_receipts'].includes(code)) return { page: 'finance', filters: {} }
  if (code === 'new_support_tickets') return { page: 'support', filters: {} }
  if (code === 'unavailable_publications') return { page: 'publications', filters: { availability: 'unavailable' } }
  const platform = ['youtube', 'tiktok', 'vk', 'rutube'].find((item) => code.startsWith(item + '_'))
  if (platform) return { page: 'publications', filters: { platform } }
  return { page: 'overview', filters: {} }
}
const rubles = (kopecks?: number) => new Intl.NumberFormat('ru-RU').format(Math.round((kopecks ?? 0) / 100)) + ' ₽'
const numeric = (value?: number) => new Intl.NumberFormat('ru-RU', {
  notation: (value ?? 0) >= 1_000_000 ? 'compact' : 'standard', maximumFractionDigits: 2,
}).format(value ?? 0)
const date = (value: unknown) => {
  if (!value || typeof value !== 'string') return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '—' : new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric',
  }).format(parsed)
}
const initials = (value: string) => (value.trim().split(/[\s@._-]+/).filter(Boolean)
  .slice(0, 2).map((part) => part[0]).join('') || 'AMP').toUpperCase()
type TransitionDocument = Document & { startViewTransition?: (update: () => void) => unknown }
const runTransition = (update: () => void) => {
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const start = (document as TransitionDocument).startViewTransition
  if (reducedMotion || !start) return update()
  start.call(document, update)
}
const tone = (status: string, risk = false): Tone => {
  if (risk || ['failed', 'rejected', 'blocked', 'overdue', 'changes_required'].includes(status)) return 'red'
  if (['pending', 'pending_review', 'processing', 'submitted', 'under_review', 'in_progress', 'preliminary'].includes(status)) return 'amber'
  if (['approved', 'active', 'ready', 'paid', 'resolved', 'completed', 'accepted', 'corrected', 'confirmed'].includes(status)) return 'green'
  if (['new', 'open', 'partially_approved'].includes(status)) return 'blue'
  return 'gray'
}
const row = (data: Partial<Row> & Pick<Row, 'id' | 'title'>): Row => ({
  meta: '', status: '—', rawStatus: '', tone: 'gray', date: '—', value: '—',
  initials: initials(data.title), ...data,
})

function rowsFrom(payload: PagePayload | null, page: PageId, role: Role): Row[] {
  if (!payload) return []
  const data = payload as Json
  if (page === 'moderation') {
    const profileRows = ((data.profiles as Json)?.items ?? []).map((item: Json) => row({
      id: 'profile-' + item.id, rawId: item.id, title: item.display_name || item.full_name || 'Профиль без имени',
      meta: item.city_country || 'Заявка блогера', status: statusLabel(item.status),
      rawStatus: item.status, tone: tone(item.status), date: date(item.updated_at), value: 'Профиль', payload: item,
    }))
    const publicationRows = ((data.publications as Json)?.items ?? []).map((item: Json) => {
      const publication = item.publication ?? {}
      const creator = item.creator_display_name || item.creator_full_name || item.creator_email || 'Блогер'
      return row({ id: 'publication-' + publication.id, rawId: publication.id,
        title: item.card_title || publication.external_title || 'Публикация',
        meta: creator + ' · ' + platformConfig(publication.platform).label,
        status: statusLabel(publication.status), rawStatus: publication.status,
        tone: tone(publication.status), date: date(publication.updated_at), value: 'Публикация',
        initials: initials(creator), payload: item,
      })
    })
    const socialRows = ((data.socialAccounts as Json)?.items ?? []).map((item: Json) => {
      const account = item.account ?? {}
      const creator = item.creator_display_name || item.creator_full_name || 'Блогер'
      const platform = platformConfig(account.platform)
      return row({ id: 'social-' + account.id, rawId: account.id, title: creator, meta: platform.label + ' · ' + account.url, status: statusLabel(account.status), rawStatus: account.status, tone: tone(account.status), date: date(account.updated_at), value: numeric(account.follower_count) + ' подписчиков', initials: platform.shortLabel, payload: item })
    })
    return [...profileRows, ...publicationRows, ...socialRows]
  }
  if (page === 'finance' && role === 'blogger' && data.balance) {
    const balance = data.balance as Json
    const balanceRow = row({ id: 'balance', title: 'Доступный баланс', meta: `${rubles(balance.reserved_kopecks)} зарезервировано · ${rubles(balance.paid_kopecks)} выплачено`, status: balance.claim_expired_at ? 'Срок запроса истёк' : 'Доступен', rawStatus: balance.claim_expired_at ? 'expired' : 'active', tone: balance.claim_expired_at ? 'red' : 'green', date: date(balance.updated_at), value: rubles(balance.available_kopecks), initials: '₽', payload: { ...balance, __kind: 'balance' } })
    const earningRows = ((data.earnings as Json)?.items ?? []).map((item: Json) => row({ id: `earning-${item.period.id}`, rawId: item.period.period, title: `Начисления за ${date(item.period.period)}`, meta: `${numeric(item.total.eligible_views)} просмотров · ${item.total.publication_count} публикаций`, status: statusLabel(item.period.status), rawStatus: item.period.status, tone: tone(item.period.status), date: date(item.period.confirmed_at || item.period.updated_at), value: rubles(item.total.payable_amount_kopecks), initials: 'Н', payload: { ...item, __kind: 'earning' } }))
    const payoutRows = ((data.payouts as Json)?.items ?? []).map((item: Json) => row({ id: item.request_number || item.id, rawId: item.id, title: item.request_number || 'Запрос выплаты', meta: recipientLabel(item.recipient_type) + ' · запрос выплаты', status: statusLabel(item.status), rawStatus: item.status, tone: tone(item.status, Boolean(item.is_payment_overdue)), date: date(item.updated_at || item.requested_at), value: rubles(item.amount_kopecks), initials: 'В', payload: { ...item, __kind: 'payout' } }))
    return [balanceRow, ...earningRows, ...payoutRows]
  }
  const items: Json[] = Array.isArray(data.items) ? data.items : []
  if (page === 'creators') return items.map((item) => {
    const name = item.display_name || item.full_name || item.email || 'Блогер'
    return row({ id: item.id, rawId: item.id, title: name,
      meta: role === 'admin' ? item.email + ' · ' + roleLabel(item.role) : item.city_country || item.content_topics || 'Профиль',
      status: statusLabel(item.status), rawStatus: item.status, tone: tone(item.status),
      date: date(item.updated_at || item.created_at), value: role === 'admin' ? roleLabel(item.role) : 'Профиль', payload: item,
    })
  })
  if (page === 'publications') return items.map((item) => {
    if (role === 'blogger') return row({ id: item.id, rawId: item.id, title: item.title,
      meta: item.product_snapshot?.publication_name || item.reported_product?.name || 'Карточка ролика',
      status: statusLabel(item.status), rawStatus: item.status, tone: tone(item.status),
      date: date(item.updated_at), value: (item.publication_summary?.total ?? 0) + ' публикаций', payload: item, thumbnailUrl: item.thumbnail_url ?? '',
    })
    const publication = item.publication ?? {}
    const creator = item.creator_display_name || item.creator_full_name || item.creator_email || 'Блогер'
    return row({ id: publication.id, rawId: publication.id,
      title: item.card_title || publication.external_title || 'Публикация',
      meta: creator + ' · ' + platformConfig(publication.platform).label,
      status: statusLabel(publication.status), rawStatus: publication.status,
      tone: tone(publication.status), date: date(publication.updated_at),
      value: publication.external_author_name || platformConfig(publication.platform).label, initials: initials(creator), payload: item, thumbnailUrl: publication.external_thumbnail_url ?? '',
    })
  })
  if (page === 'readings') return items.map((item) => {
    const platform = platformConfig(item.platform)
    return row({
      id: item.id, rawId: item.id,
      title: item.publication_title || numeric(item.reported_value) + ' просмотров',
      meta: platform.label + ' · ' + (readingSourceLabel(item.source)) + ' · период ' + date(item.reporting_period),
      status: statusLabel(item.status), rawStatus: item.status,
      tone: tone(item.status, visibleRiskFlags(item.risk_flags).length > 0), date: date(item.updated_at || item.captured_at),
      value: numeric(item.accepted_value ?? item.reported_value) + ' просмотров',
      initials: platform.shortLabel, thumbnailUrl: item.thumbnail_url ?? '', payload: item,
    })
  })
  if (page === 'finance') return items.map((item) => {
    const name = item.recipient_display_name || item.recipient_full_name || item.request_number
    return row({ id: item.request_number || item.id, rawId: item.id, title: name,
      meta: recipientLabel(item.recipient_type) + ' · ' + item.request_number,
      status: statusLabel(item.status), rawStatus: item.status,
      tone: tone(item.status, Boolean(item.is_payment_overdue)), date: date(item.updated_at || item.requested_at),
      value: rubles(item.amount_kopecks),
    })
  })
  if (page === 'exports') return items.map((item) => row({
    id: item.id, rawId: item.id, title: exportTypeLabel(item.export_type),
    meta: exportFormatLabel(item.format),
    status: statusLabel(item.status), rawStatus: item.status, tone: tone(item.status),
    date: date(item.completed_at || item.created_at), value: item.row_count == null ? '—' : numeric(item.row_count) + ' строк',
    initials: item.format === 'csv' ? 'CSV' : 'XLS',
  }))
  if (page === 'support') return items.map((item) => row({
    id: item.ticket_number || item.id, rawId: item.id, title: item.subject,
    meta: supportCategoryLabel(item.category),
    status: statusLabel(item.status), rawStatus: item.status, tone: tone(item.status),
    date: date(item.updated_at || item.last_message_at),
    value: item.assigned_to_user_id ? 'Назначено' : 'Без исполнителя', initials: 'ТП',
  }))
  if (page === 'catalog') return items.map((item) => row({ id: item.id, rawId: item.id, title: item.publication_name, meta: item.brand + ' · ' + item.sku, status: item.is_active ? 'Активен' : 'Архив', rawStatus: item.is_active ? 'active' : 'inactive', tone: item.is_active ? 'green' : 'gray', date: date(item.updated_at), value: item.required_hashtags?.length + ' хэштегов', initials: String(item.brand).slice(0, 2).toUpperCase() }))
  if (page === 'billing') return items.map((item) => row({ id: item.id, rawId: item.id, title: 'Период ' + date(item.period), meta: numeric(item.total_views) + ' просмотров', status: statusLabel(item.status), rawStatus: item.status, tone: tone(item.status), date: date(item.updated_at), value: rubles(item.total_payable_kopecks), initials: '₽' }))
  if (page === 'security') return items.map((item) => row({ id: item.id, rawId: item.id, title: securityActionLabel(item.action), meta: item.actor_email || (item.actor_role === 'lifecycle_worker' ? 'Служба контроля активности' : item.actor_role ? roleLabel(item.actor_role) : 'Системный процесс'), status: securityResultLabel(item.result), rawStatus: item.result, tone: item.result === 'success' ? 'green' : 'red', date: date(item.occurred_at), value: item.object_type ? securityObjectLabel(item.object_type) : 'Без отдельного объекта', initials: item.result === 'success' ? 'OK' : '!' }))
  if (page === 'legal') return items.map((item) => row({ id: item.id, rawId: item.id, title: item.title, meta: documentLabel(item.document_type) + ' · версия ' + item.version, status: item.is_current ? 'Действует' : 'Архив', rawStatus: item.is_current ? 'active' : 'archived', tone: item.is_current ? 'green' : 'gray', date: date(item.published_at), value: 'Редакция ' + item.revision, initials: 'Д' }))
  return []
}

function dashboardRows(payload: PagePayload | null, role: Role): Row[] {
  const data = (payload ?? {}) as Json
  if (role === 'blogger') return (data.top_video_cards ?? []).map((item: Json) => row({
    id: item.video_card_id, rawId: item.video_card_id, title: item.title, meta: item.publications + ' публикаций',
    status: 'Активно', rawStatus: 'active', tone: 'green', date: 'Всего',
    value: numeric(item.total_views) + ' просмотров', thumbnailUrl: item.thumbnail_url ?? '',
  }))
  return (data.queues ?? []).filter((item: Json) => item.count > 0).map((item: Json) => row({
    id: item.code, title: queueLabels[item.code] ?? 'Задачи требуют внимания',
    meta: 'Операционная очередь', status: item.count >= 5 ? 'Приоритет' : 'В работе',
    rawStatus: 'pending', tone: item.count >= 5 ? 'red' : 'amber',
    date: 'Сейчас', value: item.count + ' задач', initials: String(item.count),
  }))
}

function Badge({ value, color }: { value: string; color: Tone }) {
  return <span className={`badge badge--${color}`}><span className="badge__dot" />{value}</span>
}
function Avatar({ value, small = false, src }: { value: string; small?: boolean; src?: string | null }) {
  return <span className={`avatar ${small ? 'avatar--small' : ''}`}><span>{value}</span>{src ? <img src={src} alt="" onError={(event) => { event.currentTarget.hidden = true }} /> : null}</span>
}
function MediaThumb({ src, label }: { src?: string; label: string }) {
  return <span className="media-thumb"><Video size={17} />{src ? <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.hidden = true }} /> : null}<small>{label}</small></span>
}
function RequestState({ loading, error, retry }: { loading: boolean; error: string; retry: () => void }) {
  if (loading) return <div className="request-state"><LoaderCircle className="spin" size={20} />Получаем данные</div>
  if (error) return <div className="request-state request-state--error"><WifiOff size={20} /><span>{error}</span><button onClick={retry}>Повторить</button></div>
  return null
}
function Stat({ label, value, note, color, icon: Icon }: { label: string; value: string; note: string; color: string; icon: typeof UsersRound }) {
  return <article className="stat"><div className={`stat__icon stat__icon--${color}`}><Icon size={19} /></div><div className="stat__body"><span>{label}</span><strong>{value}</strong><small>{note}</small></div></article>
}

function Sidebar({ page, user, identity, open, navigate, close, signOut, settings }: {
  page: PageId; user: CurrentUser; identity?: Json | null; open: boolean; navigate: (page: PageId) => void;
  close: () => void; signOut: () => void; settings: () => void;
}) {
  const identityName = identity?.full_name || identity?.display_name || user.email
  const groups = navGroups.map((group) => ({ ...group,
    items: group.items.filter((item) => rolePages[user.role].includes(item.id)).map((item) =>
      user.role === 'blogger' && item.id === 'publications' ? { ...item, label: 'Мои ролики' } : item),
  })).filter((group) => group.items.length)
  return <>
    <aside className={`sidebar ${open ? 'sidebar--open' : ''}`}>
      <div className="brand"><div className="brand__mark"><span>A</span></div><div><strong>AMP</strong><span>Content Factory</span></div><button className="icon-button sidebar__close" onClick={close}><X size={19} /></button></div>
      <nav className="nav">{groups.map((group) => <div className="nav__group" key={group.label}><div className="nav__label">{group.label}</div>{group.items.map((item) => {
        const Icon = item.icon
        return <button className={`nav__item ${page === item.id ? 'nav__item--active' : ''}`} key={item.id} onClick={() => { navigate(item.id); close() }}><Icon size={18} /><span>{item.label}</span></button>
      })}</div>)}</nav>
      <div className="sidebar__bottom"><button className="nav__item" onClick={() => { settings(); close() }}><Settings size={18} /><span>Личный кабинет</span></button><div className="profile-chip"><Avatar value={initials(identityName)} src={identity?.avatar_url} small /><span><strong>{identityName}</strong><small>{roleLabels[user.role]}</small></span><button className="profile-logout" onClick={signOut} title="Выйти"><LogOut size={16} /></button></div></div>
    </aside>
    {open ? <button className="sidebar-backdrop" onClick={close} /> : null}
  </>
}
function Header({ menu, query, setQuery, navigate, supportAvailable }: { menu: () => void; query: string; setQuery: (value: string) => void; navigate: (page: PageId) => void; supportAvailable: boolean }) {
  return <header className="topbar"><button className="icon-button menu-button" onClick={menu}><Menu size={21} /></button><div className="global-search"><Search size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти в текущем разделе" /><kbd>Ctrl K</kbd></div><div className="topbar__actions"><NotificationMenu navigate={navigate} />{supportAvailable ? <button className="help-button" onClick={() => navigate('support')}><HelpCircle size={18} /><span>Помощь</span></button> : null}</div></header>
}

function Table({ rows, query, page, action, cardAction }: { rows: Row[]; query: string; page: PageId; action: (row: Row) => void; cardAction?: (row: Row) => void }) {
  const filtered = rows.filter((item) => (item.title + ' ' + item.meta + ' ' + item.id).toLowerCase().includes(query.toLowerCase()))
  const creatorOverview = page === 'overview' && rows.some((item) => item.thumbnailUrl !== undefined)
  const columns: Partial<Record<PageId, [string, string]>> = {
    publications: ['Ролик', 'Публикации'], creators: ['Блогер', 'Профиль'],
    finance: ['Операция', 'Сумма'], readings: ['Показание', 'Просмотры'],
    support: ['Обращение', 'Ответственный'], exports: ['Выгрузка', 'Объём'],
    billing: ['Расчётный период', 'К выплате'], security: ['Событие', 'Объект'],
    legal: ['Документ', 'Редакция'], moderation: ['Заявка', 'Данные'],
  }
  const [nameLabel, valueLabel] = columns[page] ?? ['Название', 'Показатель']
  if (page === 'catalog' && filtered.length) return <div className="catalog-grid">{filtered.map((item) => <button className="catalog-tile" key={item.id} onClick={() => action(item)}><span className="catalog-tile__head"><Package size={30} /><Badge value={item.status} color={item.tone} /></span><strong>{item.title}</strong><small>{item.meta}</small><span className="catalog-tile__foot"><span>Требования к публикации</span><ArrowRight size={18} /></span></button>)}</div>
  return <div className="table-wrap"><table><thead><tr><th>{nameLabel}</th>{creatorOverview ? null : <><th>Статус</th><th>Дата</th></>}<th>{valueLabel}</th><th className="row-action-column"><span className="sr-only">Действие</span></th></tr></thead><tbody>{filtered.map((item) => <tr key={item.id} onClick={() => action(item)}><td><div className="entity">{item.thumbnailUrl !== undefined ? <MediaThumb src={item.thumbnailUrl} label={item.initials} /> : page === 'creators' || (page === 'moderation' && !item.id.startsWith('publication-')) ? <Avatar value={item.initials} /> : null}<div><strong>{item.title}</strong><span>{item.meta}</span></div></div></td>{creatorOverview ? null : <><td><Badge value={item.status} color={item.tone} /></td><td className="muted">{item.date}</td></>}<td><strong className="cell-value">{item.value}</strong></td><td className="row-action-cell"><button className="row-action" onClick={(event) => { event.stopPropagation(); (cardAction ?? action)(item) }} title={"Открыть полную карточку: " + item.title} aria-label={"Открыть полную карточку: " + item.title}>{page === 'exports' && item.rawStatus === 'ready' ? <Download size={16} /> : <ArrowRight size={18} />}</button></td></tr>)}</tbody></table>{!filtered.length ? <div className="empty"><Search size={24} /><strong>Данных пока нет</strong><span>{query ? 'Измените поисковый запрос' : 'Здесь пока ничего нет'}</span></div> : null}</div>}

function Overview({ data, role, query, loading, error, reload, openVideoCard, openQueue }: {
  data: PagePayload | null; role: Role; query: string; loading: boolean; error: string; reload: () => void; openVideoCard: (id: string) => void; openQueue: (item: Row) => void;
}) {
  const source = (data ?? {}) as Json
  const overview = source.overview ?? {}
  const blogger = role === 'blogger'
  const rows = dashboardRows(data, role)
  const stats = blogger ? [
    ['Активные ролики', numeric(source.content?.active_video_cards), (source.content?.active_publications ?? 0) + ' публикаций', 'green', Video],
    ['Просмотры всего', numeric(source.views?.total_views), '+' + numeric(source.views?.current_period_new_views) + ' за период', 'blue', Eye],
    ['Доступный баланс', rubles(source.finance?.available_kopecks), rubles(source.finance?.reserved_kopecks) + ' зарезервировано', 'amber', WalletCards],
    ['Выплачено', rubles(source.finance?.paid_kopecks), 'За всё время', 'red', CircleDollarSign],
  ] : [
    ['Активные блогеры', numeric(overview.active_bloggers), (overview.new_applications ?? 0) + ' новых заявок', 'green', UsersRound],
    ['Публикации', numeric(overview.active_publications), 'Активные ссылки', 'blue', Video],
    ['Предварительно', rubles(overview.preliminary_accrual_kopecks), 'Начисления периода', 'amber', Gauge],
    ['Выплаты в работе', rubles(overview.payouts_in_progress_kopecks), rubles(overview.available_balance_kopecks) + ' доступно', 'red', CircleDollarSign],
  ]
  return <>
    <PageHeading title="Рабочий стол" subtitle={blogger ? 'Результаты контента и ближайшие действия.' : 'Операционные показатели и очереди команды.'} role={role} loading={loading} reload={reload} />
    <RequestState loading={loading} error={error} retry={reload} />
    {!error && !loading ? <>
      <section className="stats-grid">{stats.map(([label, value, note, color, icon]) => <Stat key={label as string} label={label as string} value={value as string} note={note as string} color={color as string} icon={icon as typeof UsersRound} />)}</section>
      <div className={`content-grid ${blogger ? 'content-grid--single' : ''}`}>
        <section className="panel queue-panel">
          <div className="panel__head"><div><h2>{blogger ? 'Лучшие ролики' : 'Очередь работы'}</h2>{blogger ? null : <p>Актуальные задачи команды</p>}</div><Badge value={String(rows.length)} color="blue" /></div>
          <Table rows={rows} query={query} page="overview" action={(item) => { if (blogger && item.rawId) openVideoCard(item.rawId); else if (!blogger) openQueue(item) }} />
        </section>
        {!blogger ? <aside className="attention"><div className="attention__head"><div><AlertTriangle size={18} /><h2>Требует внимания</h2></div></div><div className="attention-item attention-item--static"><span className="risk-icon risk-icon--amber"><Gauge size={17} /></span><span><strong>{rows.length} активных очередей</strong><small>Ожидают обработки</small></span></div></aside> : null}
      </div>
    </> : null}
  </>
}
function PageHeading({ title, subtitle, role, loading, reload, actionLabel, action, secondaryLabel, secondaryAction }: {
  title: string; subtitle: string; role: Role; loading: boolean; reload: () => void; actionLabel?: string; action?: () => void; secondaryLabel?: string; secondaryAction?: () => void;
}) {
  return <div className="page-heading"><div><p className="eyebrow">{roleLabels[role]}</p><h1>{title}</h1><p>{subtitle}</p></div><div className="heading-actions">{secondaryLabel && secondaryAction ? <button className="button button--secondary" onClick={secondaryAction}><WalletCards size={17} />{secondaryLabel}</button> : null}{actionLabel && action ? <button className="button button--primary" onClick={action}><Plus size={17} />{actionLabel}</button> : null}<button className="button button--secondary refresh-button" onClick={reload} disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={17} />Обновить</button></div></div>
}
function ListPage({ page, role, rows, query, loading, error, reload, action, cardAction, primaryLabel, primaryAction, secondaryLabel, secondaryAction, pageNumber, totalPages, totalItems, setPageNumber, filters, setFilters }: {
  page: PageId; role: Role; rows: Row[]; query: string; loading: boolean; error: string; reload: () => void; action: (row: Row) => void; cardAction?: (row: Row) => void; primaryLabel?: string; primaryAction?: () => void; secondaryLabel?: string; secondaryAction?: () => void; pageNumber: number; totalPages: number; totalItems: number; setPageNumber: (page: number) => void; filters: PageFilters; setFilters: (filters: PageFilters) => void;
}) {
  const [title, subtitle] = pageMeta[page]
  const [status, setStatus] = useState('')
  const [platform, setPlatform] = useState('')
  const [riskOnly, setRiskOnly] = useState(false)
  const [sort, setSort] = useState('newest')
  const remotePublicationFilters = page === 'publications' && role !== 'blogger'
  const platformFor = (item: Row) => item.payload?.publication?.platform ?? item.payload?.account?.platform ?? item.payload?.platform ?? ''
  const riskFor = (item: Row) => visibleRiskFlags(item.payload?.risk_flags).length > 0
  useEffect(() => { setStatus(''); setPlatform(''); setRiskOnly(false); setSort('newest') }, [page])
  const statuses = remotePublicationFilters
    ? Object.entries(statusLabels).filter(([id]) => ['draft', 'pending_review', 'changes_required', 'approved', 'rejected', 'inactive', 're_review_required'].includes(id))
    : Array.from(new Map(rows.map((item) => [item.rawStatus, item.status])).entries()).filter(([id]) => id)
  const pagePlatforms = remotePublicationFilters ? platformIds : Array.from(new Set(rows.map(platformFor).filter(Boolean)))
  const selectedStatus = remotePublicationFilters ? filters.status ?? '' : status
  const selectedPlatform = remotePublicationFilters ? filters.platform ?? '' : platform
  const changeFilter = (name: string, value: string) => {
    if (!remotePublicationFilters) {
      if (name === 'status') setStatus(value)
      if (name === 'platform') setPlatform(value)
      return
    }
    setPageNumber(1); setFilters({ ...filters, [name]: value })
  }
  const visibleRows = remotePublicationFilters ? [...rows] : rows.filter((item) => (!status || item.rawStatus === status) && (!platform || platformFor(item) === platform) && (!riskOnly || riskFor(item)))
  if (sort === 'oldest') visibleRows.reverse()
  if (sort === 'title') visibleRows.sort((left, right) => left.title.localeCompare(right.title, 'ru'))
  const applyAdvanced = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const next = { ...filters }
    for (const name of ['blogger', 'brand', 'product', 'dateFrom', 'dateTo', 'reason']) next[name] = String(form.get(name) ?? '').trim()
    setPageNumber(1); setFilters(next)
  }
  return <><PageHeading title={role === 'blogger' && page === 'publications' ? 'Мои ролики' : title} subtitle={subtitle} role={role} loading={loading} reload={reload} actionLabel={primaryLabel} action={primaryAction} secondaryLabel={secondaryLabel} secondaryAction={secondaryAction} /><RequestState loading={loading} error={error} retry={reload} />{!error && !loading ? <section className="panel list-panel"><div className="list-toolbar"><div className="local-search"><Search size={17} /><span>{query ? 'Поиск: ' + query : 'Всего записей: ' + totalItems}</span></div><label className="toolbar-select"><SlidersHorizontal size={16} /><span className="sr-only">Статус</span><select value={selectedStatus} onChange={(event) => changeFilter('status', event.target.value)}><option value="">Все статусы</option>{statuses.map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select></label>{pagePlatforms.length ? <label className="toolbar-select"><span className="sr-only">Площадка</span><select value={selectedPlatform} onChange={(event) => changeFilter('platform', event.target.value)}><option value="">Все площадки</option>{pagePlatforms.map((id) => <option value={id} key={id}>{platformConfig(id).label}</option>)}</select></label> : null}{page === 'readings' && rows.some(riskFor) ? <label className="toolbar-check"><input type="checkbox" checked={riskOnly} onChange={(event) => setRiskOnly(event.target.checked)} /><span>Только с риском</span></label> : null}<label className="toolbar-select"><span className="sr-only">Сортировка</span><select value={sort} onChange={(event) => setSort(event.target.value)}><option value="newest">Сначала новые</option><option value="oldest">Сначала старые</option><option value="title">По названию</option></select></label></div>
    {remotePublicationFilters ? <form className="advanced-filters" onSubmit={applyAdvanced} key={JSON.stringify(filters)}><input name="blogger" defaultValue={filters.blogger ?? ''} placeholder="Блогер или email" /><select name="brand" defaultValue={filters.brand ?? ''}><option value="">Все бренды</option><option value="AMP">AMP</option><option value="AirTone">AirTone</option><option value="CrioLight">CrioLight</option></select><input name="product" defaultValue={filters.product ?? ''} placeholder="Товар, модель или SKU" /><label><span>С</span><input name="dateFrom" type="date" defaultValue={filters.dateFrom ?? ''} /></label><label><span>По</span><input name="dateTo" type="date" defaultValue={filters.dateTo ?? ''} /></label><input name="reason" defaultValue={filters.reason ?? ''} placeholder="Причина решения" /><button className="button button--secondary"><SlidersHorizontal size={16} />Применить</button><button type="button" className="text-button" onClick={() => { setPageNumber(1); setFilters({}) }}>Сбросить</button></form> : null}
    {page === 'finance' && role === 'blogger' && rows.find((item) => item.id === 'balance') ? <section className="list-finance"><span><small>Доступно к выплате</small><strong>{rows.find((item) => item.id === 'balance')?.value}</strong></span><p>{rows.find((item) => item.id === 'balance')?.meta}</p></section> : null}
    <Table rows={page === 'finance' && role === 'blogger' ? visibleRows.filter((item) => item.id !== 'balance') : visibleRows} query={query} page={page} action={action} cardAction={cardAction} /><div className="pagination"><span>Страница {pageNumber} из {totalPages || 1}</span><div><button disabled={pageNumber <= 1 || loading} onClick={() => setPageNumber(pageNumber - 1)}>Назад</button><button disabled={pageNumber >= totalPages || loading} onClick={() => setPageNumber(pageNumber + 1)}>Дальше</button></div></div></section> : null}</>
}
type WorkKind = 'content' | 'reading' | 'moderate-profile' | 'moderate-social' | 'moderate-publication' | 'moderate-reading' | 'correct-reading'
type WorkState = { kind: WorkKind; item?: Row } | null

const workTitles: Record<WorkKind, [string, string]> = {
  content: ['Новый ролик', 'Карточка и ссылка на публикацию'],
  reading: ['Добавить показание', 'Ручное значение просмотров'],
  'moderate-profile': ['Решение по профилю', 'Проверка данных блогера'],
  'moderate-social': ['Решение по площадке', 'Проверка социального аккаунта'],
  'moderate-publication': ['Решение по публикации', 'Проверка ссылки и материала'],
  'moderate-reading': ['Решение по показанию', 'Проверка значения просмотров'],
  'correct-reading': ['Исправить показание', 'Корректировка уже принятого значения'],
}

function Field({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  return <label className={wide ? 'form-field form-field--wide' : 'form-field'}><span>{label}</span>{children}</label>
}

function WorkDrawer({ state, role, close, completed }: {
  state: Exclude<WorkState, null>; role: Role; close: () => void; completed: (message: string) => void;
}) {
  const [workspace, setWorkspace] = useState<Json | null>(null)
  const [loading, setLoading] = useState(['content', 'reading'].includes(state.kind))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [decision, setDecision] = useState(
    state.kind === 'moderate-reading' ? 'accept'
      : state.kind === 'correct-reading' ? 'correct'
      : state.kind === 'moderate-profile' && state.item?.rawStatus === 'submitted' ? 'start_review'
      : 'approve',
  )
  const [productMode, setProductMode] = useState('catalog')
  const [contentAccountId, setContentAccountId] = useState('')
  const [moderationWorkspace, setModerationWorkspace] = useState<Json | null>(null)
  useEffect(() => {
    if (!['content', 'reading'].includes(state.kind)) return
    loadCreatorWorkspace(state.kind as 'content' | 'reading').then(setWorkspace).catch((caught) => {
      setError(caught instanceof ApiError ? caught.message : 'Не удалось получить данные')
    }).finally(() => setLoading(false))
  }, [state.kind])
  useEffect(() => {
    if (state.kind !== 'moderate-publication' || !state.item?.rawId) return
    setLoading(true)
    loadPublicationModerationWorkspace(state.item.rawId)
      .then(setModerationWorkspace)
      .catch((caught) => setError(caught instanceof ApiError ? caught.message : 'Не удалось загрузить публикацию'))
      .finally(() => setLoading(false))
  }, [state.kind, state.item?.rawId])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setPending(true); setError('')
    const form = new FormData(event.currentTarget)
    const value = (name: string) => String(form.get(name) ?? '').trim()
    const submitAction = ((event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null)?.value
    try {
      if (state.kind === 'content') {
        const card = await createVideoCard({
          title: value('title'), description: value('description') || null,
          product: productMode === 'catalog'
            ? { type: 'catalog', product_id: value('product_id') }
            : { type: 'unlisted', brand: value('brand'), name: value('product_name') },
        })
        if (value('publication_url')) {
          if (!value('social_account_id')) throw new Error('Добавьте или выберите социальный аккаунт')
          const publication = await createPublication(card.id, {
            social_account_id: value('social_account_id'), url: value('publication_url'),
          })
          await submitPublication(publication.publication?.id ?? publication.id)
        }
        return completed(value('publication_url') ? 'Ролик отправлен на модерацию' : 'Карточка ролика создана')
      }
      if (state.kind === 'reading') {
        await createManualReading(value('publication_id'), Number(value('reading_value')))
        return completed('Показание добавлено')
      }
      const reason = value('reason')
      if (state.kind === 'moderate-profile') await reviewProfile(state.item!.rawId!, decision, reason)
      else if (state.kind === 'moderate-social') await reviewSocialAccount(state.item!.rawId!, decision, reason)
      else if (state.kind === 'moderate-publication') {
        if (submitAction === 'promo') {
          await recordPromoIssuance(state.item!.rawId!, value('marketplace'), value('promo_note'))
          return completed('Выдача промокода зафиксирована')
        }
        if (submitAction === 'deactivate') {
          if (reason.length < 3) throw new Error('Укажите причину деактивации')
          if (!window.confirm('Деактивировать публикацию? Новые просмотры и начисления будут остановлены.')) return
          await deactivatePublication(state.item!.rawId!, reason)
          return completed('Публикация деактивирована')
        }
        await reviewPublication(state.item!.rawId!, decision, reason, value('resolved_product_id') || undefined)
      }
      else if (state.kind === 'correct-reading') await correctReading(state.item!.rawId!, Number(value('accepted_value')), reason)
      else await reviewReading(state.item!.rawId!, decision, reason, value('accepted_value') ? Number(value('accepted_value')) : undefined)
      completed('Решение сохранено')
    } catch (caught) {
      setError(caught instanceof ApiError || caught instanceof Error ? caught.message : 'Не удалось сохранить изменения')
    } finally { setPending(false) }
  }

  const [title, subtitle] = workTitles[state.kind]
  const accounts: Json[] = workspace?.socialAccounts ?? []
  const products: Json[] = workspace?.products ?? []
  const publications: Json[] = (workspace?.publications ?? []).filter((item: Json) => item.status === 'approved')
  const manualPublications = publications.filter((item) => !platformConfig(item.platform).automaticReadings)
  const contentAccount = accounts.find((item) => item.id === contentAccountId)
  const contentPlatform = platformConfig(contentAccount?.platform)
  const moderation = state.kind.startsWith('moderate-') || state.kind === 'correct-reading'
  const negative = ['reject', 'request_changes', 'suspend', 'block', 'correct'].includes(decision)
  const itemData = moderationWorkspace?.detail ?? state.item?.payload ?? {}
  const publicationData = itemData.publication ?? itemData
  const moderationProducts: Json[] = moderationWorkspace?.products ?? []
  const publicationOperational = state.kind === 'moderate-publication' && publicationData.status === 'approved'
  const promoIssuances: Json[] = itemData.promo_issuances ?? []
  const rejectionReasons: string[] = moderationWorkspace?.programSettings?.rejection_reasons ?? []
  return <div className="drawer-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}>
    <aside className="work-drawer" role="dialog" aria-modal="true" aria-labelledby="work-title">
      <header className="work-drawer__head"><div><h2 id="work-title">{title}</h2><p>{subtitle}</p></div><button type="button" className="icon-button" onClick={close} title="Закрыть"><ArrowLeft size={19} /></button></header>
      {loading ? <div className="drawer-loading"><LoaderCircle className="spin" size={20} />Загружаем данные</div> :
      <form className="work-form" onSubmit={submit}>
        {state.item ? <div className="drawer-entity"><span><strong>{state.item.title}</strong><small>{state.item.meta}</small></span><Badge value={state.item.status} color={state.item.tone} /></div> : null}
        {state.kind === 'moderate-profile' ? <section className="form-section"><h3>Данные профиля</h3><div className="export-details"><span><small>ФИО</small>{itemData.full_name || '—'}</span><span><small>Публичное имя</small>{itemData.display_name || '—'}</span><span><small>Телефон</small>{itemData.phone || '—'}</span><span><small>Telegram</small>{itemData.telegram || '—'}</span><span><small>Город и страна</small>{itemData.city_country || '—'}</span><span><small>Получатель</small>{recipientLabel(itemData.recipient_status)}</span></div>{itemData.content_topics ? <p className="entity-copy">{itemData.content_topics}</p> : null}</section> : null}
        {state.kind === 'moderate-social' ? <section className="form-section"><h3>Социальный аккаунт</h3><div className="export-details"><span><small>Площадка</small>{platformConfig(itemData.account?.platform).label}</span><span><small>Подписчики</small>{numeric(itemData.account?.follower_count)}</span><span><small>Блогер</small>{itemData.creator_display_name || itemData.creator_full_name || '—'}</span><span><small>Текущий статус</small>{statusLabel(itemData.account?.status, '—')}</span></div>{itemData.account?.url ? <a className="entity-link" href={itemData.account.url} target="_blank" rel="noreferrer"><Link2 size={15} />Открыть площадку</a> : null}</section> : null}
        {state.kind === 'moderate-publication' ? <section className="form-section"><h3>Материал</h3><div className="export-details"><span><small>Площадка</small>{platformConfig(publicationData.platform).label}</span><span><small>Автор на площадке</small>{publicationData.external_author_name || '—'}</span><span><small>Заголовок</small>{publicationData.external_title || itemData.card_title || '—'}</span><span><small>Доступность</small>{availabilityLabel(publicationData.availability)}</span><span><small>Получение данных</small>{enrichmentLabel(publicationData.enrichment_status)}</span><span><small>Просмотры</small>{platformConfig(publicationData.platform).automaticReadings ? 'Автоматически' : 'Вручную'}</span></div>{publicationData.enrichment_error_code ? <div className="integration-error"><AlertTriangle size={16} /><span><b>Не удалось получить данные</b>{integrationErrorLabel(publicationData.enrichment_error_code)}</span></div> : null}{publicationData.submitted_url ? <a className="entity-link" href={publicationData.submitted_url} target="_blank" rel="noreferrer"><Link2 size={15} />Открыть публикацию</a> : null}</section> : null}
        {['moderate-reading', 'correct-reading'].includes(state.kind) ? <section className="form-section"><h3>Данные показания</h3><div className="export-details"><span><small>Заявлено</small>{numeric(itemData.reported_value)}</span><span><small>Принято</small>{itemData.accepted_value == null ? '—' : numeric(itemData.accepted_value)}</span><span><small>Источник</small>{readingSourceLabel(itemData.source)}</span><span><small>Период</small>{date(itemData.reporting_period)}</span></div>{visibleRiskFlags(itemData.risk_flags).length ? <div className="payout-warning"><AlertTriangle size={16} /><span><b>Требуется проверка</b>{visibleRiskFlags(itemData.risk_flags).map(riskFlagLabel).join(', ')}</span></div> : null}</section> : null}
        {state.kind === 'content' ? <>
          <section className="form-section"><h3>Карточка ролика</h3><div className="form-grid">
            <Field label="Название" wide><input name="title" required maxLength={255} /></Field>
            <Field label="Источник товара" wide><select value={productMode} onChange={(event) => setProductMode(event.target.value)}><option value="catalog">Выбрать из каталога</option><option value="unlisted">Товара нет в каталоге</option></select></Field>
            {productMode === 'catalog' ? <Field label="Товар" wide><select name="product_id" required defaultValue=""><option value="" disabled>Выберите товар</option>{products.map((item) => <option value={item.id} key={item.id}>{item.brand} · {item.publication_name} · {item.sku}</option>)}</select></Field> : <><Field label="Бренд"><select name="brand"><option value="AMP">AMP</option><option value="AirTone">AirTone</option><option value="CrioLight">CrioLight</option></select></Field><Field label="Название товара"><input name="product_name" required /></Field></>}
            <Field label="Описание" wide><textarea name="description" rows={4} /></Field>
          </div></section>
          <section className="form-section"><h3>Публикация</h3><p className="form-hint">Ссылку можно оставить пустой и сохранить только карточку.</p><div className="form-grid">
            <Field label="Социальный аккаунт" wide><select name="social_account_id" value={contentAccountId} onChange={(event) => setContentAccountId(event.target.value)}><option value="">Выберите аккаунт</option>{accounts.filter((item) => item.status === 'approved').map((item) => <option value={item.id} key={item.id}>{platformConfig(item.platform).label}: {item.url}</option>)}</select></Field>
            <Field label="Ссылка на публикацию" wide><input name="publication_url" type="url" placeholder={contentAccount ? contentPlatform.publicationPlaceholder : 'Сначала выберите площадку'} disabled={!contentAccount} /></Field>
            {contentAccount ? <div className="collection-mode form-field--wide"><span className={contentPlatform.automaticReadings ? 'collection-mode__automatic' : ''}>{contentPlatform.automaticReadings ? 'Автоматический сбор просмотров' : 'Ручная передача просмотров'}</span><small>{contentPlatform.label}</small></div> : null}
          </div></section>
        </> : null}
        {state.kind === 'reading' ? <section className="form-section"><h3>Данные площадки</h3><div className="form-grid">
          <Field label="Публикация" wide><select name="publication_id" required defaultValue=""><option value="" disabled>Выберите одобренную публикацию</option>{manualPublications.map((item) => <option value={item.id} key={item.id}>{platformConfig(item.platform).label}: {item.external_title || item.submitted_url}</option>)}</select></Field>
          <Field label="Текущее число просмотров" wide><input name="reading_value" type="number" min="0" required /></Field>
        </div>{!manualPublications.length ? <div className="inline-note">Для YouTube, TikTok, VK и RUTUBE просмотры собираются автоматически. Ручное показание доступно только для остальных одобренных публикаций.</div> : null}</section> : null}
        {publicationOperational ? <section className="form-section"><h3><ClipboardCheck size={16} />Промокоды</h3>
          {promoIssuances.length ? <div className="account-list">{promoIssuances.map((issuance) => <span key={issuance.id}><b>{issuance.marketplace === 'ozon' ? 'OZ' : issuance.marketplace === 'wildberries' ? 'WB' : 'YM'}</b><em>{issuance.marketplace === 'ozon' ? 'Ozon' : issuance.marketplace === 'wildberries' ? 'Wildberries' : 'Яндекс Маркет'} · {date(issuance.issued_at)}</em><Badge value="Выдан" color="green" /></span>)}</div> : <div className="inline-note">Выдача промокодов ещё не фиксировалась.</div>}
          <div className="decision-grid"><Field label="Маркетплейс" wide><select name="marketplace" defaultValue="ozon"><option value="ozon">Ozon</option><option value="wildberries">Wildberries</option><option value="yandex_market">Яндекс Маркет</option></select></Field><Field label="Служебный комментарий" wide><textarea name="promo_note" rows={3} placeholder="Например: передан блогеру в Telegram" /></Field><button className="button button--secondary form-field--wide" name="action" value="promo" disabled={pending}><Check size={17} />Зафиксировать выдачу</button></div>
        </section> : null}
        {publicationOperational && ['manager', 'admin'].includes(role) ? <section className="form-section danger-section"><h3><AlertTriangle size={16} />Деактивация</h3><p className="form-hint">Публикация останется в истории, но перестанет участвовать в сборе новых просмотров и начислениях.</p><Field label="Причина деактивации" wide><textarea name="reason" rows={4} minLength={3} /></Field><button className="button button--danger" name="action" value="deactivate" disabled={pending}><Trash2 size={17} />Деактивировать публикацию</button></section> : null}
        {moderation && !publicationOperational ? <section className="form-section"><h3><ClipboardCheck size={16} />Решение</h3><div className="decision-grid">
          {state.kind === 'moderate-profile' ? <select value={decision} onChange={(e) => setDecision(e.target.value)}><option value="approve">Одобрить</option><option value="start_review">Взять в работу</option><option value="reject">Отклонить</option><option value="suspend">Приостановить</option></select> : null}
          {state.kind === 'moderate-social' ? <select value={decision} onChange={(e) => setDecision(e.target.value)}><option value="approve">Одобрить</option><option value="reject">Отклонить</option></select> : null}
          {state.kind === 'moderate-publication' ? <>
            <select value={decision} onChange={(e) => setDecision(e.target.value)}><option value="approve">Одобрить</option><option value="request_changes">Вернуть на исправление</option><option value="reject">Отклонить</option></select>
            {decision === 'approve' && itemData.card && !itemData.card.is_product_resolved ? <Field label="Привязать товар" wide><select name="resolved_product_id" required defaultValue=""><option value="" disabled>Выберите товар из каталога</option>{moderationProducts.map((item) => <option value={item.id} key={item.id}>{item.brand} · {item.publication_name} · {item.sku}</option>)}</select></Field> : null}
            {decision === 'approve' ? <div className="moderation-checklist form-field--wide"><span>Проверено перед одобрением</span><label><input type="checkbox" required />Товар и артикул соответствуют ролику</label><label><input type="checkbox" required />Формат и содержание соответствуют правилам</label><label><input type="checkbox" required />Название и обязательные хэштеги корректны</label></div> : null}
          </> : null}
          {state.kind === 'moderate-reading' ? <select value={decision} onChange={(e) => setDecision(e.target.value)}><option value="accept">Принять</option><option value="correct">Исправить</option><option value="reject">Отклонить</option></select> : null}
          {state.kind === 'correct-reading' ? <input type="hidden" value="correct" /> : null}
          {decision === 'correct' ? <Field label="Принятое значение"><input name="accepted_value" type="number" min="0" required /></Field> : null}
          <Field label={negative ? 'Причина (обязательно)' : 'Комментарий'} wide>{negative && rejectionReasons.length ? <><input name="reason" list="rejection-reasons" required /><datalist id="rejection-reasons">{rejectionReasons.map((reason) => <option value={reason} key={reason} />)}</datalist></> : <textarea name="reason" rows={4} required={negative} />}</Field>
        </div></section> : null}
        {error ? <div className="auth-error"><AlertTriangle size={16} />{error}</div> : null}
        <footer className="work-form__actions">
          <button type="button" className="button button--secondary" onClick={close}>Отмена</button>
          {!publicationOperational ? <button className="button button--primary" disabled={pending || (state.kind === 'reading' && !manualPublications.length)}>
            {pending ? <LoaderCircle className="spin" size={17} /> : moderation ? <ClipboardCheck size={17} /> : <Save size={17} />}
            {pending ? 'Сохраняем' : moderation ? 'Сохранить решение' : 'Сохранить'}
          </button> : null}
        </footer>
      </form>}
    </aside>
  </div>
}
function Cabinet({ user, signedOut }: { user: CurrentUser; signedOut: () => void }) {
  const allowed = rolePages[user.role]
  const hash = window.location.hash.slice(1) as PageId
  const [page, setPage] = useState<PageId>(allowed.includes(hash) ? hash : allowed[0])
  const [menu, setMenu] = useState(false); const [query, setQuery] = useState('')
  const [data, setData] = useState<PagePayload | null>(null); const [loading, setLoading] = useState(true)
  const [error, setError] = useState(''); const [reloadKey, setReloadKey] = useState(0)
  const [pageNumber, setPageNumber] = useState(1)
  const [filters, setFilters] = useState<PageFilters>({})
  const [toast, setToast] = useState(''); const [work, setWork] = useState<WorkState>(null)
  const [payout, setPayout] = useState<{ mode: PayoutMode; id?: string } | null>(null)
  const [earningPeriod, setEarningPeriod] = useState<string | null>(null)
  const [support, setSupport] = useState<{ mode: 'create' | 'details'; id?: string } | null>(null)
  const [exportJob, setExportJob] = useState<{ id?: string } | null>(null)
  const [adminUser, setAdminUser] = useState<string | null>(null)
  const [bloggerCard, setBloggerCard] = useState<string | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [operation, setOperation] = useState<{ kind: OperationKind; id?: string } | null>(null)
  const [entity, setEntity] = useState<{ kind: 'video-card' | 'reading'; id: string; payload?: Json } | null>(null)
  const [creatorIdentity, setCreatorIdentity] = useState<Json | null>(null)
  const refreshCreatorIdentity = async () => {
    if (user.role !== 'blogger') return
    try { setCreatorIdentity(await loadCreatorIdentity()) }
    catch { setCreatorIdentity(null) }
  }
  useEffect(() => { void refreshCreatorIdentity() }, [user.role])
  useEffect(() => {
    if (user.role === 'blogger') return
    let timer = window.setTimeout(() => { void logout().finally(signedOut) }, 30 * 60 * 1000)
    const resetIdleTimer = () => {
      window.clearTimeout(timer)
      timer = window.setTimeout(() => { void logout().finally(signedOut) }, 30 * 60 * 1000)
    }
    const events = ['pointerdown', 'keydown', 'touchstart', 'scroll'] as const
    events.forEach((event) => window.addEventListener(event, resetIdleTimer, { passive: true }))
    return () => {
      window.clearTimeout(timer)
      events.forEach((event) => window.removeEventListener(event, resetIdleTimer))
    }
  }, [user.role, signedOut])
  useEffect(() => {
    let cancelled = false; setLoading(true); setError('')
    loadPage(user.role, page, pageNumber, filters).then((value) => { if (!cancelled) setData(value) }).catch((caught) => {
      if (cancelled) return
      if (caught instanceof ApiError && caught.status === 401) return signedOut()
      setData(null); setError(caught instanceof ApiError ? caught.message : 'Не удалось загрузить данные')
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [page, pageNumber, filters, reloadKey, user.role, signedOut])
  const rows = useMemo(() => rowsFrom(data, page, user.role), [data, page, user.role])
  const reload = () => runTransition(() => setReloadKey((value) => value + 1))
  const navigate = (next: PageId, nextFilters: PageFilters = {}) => { if (!allowed.includes(next)) return; runTransition(() => { setPage(next); setPageNumber(1); setFilters(nextFilters); setData(null); setQuery(''); window.history.replaceState(null, '', '#' + next) }) }
  const notify = (value: string) => { setToast(value); window.setTimeout(() => setToast(''), 2400) }
  const openDashboardQueue = (item: Row) => {
    const target = queueTarget(item.id)
    if (target.page === 'overview' || !allowed.includes(target.page)) return notify('Для этой очереди пока нет отдельного экрана')
    navigate(target.page, target.filters)
  }
  const completeWork = (message: string) => { runTransition(() => setWork(null)); notify(message); reload() }
  const action = async (item: Row) => {
    if (page === 'exports' && item.rawId) return setExportJob({ id: item.rawId })
    if (!item.rawId) return notify(item.title)
    if (page === 'finance' && item.payload?.__kind === 'earning') return setEarningPeriod(item.rawId)
    if (page === 'finance') return setPayout({ mode: 'details', id: item.rawId })
    if (page === 'support') return setSupport({ mode: 'details', id: item.rawId })
    if (page === 'creators' && user.role === 'admin') {
      return item.payload?.role === 'blogger' ? setBloggerCard(item.rawId) : setAdminUser(item.rawId)
    }
    if (page === 'catalog') return setOperation({ kind: 'product', id: item.rawId })
    if (page === 'billing') return setOperation({ kind: 'period', id: item.rawId })
    if (page === 'security') return setOperation({ kind: 'security', id: item.rawId })
    if (page === 'legal') return setOperation({ kind: 'legal', id: item.rawId })
    if (page === 'publications' && user.role === 'blogger') return setEntity({ kind: 'video-card', id: item.rawId, payload: item.payload })
    if (page === 'moderation') return setWork({ kind: item.id.startsWith('profile-') ? 'moderate-profile' : item.id.startsWith('social-') ? 'moderate-social' : 'moderate-publication', item })
    if (page === 'creators' && user.role === 'moderator') return setBloggerCard(item.payload?.user_id ?? item.rawId)
    if (page === 'publications' && ['moderator', 'manager', 'admin'].includes(user.role)) return setWork({ kind: 'moderate-publication', item })
    if (page === 'readings' && user.role !== 'blogger') {
      if (item.rawStatus === 'accepted' && ['manager', 'admin'].includes(user.role)) return setWork({ kind: 'correct-reading', item })
      if (item.rawStatus === 'accepted') return setEntity({ kind: 'reading', id: item.rawId, payload: item.payload })
      return setWork({ kind: 'moderate-reading', item })
    }
    if (page === 'readings' && user.role === 'blogger') return setEntity({ kind: 'reading', id: item.rawId, payload: item.payload })
    notify(item.title)
  }
  let primary: { label: string; run: () => void } | null = null
  if (user.role === 'blogger' && page === 'publications') primary = { label: 'Новый ролик', run: () => setWork({ kind: 'content' }) }
  else if (user.role === 'blogger' && page === 'readings') primary = { label: 'Добавить показание', run: () => setWork({ kind: 'reading' }) }
  else if (user.role === 'blogger' && page === 'finance') primary = { label: 'Запросить выплату', run: () => setPayout({ mode: 'request' }) }
  else if (user.role === 'blogger' && page === 'support') primary = { label: 'Новое обращение', run: () => setSupport({ mode: 'create' }) }
  else if (page === 'exports') primary = { label: 'Новая выгрузка', run: () => setExportJob({}) }
  else if (page === 'catalog' && ['moderator', 'manager', 'admin'].includes(user.role)) primary = { label: 'Новый товар', run: () => setOperation({ kind: 'product' }) }
  else if (page === 'legal' && user.role === 'admin') primary = { label: 'Новая версия', run: () => setOperation({ kind: 'legal' }) }
  const signOut = async () => { try { await logout() } finally { signedOut() } }
  const pagingSource = (data ?? {}) as Json
  const totalPages = page === 'moderation' ? Math.max(pagingSource.profiles?.total_pages ?? 1, pagingSource.publications?.total_pages ?? 1, pagingSource.socialAccounts?.total_pages ?? 1) : Number(pagingSource.total_pages ?? 1)
  const totalItems = page === 'moderation' ? Number(pagingSource.profiles?.total_items ?? 0) + Number(pagingSource.publications?.total_items ?? 0) + Number(pagingSource.socialAccounts?.total_items ?? 0) : Number(pagingSource.total_items ?? rows.length)
  const secondary = user.role === 'blogger' && page === 'finance' ? { label: 'Реквизиты', run: () => setPayout({ mode: 'requisites' as const }) } : user.role === 'admin' && page === 'billing' ? { label: 'Новая ставка', run: () => setOperation({ kind: 'rate' }) } : null
  return <div className="app-shell"><Sidebar page={page} user={user} identity={creatorIdentity} open={menu} navigate={navigate} close={() => setMenu(false)} signOut={signOut} settings={() => runTransition(() => setSettingsOpen(true))} /><div className="workspace"><Header menu={() => setMenu(true)} query={query} setQuery={setQuery} navigate={navigate} supportAvailable={allowed.includes('support')} /><main key={page}>{page === 'overview' && user.role === 'blogger' ? <CreatorOverview data={data ?? {}} query={query} loading={loading} error={error} reload={reload} openVideoCard={(id) => setEntity({ kind: 'video-card', id })} createVideo={() => setWork({ kind: 'content' })} navigate={navigate} /> : page === 'overview' ? <Overview data={data} role={user.role} query={query} loading={loading} error={error} reload={reload} openVideoCard={(id) => setEntity({ kind: 'video-card', id })} openQueue={openDashboardQueue} /> : page === 'analytics' ? <AnalyticsPage initialData={data} role={user.role} initialLoading={loading} initialError={error} reload={reload} /> : <ListPage page={page} role={user.role} rows={rows} query={query} loading={loading} error={error} reload={reload} action={action} cardAction={(item) => page === 'readings' && item.payload?.video_card_id ? setEntity({ kind: 'video-card', id: item.payload.video_card_id }) : void action(item)} primaryLabel={primary?.label} primaryAction={primary?.run} secondaryLabel={secondary?.label} secondaryAction={secondary?.run} pageNumber={pageNumber} totalPages={totalPages} totalItems={totalItems} setPageNumber={setPageNumber} filters={filters} setFilters={setFilters} />}</main></div>{work ? <WorkDrawer state={work} role={user.role} close={() => runTransition(() => setWork(null))} completed={completeWork} /> : null}{entity ? <EntityDrawer kind={entity.kind} id={entity.id} payload={entity.payload} close={() => runTransition(() => setEntity(null))} changed={(message) => { notify(message); reload() }} /> : null}{payout ? <PayoutDrawer mode={payout.mode} payoutId={payout.id} role={user.role} close={() => runTransition(() => setPayout(null))} completed={(message) => { setPayout(null); notify(message); reload() }} /> : null}{earningPeriod ? <EarningsDrawer period={earningPeriod} close={() => runTransition(() => setEarningPeriod(null))} /> : null}{support ? <SupportDrawer mode={support.mode} ticketId={support.id} user={user} close={() => runTransition(() => setSupport(null))} changed={(message) => { notify(message); reload() }} /> : null}{exportJob ? <ExportDrawer exportId={exportJob.id} close={() => runTransition(() => setExportJob(null))} changed={(message) => { notify(message); reload() }} /> : null}{bloggerCard ? <BloggerDrawer bloggerId={bloggerCard} close={() => runTransition(() => setBloggerCard(null))} manageAccess={user.role === 'admin' ? () => { const id = bloggerCard; setBloggerCard(null); setAdminUser(id) } : undefined} /> : null}{adminUser ? <AdminUserDrawer userId={adminUser} currentUser={user} close={() => runTransition(() => setAdminUser(null))} changed={(message) => { notify(message); reload() }} /> : null}{operation ? <OperationsDrawer kind={operation.kind} id={operation.id} role={user.role} close={() => runTransition(() => setOperation(null))} changed={(message) => { notify(message); reload() }} /> : null}{settingsOpen ? <SettingsDrawer user={user} close={() => runTransition(() => setSettingsOpen(false))} changed={(message) => { notify(message); void refreshCreatorIdentity() }} signedOut={signOut} /> : null}{toast ? <div className="toast"><Check size={18} /><span>{toast}</span><button onClick={() => setToast('')}><X size={16} /></button></div> : null}</div>
}
export function App() {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [checking, setChecking] = useState(true)
  const [backendError, setBackendError] = useState('')
  const check = () => {
    setChecking(true); setBackendError('')
    getCurrentUser().then(setUser).catch((caught) => {
      setUser(null)
      if (!(caught instanceof ApiError && caught.status === 401)) setBackendError('Сервис временно недоступен. Попробуйте ещё раз.')
    }).finally(() => setChecking(false))
  }
  useEffect(check, [])
  if (checking) return <div className="boot-screen"><div className="brand__mark"><span>A</span></div><LoaderCircle className="spin" size={22} /><span>Проверяем сессию</span></div>
  const publicTokenFlow = ['verifyToken', 'resetToken', 'deletionToken'].some((name) => new URLSearchParams(window.location.search).has(name))
  if (!user || publicTokenFlow) return <AuthPortal backendError={backendError} retryBackend={check} signedIn={(value) => { setBackendError(''); setUser(value); window.history.replaceState(null, '', window.location.pathname) }} />
  return <Cabinet user={user} signedOut={() => setUser(null)} />
}
