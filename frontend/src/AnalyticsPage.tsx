import { FormEvent, useEffect, useState } from 'react'
import {
  AlertTriangle, BarChart3, CircleDollarSign, Eye, Filter, LoaderCircle,
  Package, RefreshCw, Trophy, Video, WifiOff,
} from 'lucide-react'
import { AnalyticsFilters, ApiError, JsonObject, PagePayload, Role, listProducts, loadStaffAnalytics } from './api'
import { platformConfig, platformIds, platforms } from './platforms'

type Json = Record<string, any>
const numeric = (value = 0) => new Intl.NumberFormat('ru-RU').format(value)
const rubles = (value = 0) => new Intl.NumberFormat('ru-RU').format(Math.round(value / 100)) + ' ₽'
const roles: Record<Role, string> = {
  blogger: 'Блогер', moderator: 'Модератор', manager: 'Менеджер',
  finance: 'Финансы', analyst: 'Аналитик', admin: 'Администратор',
}

function Metric({ label, value, note, icon: Icon, tone }: {
  label: string; value: string; note: string; icon: typeof Eye; tone: string
}) {
  return <article className="stat"><div className={`stat__icon stat__icon--${tone}`}><Icon size={19} /></div><div className="stat__body"><span>{label}</span><strong>{value}</strong><small>{note}</small></div></article>
}

function Breakdown({ title, items }: { title: string; items: Json[] }) {
  const maximum = Math.max(1, ...items.map((item) => Number(item.views ?? 0)))
  return <section className="panel analytics-breakdown">
    <div className="panel__head"><div><h2>{title}</h2><p>Просмотры и начисления</p></div></div>
    <div className="breakdown-list">{items.slice(0, 8).map((item) => <article key={item.key}>
      <div><strong>{title === 'По площадкам' ? platformConfig(item.key ?? item.label).label : item.label || 'Без названия'}</strong><small>{numeric(item.publications)} публикаций · {rubles(item.accrual_kopecks)}</small></div>
      <span>{numeric(item.views)}</span>
      <i><em style={{ width: `${Math.max(3, Number(item.views ?? 0) / maximum * 100)}%` }} /></i>
    </article>)}{!items.length ? <div className="analytics-empty">Нет данных за выбранный период</div> : null}</div>
  </section>
}

function Ranking({ title, items }: { title: string; items: Json[] }) {
  return <section className="panel analytics-ranking">
    <div className="panel__head"><div><h2>{title}</h2><p>По подтвержденным данным</p></div><Trophy size={18} /></div>
    <div>{items.slice(0, 7).map((item, index) => <article key={item.id}>
      <b>{index + 1}</b><span><strong>{item.label}</strong><small>{rubles(item.accrual_kopecks)}</small></span><em>{numeric(item.views)}</em>
    </article>)}{!items.length ? <div className="analytics-empty">Рейтинг пока не сформирован</div> : null}</div>
  </section>
}

export function AnalyticsPage({ initialData, initialLoading, initialError, reload, role }: {
  initialData: PagePayload | null; initialLoading: boolean; initialError: string
  reload: () => void; role: Role
}) {
  const [data, setData] = useState<Json>((initialData ?? {}) as Json)
  const [products, setProducts] = useState<JsonObject[]>([])
  const [loading, setLoading] = useState(initialLoading)
  const [error, setError] = useState(initialError)

  useEffect(() => { setData((initialData ?? {}) as Json); setLoading(initialLoading); setError(initialError) }, [initialData, initialLoading, initialError])
  useEffect(() => { listProducts().then((value) => setProducts(value.items ?? [])).catch(() => {}) }, [])

  const applyFilters = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setLoading(true); setError('')
    const form = new FormData(event.currentTarget)
    const month = (name: string) => {
      const value = String(form.get(name) ?? '')
      return value ? value + '-01' : undefined
    }
    const filters: AnalyticsFilters = {
      periodFrom: month('periodFrom'), periodTo: month('periodTo'),
      brand: String(form.get('brand') ?? '') || undefined,
      productId: String(form.get('productId') ?? '') || undefined,
      platform: String(form.get('platform') ?? '') || undefined,
    }
    try { setData(await loadStaffAnalytics(filters)) }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось получить аналитику') }
    finally { setLoading(false) }
  }

  const overview = data.overview ?? {}
  const monthly: Json[] = data.monthly ?? []
  const maximum = Math.max(1, ...monthly.map((item) => Number(item.views ?? 0)))
  const risks = data.risks ?? {}

  return <>
    <div className="page-heading"><div><p className="eyebrow">{roles[role]}</p><h1>Аналитика</h1><p>Контент, аудитория, начисления и риски программы</p></div><div className="heading-actions"><button className="button button--secondary" onClick={reload} disabled={loading}><RefreshCw className={loading ? 'spin' : ''} size={17} />Обновить</button></div></div>
    <form className="panel analytics-filters" onSubmit={applyFilters}>
      <label><span>С месяца</span><input name="periodFrom" type="month" /></label>
      <label><span>По месяц</span><input name="periodTo" type="month" /></label>
      <label><span>Бренд</span><select name="brand"><option value="">Все бренды</option><option>AMP</option><option>AirTone</option><option>CrioLight</option></select></label>
      <label><span>Товар</span><select name="productId"><option value="">Все товары</option>{products.map((item) => <option key={item.id} value={item.id}>{item.publication_name}</option>)}</select></label>
      <label><span>Площадка</span><select name="platform"><option value="">Все площадки</option>{platformIds.map((id) => <option key={id} value={id}>{platforms[id].label}</option>)}</select></label>
      <button className="button button--primary"><Filter size={16} />Применить</button>
    </form>
    {loading ? <div className="request-state"><LoaderCircle className="spin" size={20} />Получаем данные</div> : null}
    {error ? <div className="request-state request-state--error"><WifiOff size={20} /><span>{error}</span><button onClick={reload}>Повторить</button></div> : null}
    {!loading && !error ? <>
      <section className="stats-grid">
        <Metric label="Просмотры" value={numeric(overview.views)} note="За выбранный период" tone="blue" icon={Eye} />
        <Metric label="Начислено" value={rubles(overview.accrual_kopecks)} note="По подтвержденным данным" tone="green" icon={CircleDollarSign} />
        <Metric label="Выплачено" value={rubles(overview.paid_kopecks)} note="Фактические выплаты" tone="red" icon={CircleDollarSign} />
        <Metric label="Публикации" value={numeric(overview.publications)} note={numeric(overview.video_cards) + ' карточек'} tone="amber" icon={Video} />
        <Metric label="Средняя стоимость" value={rubles(overview.average_video_cost_kopecks)} note="На один ролик" tone="blue" icon={BarChart3} />
      </section>
      <section className="panel analytics-chart">
        <div className="panel__head"><div><h2>Динамика по месяцам</h2><p>Просмотры, начисления и выплаты</p></div></div>
        <div className="bar-chart">{monthly.map((item, index) => <span key={item.period ?? index} title={`${numeric(item.views)} просмотров · ${rubles(item.accrual_kopecks)}`} className={index >= monthly.length - 3 ? 'hot' : ''} style={{ height: Math.max(8, Math.round((item.views / maximum) * 116)) + 'px' }} />)}</div>
        <div className="bar-labels">{monthly.map((item) => <span key={item.period}>{new Date(item.period).toLocaleDateString('ru-RU', { month: 'short', year: '2-digit', timeZone: 'Europe/Moscow' })}</span>)}</div>
      </section>
      <div className="analytics-breakdown-grid"><Breakdown title="По брендам" items={data.by_brand ?? []} /><Breakdown title="По товарам" items={data.by_product ?? []} /><Breakdown title="По площадкам" items={data.by_platform ?? []} /></div>
      <div className="analytics-ranking-grid"><Ranking title="Лучшие блогеры" items={data.top_bloggers ?? []} /><Ranking title="Лучшие товары" items={data.top_products ?? []} /><Ranking title="Лучшие публикации" items={data.top_publications ?? []} /></div>
      <section className="panel analytics-risks"><div className="panel__head"><div><h2>Контроль рисков</h2><p>Сигналы, требующие проверки</p></div><AlertTriangle size={18} /></div><div>
        <span><AlertTriangle size={16} /><b>{numeric(risks.suspicious_accruals)}</b><small>Подозрительных начислений</small></span>
        <span><WifiOff size={16} /><b>{numeric(risks.failed_enrichment_jobs)}</b><small>Ошибок метаданных</small></span>
        <span><Eye size={16} /><b>{numeric(risks.failed_view_collection_jobs)}</b><small>Ошибок просмотров</small></span>
        <span><Package size={16} /><b>{numeric(risks.unavailable_publications)}</b><small>Недоступных публикаций</small></span>
      </div></section>
    </> : null}
  </>
}