import { FormEvent, useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle, ArrowRight, BarChart3, Bell, Check, CircleDollarSign, Download,
  Eye, FileSpreadsheet, Gauge, HelpCircle, LayoutDashboard, LoaderCircle,
  LockKeyhole, LogOut, Menu, MessageSquareText, RefreshCw, Search, Settings,
  SlidersHorizontal, UsersRound, Video, WalletCards, WifiOff, X,
} from 'lucide-react'
import {
  ApiError, CurrentUser, PageId, PagePayload, Role, downloadExport,
  getCurrentUser, loadPage, login, logout,
} from './api'

type Tone = 'red' | 'amber' | 'green' | 'blue' | 'gray'
type Json = Record<string, any>
type Row = {
  id: string; rawId?: string; title: string; meta: string; status: string;
  rawStatus: string; tone: Tone; date: string; value: string; initials: string;
}
type NavItem = { id: PageId; label: string; icon: typeof LayoutDashboard }

const roleLabels: Record<Role, string> = {
  blogger: 'Блогер', moderator: 'Модератор', manager: 'Менеджер',
  finance: 'Финансы', analyst: 'Аналитик', admin: 'Администратор',
}
const rolePages: Record<Role, PageId[]> = {
  blogger: ['overview', 'publications', 'readings', 'finance', 'support'],
  moderator: ['overview', 'moderation', 'creators', 'publications', 'readings', 'exports', 'support'],
  manager: ['overview', 'readings', 'finance', 'exports', 'support'],
  finance: ['finance', 'exports'],
  analyst: ['analytics', 'exports'],
  admin: ['overview', 'moderation', 'creators', 'publications', 'readings', 'finance', 'exports', 'support', 'analytics'],
}
const navGroups: { label: string; items: NavItem[] }[] = [
  { label: 'Работа', items: [
    { id: 'overview', label: 'Обзор', icon: LayoutDashboard },
    { id: 'moderation', label: 'Очереди', icon: Gauge },
    { id: 'creators', label: 'Блогеры', icon: UsersRound },
    { id: 'publications', label: 'Публикации', icon: Video },
    { id: 'readings', label: 'Показания', icon: Eye },
  ]},
  { label: 'Деньги и данные', items: [
    { id: 'finance', label: 'Выплаты', icon: WalletCards },
    { id: 'exports', label: 'Выгрузки', icon: FileSpreadsheet },
    { id: 'analytics', label: 'Аналитика', icon: BarChart3 },
  ]},
  { label: 'Связь', items: [{ id: 'support', label: 'Обращения', icon: MessageSquareText }]},
]
const pageMeta: Record<PageId, [string, string]> = {
  overview: ['Рабочий стол', 'Текущие показатели и ближайшие действия'],
  moderation: ['Очереди', 'Задачи, которые требуют решения сотрудника'],
  creators: ['Блогеры', 'Профили, аккаунты и статусы участия'],
  publications: ['Публикации', 'Контент и карточки роликов по площадкам'],
  readings: ['Показания', 'Значения просмотров и подозрительные изменения'],
  finance: ['Выплаты', 'Запросы, реквизиты и фиксация оплаты'],
  exports: ['Выгрузки', 'Асинхронные отчёты и платёжные реестры'],
  support: ['Обращения', 'Диалоги, вопросы и запросы восстановления'],
  analytics: ['Аналитика', 'Контент, аудитория и выплаты по программе'],
}
const statusLabels: Record<string, string> = {
  active: 'Активен', approved: 'Одобрено', blocked: 'Заблокирован',
  changes_required: 'Нужны изменения', closed: 'Закрыто', completed: 'Готово',
  draft: 'Черновик', email_pending: 'Ждёт email', failed: 'Ошибка',
  in_progress: 'В работе', new: 'Новое', open: 'Открыто', paid: 'Оплачено',
  partially_approved: 'Частично одобрено', pending: 'На проверке',
  pending_review: 'На модерации', processing: 'Формируется', ready: 'Готово',
  rejected: 'Отклонено', resolved: 'Решено', suspended: 'Приостановлен',
  submitted: 'Отправлено', under_review: 'На проверке', waiting_blogger: 'Ждём блогера',
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
}

const rubles = (kopecks?: number) => new Intl.NumberFormat('ru-RU').format(Math.round((kopecks ?? 0) / 100)) + ' ₽'
const numeric = (value?: number) => new Intl.NumberFormat('ru-RU', {
  notation: (value ?? 0) >= 1_000_000 ? 'compact' : 'standard', maximumFractionDigits: 2,
}).format(value ?? 0)
const date = (value: unknown) => {
  if (!value || typeof value !== 'string') return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : new Intl.DateTimeFormat('ru-RU', {
    day: '2-digit', month: '2-digit', year: 'numeric',
  }).format(parsed)
}
const initials = (value: string) => (value.trim().split(/[\s@._-]+/).filter(Boolean)
  .slice(0, 2).map((part) => part[0]).join('') || 'AMP').toUpperCase()
const tone = (status: string, risk = false): Tone => {
  if (risk || ['failed', 'rejected', 'blocked', 'overdue', 'changes_required'].includes(status)) return 'red'
  if (['pending', 'pending_review', 'processing', 'submitted', 'under_review', 'in_progress'].includes(status)) return 'amber'
  if (['approved', 'active', 'ready', 'paid', 'resolved', 'completed'].includes(status)) return 'green'
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
      meta: item.city_country || 'Заявка блогера', status: statusLabels[item.status] ?? item.status,
      rawStatus: item.status, tone: tone(item.status), date: date(item.updated_at), value: 'Профиль',
    }))
    const publicationRows = ((data.publications as Json)?.items ?? []).map((item: Json) => {
      const publication = item.publication ?? {}
      const creator = item.creator_display_name || item.creator_full_name || item.creator_email || 'Блогер'
      return row({ id: 'publication-' + publication.id, rawId: publication.id,
        title: item.card_title || publication.external_title || 'Публикация',
        meta: creator + ' · ' + (publication.platform ?? 'площадка'),
        status: statusLabels[publication.status] ?? publication.status, rawStatus: publication.status,
        tone: tone(publication.status), date: date(publication.updated_at), value: 'Публикация',
        initials: initials(creator),
      })
    })
    return [...profileRows, ...publicationRows]
  }
  const items: Json[] = Array.isArray(data.items) ? data.items : []
  if (page === 'creators') return items.map((item) => {
    const name = item.display_name || item.full_name || item.email || 'Блогер'
    return row({ id: item.id, rawId: item.id, title: name,
      meta: role === 'admin' ? item.email + ' · ' + (roleLabels[item.role as Role] ?? item.role) : item.city_country || item.content_topics || 'Профиль',
      status: statusLabels[item.status] ?? item.status, rawStatus: item.status, tone: tone(item.status),
      date: date(item.updated_at || item.created_at), value: role === 'admin' ? roleLabels[item.role as Role] ?? item.role : 'Профиль',
    })
  })
  if (page === 'publications') return items.map((item) => {
    if (role === 'blogger') return row({ id: item.id, rawId: item.id, title: item.title,
      meta: item.product_snapshot?.publication_name || item.reported_product?.name || 'Карточка ролика',
      status: statusLabels[item.status] ?? item.status, rawStatus: item.status, tone: tone(item.status),
      date: date(item.updated_at), value: (item.publication_summary?.total ?? 0) + ' публикаций',
    })
    const publication = item.publication ?? {}
    const creator = item.creator_display_name || item.creator_full_name || item.creator_email || 'Блогер'
    return row({ id: publication.id, rawId: publication.id,
      title: item.card_title || publication.external_title || 'Публикация',
      meta: creator + ' · ' + (publication.platform ?? 'площадка'),
      status: statusLabels[publication.status] ?? publication.status, rawStatus: publication.status,
      tone: tone(publication.status), date: date(publication.updated_at),
      value: publication.external_author_name || publication.platform || '—', initials: initials(creator),
    })
  })
  if (page === 'readings') return items.map((item) => row({
    id: item.id, rawId: item.id, title: numeric(item.reported_value) + ' просмотров',
    meta: (item.source ?? 'manual') + ' · период ' + date(item.reporting_period),
    status: statusLabels[item.status] ?? item.status, rawStatus: item.status,
    tone: tone(item.status, Boolean(item.risk_flags?.length)), date: date(item.updated_at || item.captured_at),
    value: item.risk_flags?.length ? item.risk_flags.length + ' риска' : item.accepted_value == null ? '—' : numeric(item.accepted_value),
    initials: item.source === 'youtube' ? 'YT' : 'Р',
  }))
  if (page === 'finance') return items.map((item) => {
    const name = item.recipient_display_name || item.recipient_full_name || item.request_number
    return row({ id: item.request_number || item.id, rawId: item.id, title: name,
      meta: (item.recipient_type ?? 'получатель') + ' · ' + item.request_number,
      status: statusLabels[item.status] ?? item.status, rawStatus: item.status,
      tone: tone(item.status, Boolean(item.is_payment_overdue)), date: date(item.updated_at || item.requested_at),
      value: rubles(item.amount_kopecks),
    })
  })
  if (page === 'exports') return items.map((item) => row({
    id: item.id, rawId: item.id, title: String(item.export_type ?? 'Выгрузка').replaceAll('_', ' '),
    meta: String(item.format ?? '').toUpperCase() + ' · схема ' + (item.schema_version ?? 1),
    status: statusLabels[item.status] ?? item.status, rawStatus: item.status, tone: tone(item.status),
    date: date(item.completed_at || item.created_at), value: item.row_count == null ? '—' : numeric(item.row_count) + ' строк',
    initials: String(item.format ?? 'XLS').slice(0, 3).toUpperCase(),
  }))
  if (page === 'support') return items.map((item) => row({
    id: item.ticket_number || item.id, rawId: item.id, title: item.subject,
    meta: String(item.category ?? 'Обращение').replaceAll('_', ' '),
    status: statusLabels[item.status] ?? item.status, rawStatus: item.status, tone: tone(item.status),
    date: date(item.updated_at || item.last_message_at),
    value: item.assigned_to_user_id ? 'Назначено' : 'Без исполнителя', initials: 'ТП',
  }))
  return []
}

function dashboardRows(payload: PagePayload | null, role: Role): Row[] {
  const data = (payload ?? {}) as Json
  if (role === 'blogger') return (data.top_video_cards ?? []).map((item: Json) => row({
    id: item.video_card_id, title: item.title, meta: item.publications + ' публикаций',
    status: 'Активно', rawStatus: 'active', tone: 'green', date: 'Текущий период',
    value: numeric(item.current_period_new_views) + ' просмотров',
  }))
  return (data.queues ?? []).filter((item: Json) => item.count > 0).map((item: Json) => row({
    id: item.code, title: queueLabels[item.code] ?? String(item.code).replaceAll('_', ' '),
    meta: 'Операционная очередь', status: item.count >= 5 ? 'Приоритет' : 'В работе',
    rawStatus: 'pending', tone: item.count >= 5 ? 'red' : 'amber',
    date: 'Сейчас', value: item.count + ' задач', initials: String(item.count),
  }))
}

function Badge({ value, color }: { value: string; color: Tone }) {
  return <span className={`badge badge--${color}`}><span className="badge__dot" />{value}</span>
}
function Avatar({ value, small = false }: { value: string; small?: boolean }) {
  return <span className={`avatar ${small ? 'avatar--small' : ''}`}>{value}</span>
}
function MediaThumb({ index }: { index: number }) {
  return <span className="media-thumb" style={{ backgroundPosition: `${index * 33.333}% center` }} />
}
function RequestState({ loading, error, retry }: { loading: boolean; error: string; retry: () => void }) {
  if (loading) return <div className="request-state"><LoaderCircle className="spin" size={20} />Получаем данные</div>
  if (error) return <div className="request-state request-state--error"><WifiOff size={20} /><span>{error}</span><button onClick={retry}>Повторить</button></div>
  return null
}
function Stat({ label, value, note, color, icon: Icon }: { label: string; value: string; note: string; color: string; icon: typeof UsersRound }) {
  return <article className="stat"><div className={`stat__icon stat__icon--${color}`}><Icon size={19} /></div><div className="stat__body"><span>{label}</span><strong>{value}</strong><small>{note}</small></div></article>
}

function Sidebar({ page, user, open, navigate, close, signOut }: {
  page: PageId; user: CurrentUser; open: boolean; navigate: (page: PageId) => void;
  close: () => void; signOut: () => void;
}) {
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
      <div className="sidebar__bottom"><button className="nav__item"><Settings size={18} /><span>Настройки</span></button><div className="profile-chip"><Avatar value={initials(user.email)} small /><span><strong>{user.email}</strong><small>{roleLabels[user.role]}</small></span><button className="profile-logout" onClick={signOut} title="Выйти"><LogOut size={16} /></button></div></div>
    </aside>
    {open ? <button className="sidebar-backdrop" onClick={close} /> : null}
  </>
}
function Header({ menu, query, setQuery }: { menu: () => void; query: string; setQuery: (value: string) => void }) {
  return <header className="topbar"><button className="icon-button menu-button" onClick={menu}><Menu size={21} /></button><div className="global-search"><Search size={18} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Найти в текущем разделе" /><kbd>Ctrl K</kbd></div><div className="topbar__actions"><button className="icon-button"><Bell size={19} /></button><button className="help-button"><HelpCircle size={18} /><span>Помощь</span></button></div></header>
}

function Table({ rows, query, page, action }: { rows: Row[]; query: string; page: PageId; action: (row: Row) => void }) {
  const filtered = rows.filter((item) => (item.title + ' ' + item.meta + ' ' + item.id).toLowerCase().includes(query.toLowerCase()))
  return <div className="table-wrap"><table><thead><tr><th>Название</th><th>Статус</th><th>Дата</th><th>Показатель</th><th /></tr></thead><tbody>{filtered.map((item, index) => <tr key={item.id} onClick={() => action(item)}><td><div className="entity">{page === 'publications' ? <MediaThumb index={index % 4} /> : <Avatar value={item.initials} />}<div><strong>{item.title}</strong><span>{item.id} · {item.meta}</span></div></div></td><td><Badge value={item.status} color={item.tone} /></td><td className="muted">{item.date}</td><td><strong className="cell-value">{item.value}</strong></td><td><button className="row-action">{page === 'exports' && item.rawStatus === 'ready' ? <Download size={16} /> : <ArrowRight size={18} />}</button></td></tr>)}</tbody></table>{!filtered.length ? <div className="empty"><Search size={24} /><strong>Данных пока нет</strong><span>{query ? 'Измените поисковый запрос' : 'Backend вернул пустой список'}</span></div> : null}</div>
}

function Overview({ data, role, query, loading, error, reload }: {
  data: PagePayload | null; role: Role; query: string; loading: boolean; error: string; reload: () => void;
}) {
  const source = (data ?? {}) as Json
  const overview = source.overview ?? {}
  const blogger = role === 'blogger'
  const rows = dashboardRows(data, role)
  const stats = blogger ? [
    ['Активные ролики', numeric(source.content?.active_video_cards), (source.content?.active_publications ?? 0) + ' публикаций', 'green', Video],
    ['Просмотры всего', numeric(source.views?.total_views), '+' + numeric(source.views?.current_period_new_views) + ' за период', 'blue', Eye],
    ['Доступный баланс', rubles(source.finance?.available_kopecks), rubles(source.finance?.reserved_kopecks) + ' зарезервировано', 'amber', WalletCards],
    ['Выплачено', rubles(source.finance?.paid_kopecks), statusLabels[source.calculation?.status] ?? 'Расчёт не начат', 'red', CircleDollarSign],
  ] : [
    ['Активные блогеры', numeric(overview.active_bloggers), (overview.new_applications ?? 0) + ' новых заявок', 'green', UsersRound],
    ['Публикации', numeric(overview.active_publications), 'Активные ссылки', 'blue', Video],
    ['Предварительно', rubles(overview.preliminary_accrual_kopecks), 'Начисления периода', 'amber', Gauge],
    ['Выплаты в работе', rubles(overview.payouts_in_progress_kopecks), rubles(overview.available_balance_kopecks) + ' доступно', 'red', CircleDollarSign],
  ]
  return <>
    <PageHeading title="Рабочий стол" subtitle={blogger ? 'Результаты контента и ближайшие действия.' : 'Операционные показатели и очереди команды.'} role={role} loading={loading} reload={reload} />
    <RequestState loading={loading} error={error} retry={reload} />
    {!error && !loading ? <><section className="stats-grid">{stats.map(([label, value, note, color, icon]) => <Stat key={label as string} label={label as string} value={value as string} note={note as string} color={color as string} icon={icon as typeof UsersRound} />)}</section><div className="content-grid"><section className="panel queue-panel"><div className="panel__head"><div><h2>{blogger ? 'Лучшие ролики' : 'Очередь работы'}</h2><p>Данные backend в реальном времени</p></div><Badge value={String(rows.length)} color="blue" /></div><Table rows={rows} query={query} page="overview" action={() => {}} /></section><aside className="attention"><div className="attention__head"><div><AlertTriangle size={18} /><h2>Состояние</h2></div></div><div className="attention-item attention-item--static"><span className="risk-icon risk-icon--amber"><Gauge size={17} /></span><span><strong>{rows.length} активных очередей</strong><small>Требуют обработки</small></span></div><div className="system-status"><span><i />API подключён</span><small>Данные получены после загрузки</small></div></aside></div></> : null}
  </>
}
function PageHeading({ title, subtitle, role, loading, reload }: { title: string; subtitle: string; role: Role; loading: boolean; reload: () => void }) {
  return <div className="page-heading"><div><p className="eyebrow">{roleLabels[role]}</p><h1>{title}</h1><p>{subtitle}</p></div><button className="button button--primary" onClick={reload} disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={17} />Обновить</button></div>
}
function ListPage({ page, role, rows, query, loading, error, reload, action }: {
  page: PageId; role: Role; rows: Row[]; query: string; loading: boolean; error: string; reload: () => void; action: (row: Row) => void;
}) {
  const [title, subtitle] = pageMeta[page]
  return <><PageHeading title={role === 'blogger' && page === 'publications' ? 'Мои ролики' : title} subtitle={subtitle} role={role} loading={loading} reload={reload} /><RequestState loading={loading} error={error} retry={reload} />{!error && !loading ? <section className="panel list-panel"><div className="list-toolbar"><div className="local-search"><Search size={17} /><span>{query ? 'Поиск: ' + query : 'Записей: ' + rows.length}</span></div><button className="button button--secondary" disabled><SlidersHorizontal size={17} />Фильтры</button></div><Table rows={rows} query={query} page={page} action={action} /><div className="pagination"><span>Показано {rows.length}</span><div><button disabled>Назад</button><button disabled>Дальше</button></div></div></section> : null}</>
}
function Analytics({ data, loading, error, reload, role }: { data: PagePayload | null; loading: boolean; error: string; reload: () => void; role: Role }) {
  const source = (data ?? {}) as Json
  const overview = source.overview ?? {}
  const monthly: Json[] = source.monthly ?? []
  const maximum = Math.max(...monthly.map((item) => Number(item.views ?? 0)), 1)
  return <><PageHeading title="Аналитика" subtitle="Контент, аудитория и выплаты по программе" role={role} loading={loading} reload={reload} /><RequestState loading={loading} error={error} retry={reload} />{!error && !loading ? <><section className="stats-grid"><Stat label="Просмотры" value={numeric(overview.views)} note="За выбранный период" color="blue" icon={Eye} /><Stat label="Начислено" value={rubles(overview.accrual_kopecks)} note="По подтверждённым данным" color="green" icon={CircleDollarSign} /><Stat label="Публикации" value={numeric(overview.publications)} note={(overview.video_cards ?? 0) + ' карточек'} color="amber" icon={Video} /><Stat label="Средняя стоимость" value={rubles(overview.average_video_cost_kopecks)} note="На один ролик" color="red" icon={BarChart3} /></section><section className="panel analytics-chart"><div className="panel__head"><div><h2>Динамика по месяцам</h2><p>Просмотры</p></div></div><div className="bar-chart">{monthly.map((item, index) => <span key={item.period ?? index} className={index >= monthly.length - 3 ? 'hot' : ''} style={{ height: Math.max(8, Math.round((item.views / maximum) * 116)) + 'px' }} />)}</div><div className="bar-labels">{monthly.map((item) => <span key={item.period}>{date(item.period).slice(3)}</span>)}</div></section></> : null}</>
}

function Login({ signedIn }: { signedIn: (user: CurrentUser) => void }) {
  const [email, setEmail] = useState(''); const [password, setPassword] = useState('')
  const [pending, setPending] = useState(false); const [error, setError] = useState('')
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setPending(true); setError('')
    try { signedIn(await login(email.trim(), password)) }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Backend недоступен. Проверьте FastAPI.') }
    finally { setPending(false) }
  }
  return <main className="auth-page"><section className="auth-panel"><div className="auth-brand"><div className="brand__mark"><span>A</span></div><div><strong>AMP</strong><span>Content Factory</span></div></div><div className="auth-copy"><h1>Вход в кабинет</h1><p>Используйте почту и пароль аккаунта.</p></div><form className="auth-form" onSubmit={submit}><label><span>Email</span><input type="email" autoComplete="email" required value={email} onChange={(event) => setEmail(event.target.value)} placeholder="name@ampgroup.ru" /></label><label><span>Пароль</span><input type="password" autoComplete="current-password" required value={password} onChange={(event) => setPassword(event.target.value)} placeholder="Введите пароль" /></label>{error ? <div className="auth-error"><AlertTriangle size={16} />{error}</div> : null}<button className="button button--primary auth-submit" disabled={pending}>{pending ? <LoaderCircle className="spin" size={18} /> : <LockKeyhole size={18} />}{pending ? 'Входим' : 'Войти'}</button></form><div className="auth-status"><span><i />Защищённая cookie-сессия</span><small>Пароль не хранится в браузере</small></div></section><aside className="auth-visual"><div className="auth-visual__content"><span>Контент-завод</span><strong>От публикации<br />до выплаты</strong><p>Единое рабочее пространство AMP Group</p></div></aside></main>
}

function Cabinet({ user, signedOut }: { user: CurrentUser; signedOut: () => void }) {
  const allowed = rolePages[user.role]
  const hash = window.location.hash.slice(1) as PageId
  const [page, setPage] = useState<PageId>(allowed.includes(hash) ? hash : allowed[0])
  const [menu, setMenu] = useState(false); const [query, setQuery] = useState('')
  const [data, setData] = useState<PagePayload | null>(null); const [loading, setLoading] = useState(true)
  const [error, setError] = useState(''); const [reloadKey, setReloadKey] = useState(0)
  const [toast, setToast] = useState('')
  useEffect(() => {
    let cancelled = false; setLoading(true); setError('')
    loadPage(user.role, page).then((value) => { if (!cancelled) setData(value) }).catch((caught) => {
      if (cancelled) return
      if (caught instanceof ApiError && caught.status === 401) return signedOut()
      setData(null); setError(caught instanceof ApiError ? caught.message : 'Не удалось связаться с backend')
    }).finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [page, reloadKey, user.role, signedOut])
  const rows = useMemo(() => rowsFrom(data, page, user.role), [data, page, user.role])
  const reload = () => setReloadKey((value) => value + 1)
  const notify = (value: string) => { setToast(value); window.setTimeout(() => setToast(''), 2400) }
  const action = async (item: Row) => {
    if (page !== 'exports') return notify(item.title)
    if (item.rawStatus !== 'ready' || !item.rawId) return notify('Выгрузка ещё не готова')
    try { await downloadExport(item.rawId); notify('Скачивание началось') }
    catch (caught) { notify(caught instanceof ApiError ? caught.message : 'Не удалось скачать файл') }
  }
  const signOut = async () => { try { await logout() } finally { signedOut() } }
  return <div className="app-shell"><Sidebar page={page} user={user} open={menu} navigate={(next) => { setPage(next); setData(null); setQuery(''); window.history.replaceState(null, '', '#' + next) }} close={() => setMenu(false)} signOut={signOut} /><div className="workspace"><Header menu={() => setMenu(true)} query={query} setQuery={setQuery} /><main>{page === 'overview' ? <Overview data={data} role={user.role} query={query} loading={loading} error={error} reload={reload} /> : page === 'analytics' ? <Analytics data={data} role={user.role} loading={loading} error={error} reload={reload} /> : <ListPage page={page} role={user.role} rows={rows} query={query} loading={loading} error={error} reload={reload} action={action} />}</main></div>{toast ? <div className="toast"><Check size={18} /><span>{toast}</span><button onClick={() => setToast('')}><X size={16} /></button></div> : null}</div>
}

export function App() {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [checking, setChecking] = useState(true)
  const [backendError, setBackendError] = useState('')
  const check = () => {
    setChecking(true); setBackendError('')
    getCurrentUser().then(setUser).catch((caught) => {
      setUser(null)
      if (!(caught instanceof ApiError && caught.status === 401)) setBackendError('Backend недоступен. Запустите FastAPI на порту 8000.')
    }).finally(() => setChecking(false))
  }
  useEffect(check, [])
  if (checking) return <div className="boot-screen"><div className="brand__mark"><span>A</span></div><LoaderCircle className="spin" size={22} /><span>Проверяем сессию</span></div>
  if (!user) return <>{backendError ? <div className="backend-banner"><WifiOff size={16} />{backendError}<button onClick={check}>Повторить</button></div> : null}<Login signedIn={(value) => { setBackendError(''); setUser(value) }} /></>
  return <Cabinet user={user} signedOut={() => setUser(null)} />
}

