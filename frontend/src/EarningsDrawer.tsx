import { useEffect, useState } from 'react'
import { AlertTriangle, ArrowLeft, Eye, LoaderCircle, WalletCards } from 'lucide-react'
import { ApiError, loadEarningsPeriod } from './api'
import { exclusionReasonLabel, riskFlagLabel } from './labels'

type Json = Record<string, any>
const rubles = (kopecks?: number) => new Intl.NumberFormat('ru-RU').format(Math.round((kopecks ?? 0) / 100)) + ' ₽'
const numeric = (value?: number) => new Intl.NumberFormat('ru-RU').format(value ?? 0)
const date = (value?: string) => value ? new Intl.DateTimeFormat('ru-RU').format(new Date(value)) : '—'

export function EarningsDrawer({ period, close }: { period: string; close: () => void }) {
  const [data, setData] = useState<Json | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    loadEarningsPeriod(period).then(setData).catch((caught) => setError(caught instanceof ApiError ? caught.message : 'Не удалось загрузить начисления'))
  }, [period])
  return <div className="drawer-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}>
    <aside className="work-drawer" role="dialog" aria-modal="true" aria-labelledby="earnings-title">
      <header className="work-drawer__head"><div><h2 id="earnings-title">Начисления за {date(period)}</h2><p>Расшифровка подтверждённого расчёта по публикациям</p></div><button className="icon-button" onClick={close} title="Назад"><ArrowLeft size={19} /></button></header>
      {!data && !error ? <div className="drawer-loading"><LoaderCircle className="spin" size={20} />Загружаем начисления</div> : null}
      {error ? <div className="auth-error"><AlertTriangle size={16} />{error}</div> : null}
      {data ? <div className="work-form">
        <section className="form-section"><h3><WalletCards size={17} />Итог периода</h3><div className="export-details"><span><small>К выплате</small>{rubles(data.total?.payable_amount_kopecks)}</span><span><small>Просмотры</small>{numeric(data.total?.eligible_views)}</span><span><small>Публикации</small>{numeric(data.total?.publication_count)}</span><span><small>Корректировки</small>{rubles(data.total?.adjustment_kopecks)}</span></div></section>
        <section className="form-section"><h3><Eye size={17} />Публикации</h3>{data.accruals?.length ? <div className="earning-list">{data.accruals.map((item: Json) => <article key={item.id}><div><strong>{numeric(item.eligible_views)} просмотров</strong><small>Публикация в расчёте</small></div><div><b>{rubles(item.payable_amount_kopecks)}</b><small>{item.exclusion_reason ? exclusionReasonLabel(item.exclusion_reason) : item.risk_flags?.length ? 'Требуется проверка: ' + item.risk_flags.map(riskFlagLabel).join(', ') : 'Учтено в расчёте'}</small></div></article>)}</div> : <div className="inline-note">В этом периоде нет начислений по публикациям.</div>}</section>
      </div> : null}
    </aside>
  </div>
}