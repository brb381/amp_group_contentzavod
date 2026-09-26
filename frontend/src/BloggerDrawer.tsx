import { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle, BadgeRussianRuble, BarChart3, CalendarDays,
  CheckCircle2, ExternalLink, Eye, History, LoaderCircle, Mail, MapPin,
  MessageCircle, PlaySquare, Settings, Smartphone, UserRound, UsersRound, ArrowLeft,
} from 'lucide-react'
import { ApiError, JsonObject, loadStaffBloggerCard } from './api'
import { availabilityLabels, enrichmentLabels, platformConfig } from './platforms'

type Tab = 'overview' | 'publications' | 'profile' | 'history'

const statusLabels: Record<string, string> = {
  active: 'Активен', approved: 'Одобрено', blocked: 'Заблокирован',
  changes_required: 'Нужны изменения', draft: 'Черновик', email_pending: 'Ждёт email',
  inactive: 'Неактивно', in_review: 'На проверке', pending_review: 'На модерации',
  rejected: 'Отклонено', re_review_required: 'Повторная проверка',
  preliminary: 'Предварительно', confirmed: 'Подтверждено',
  submitted: 'Отправлено', suspended: 'Приостановлен',
}
const numeric = (value = 0) => new Intl.NumberFormat('ru-RU').format(value)
const rubles = (value = 0) => new Intl.NumberFormat('ru-RU').format(Math.round(value / 100)) + ' ₽'
const formatDate = (value?: string | null, withTime = false) => {
  if (!value) return '—'
  return new Intl.DateTimeFormat('ru-RU', withTime
    ? { dateStyle: 'short', timeStyle: 'short' }
    : { dateStyle: 'medium' }).format(new Date(value))
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div className="blogger-fact"><small>{label}</small><strong>{value || '—'}</strong></div>
}

export function BloggerDrawer({ bloggerId, close, manageAccess }: {
  bloggerId: string
  close: () => void
  manageAccess?: () => void
}) {
  const [data, setData] = useState<JsonObject | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tab, setTab] = useState<Tab>('overview')

  useEffect(() => {
    let active = true
    setLoading(true); setError('')
    loadStaffBloggerCard(bloggerId)
      .then((value) => { if (active) setData(value) })
      .catch((caught) => { if (active) setError(caught instanceof ApiError ? caught.message : 'Не удалось загрузить карточку блогера') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [bloggerId])

  const profile = data?.profile ?? {}
  const dashboard = data?.dashboard ?? {}
  const account = data?.account ?? {}
  const publications: JsonObject[] = data?.publications ?? []
  const name = profile.display_name || profile.full_name || account.email || 'Блогер'
  const initials = String(name).split(/\s+/).slice(0, 2).map((item) => item[0]).join('').toUpperCase()
  const platformStats = useMemo(() => {
    const result = new Map<string, { publications: number; views: number }>()
    publications.forEach((item) => {
      const current = result.get(item.platform) ?? { publications: 0, views: 0 }
      result.set(item.platform, { publications: current.publications + 1, views: current.views + Number(item.current_views || 0) })
    })
    return Array.from(result.entries())
  }, [publications])
  const maxPlatformViews = Math.max(1, ...platformStats.map(([, item]) => item.views))

  const tabs: { id: Tab; label: string; icon: typeof BarChart3; count?: number }[] = [
    { id: 'overview', label: 'Обзор', icon: BarChart3 },
    { id: 'publications', label: 'Публикации', icon: PlaySquare, count: publications.length },
    { id: 'profile', label: 'Профиль', icon: UserRound },
    { id: 'history', label: 'История', icon: History, count: profile.history?.length ?? 0 },
  ]

  return <div className="drawer-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}>
    <aside className="work-drawer work-drawer--blogger" role="dialog" aria-modal="true" aria-labelledby="blogger-card-title">
      <header className="blogger-head">
        <button className="icon-button" onClick={close} title="Закрыть"><ArrowLeft size={19} /></button>
        <div className="blogger-head__identity"><span>{initials}</span><div><small>Карточка блогера</small><h2 id="blogger-card-title">{name}</h2><p>{account.email ?? 'Профиль участника программы'}</p></div></div>
        <div className="blogger-head__actions">{manageAccess ? <button className="icon-button" onClick={manageAccess} title="Управление доступом"><Settings size={18} /></button> : null}</div>
      </header>
      {loading ? <div className="drawer-loading"><LoaderCircle className="spin" size={20} />Загружаем профиль и статистику</div> : error ? <div className="blogger-load-error"><AlertTriangle size={22} /><strong>Карточка недоступна</strong><span>{error}</span><button className="button button--secondary" onClick={close}>Закрыть</button></div> : data ? <>
        <div className="blogger-statusbar"><span className={`status-pill ${account.status === 'active' ? 'status-pill--ok' : 'status-pill--warning'}`}>{statusLabels[account.status] ?? account.status}</span><span><CheckCircle2 size={14} />{account.email_verified_at ? 'Email подтверждён' : 'Email не подтверждён'}</span><span><CalendarDays size={14} />В программе с {formatDate(account.created_at)}</span></div>
        <nav className="blogger-tabs" role="tablist" aria-label="Разделы карточки блогера">{tabs.map(({ id, label, icon: Icon, count }) => <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'blogger-tabs__active' : ''} onClick={() => setTab(id)}><Icon size={15} />{label}{count != null ? <b>{count}</b> : null}</button>)}</nav>
        <div className="blogger-body" key={tab}>
          {tab === 'overview' ? <>
            <section className="blogger-kpis">
              <Fact label="Просмотры всего" value={numeric(dashboard.views?.total_views)} />
              <Fact label="Новые за период" value={numeric(dashboard.views?.current_period_new_views)} />
              <Fact label="Доступно к выплате" value={rubles(dashboard.finance?.available_kopecks)} />
              <Fact label="Выплачено" value={rubles(dashboard.finance?.paid_kopecks)} />
            </section>
            <div className="blogger-overview-grid">
              <section className="blogger-panel"><header><div><small>Контент</small><h3>Площадки и охват</h3></div><Eye size={18} /></header>{platformStats.length ? <div className="platform-breakdown">{platformStats.map(([platform, item]) => <div key={platform}><span><b>{platformConfig(platform).label}</b><small>{item.publications} публикаций · {numeric(item.views)} просмотров</small></span><i><em style={{ width: `${Math.max(7, item.views / maxPlatformViews * 100)}%` }} /></i></div>)}</div> : <div className="blogger-empty">Публикаций пока нет</div>}</section>
              <section className="blogger-panel"><header><div><small>Финансы</small><h3>Текущий расчёт</h3></div><BadgeRussianRuble size={18} /></header><div className="blogger-finance"><Fact label="Предварительно" value={rubles(dashboard.finance?.preliminary_kopecks)} /><Fact label="Зарезервировано" value={rubles(dashboard.finance?.reserved_kopecks)} /><Fact label="Период" value={formatDate(dashboard.calculation?.period)} /><Fact label="Статус" value={statusLabels[dashboard.calculation?.status] ?? dashboard.calculation?.status ?? 'Не начат'} /></div></section>
            </div>
            <section className="blogger-panel blogger-ranking"><header><div><small>Результаты</small><h3>Лучшие ролики</h3></div><BarChart3 size={18} /></header>{dashboard.top_video_cards?.length ? <div>{dashboard.top_video_cards.map((item: JsonObject, index: number) => <article key={item.video_card_id}><b>{index + 1}</b><span><strong>{item.title}</strong><small>{item.publications} публикаций</small></span><em>{numeric(item.total_views)} просмотров</em></article>)}</div> : <div className="blogger-empty">Статистика появится после первых показаний</div>}</section>
          </> : null}
          {tab === 'publications' ? <section className="blogger-publications">{publications.length ? publications.map((item) => <article key={item.id}><div className="blogger-publication__media">{item.thumbnail_url ? <img src={item.thumbnail_url} alt="" /> : <PlaySquare size={24} />}</div><div className="blogger-publication__copy"><div className="blogger-publication__head"><span>{platformConfig(item.platform).label}</span><b className={`publication-status publication-status--${item.status}`}>{statusLabels[item.status] ?? item.status}</b></div><h3>{item.card_title}</h3><p>{item.product_name}</p><div className="blogger-publication__collection"><span className={`collection-state collection-state--${item.enrichment_status}`}>{platformConfig(item.platform).automaticReadings ? (enrichmentLabels[item.enrichment_status] ?? 'Автосбор') : 'Ручные показания'}</span><small>{availabilityLabels[item.availability] ?? item.availability}</small></div><footer><strong><Eye size={14} />{numeric(item.current_views)}</strong><small>Обновлено {formatDate(item.updated_at)}</small><a href={item.url} target="_blank" rel="noreferrer" title="Открыть публикацию"><ExternalLink size={15} /></a></footer></div></article>) : <div className="blogger-empty blogger-empty--large"><PlaySquare size={28} />У блогера пока нет публикаций</div>}</section> : null}
          {tab === 'profile' ? <div className="blogger-profile-grid">
            <section className="blogger-panel"><header><div><small>Контакты</small><h3>Основные данные</h3></div><UserRound size={18} /></header><div className="blogger-contacts"><Fact label="ФИО" value={profile.full_name} /><Fact label="Публичное имя" value={profile.display_name} /><Fact label="Телефон" value={profile.phone} /><Fact label="Telegram" value={profile.telegram} /><Fact label="Город и страна" value={profile.city_country} /><Fact label="Тип получателя" value={profile.recipient_status === 'self_employed' ? 'Самозанятый' : profile.recipient_status === 'individual' ? 'Физическое лицо' : '—'} /></div>{profile.content_topics ? <p className="blogger-topics">{profile.content_topics}</p> : null}</section>
            <section className="blogger-panel"><header><div><small>Площадки</small><h3>Социальные аккаунты</h3></div><UsersRound size={18} /></header>{profile.social_accounts?.length ? <div className="blogger-socials">{profile.social_accounts.map((item: JsonObject) => <a key={item.id} href={item.url} target="_blank" rel="noreferrer"><span>{platformConfig(item.platform).shortLabel}</span><div><strong>{platformConfig(item.platform).label}</strong><small>{numeric(item.follower_count)} подписчиков</small></div><b>{statusLabels[item.status] ?? item.status}</b><ExternalLink size={14} /></a>)}</div> : <div className="blogger-empty">Площадки не добавлены</div>}</section>
            <section className="blogger-panel blogger-contact-strip"><a href={`mailto:${account.email}`}><Mail size={16} /><span><small>Email</small><strong>{account.email}</strong></span></a>{profile.phone ? <a href={`tel:${profile.phone}`}><Smartphone size={16} /><span><small>Телефон</small><strong>{profile.phone}</strong></span></a> : null}{profile.telegram ? <a href={`https://t.me/${String(profile.telegram).replace(/^@/, '')}`} target="_blank" rel="noreferrer"><MessageCircle size={16} /><span><small>Telegram</small><strong>{profile.telegram}</strong></span></a> : null}{profile.city_country ? <div><MapPin size={16} /><span><small>Регион</small><strong>{profile.city_country}</strong></span></div> : null}</section>
          </div> : null}
          {tab === 'history' ? <section className="blogger-panel blogger-timeline"><header><div><small>Модерация</small><h3>История профиля</h3></div><History size={18} /></header>{profile.history?.length ? <div>{profile.history.map((item: JsonObject) => <article key={item.id}><i /><span><strong>{statusLabels[item.to_status] ?? item.to_status}</strong><small>{item.reason || String(item.event_type).replaceAll('_', ' ')}</small></span><time>{formatDate(item.created_at, true)}</time></article>)}</div> : <div className="blogger-empty">История пока пуста</div>}</section> : null}
        </div>
      </> : null}
    </aside>
  </div>
}
