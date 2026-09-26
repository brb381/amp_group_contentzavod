import { ArrowRight, ArrowUpRight, CheckCircle2, Eye, LoaderCircle, Plus, RefreshCw, Video, WalletCards } from 'lucide-react'

type Dashboard = {
  period?: string
  content?: { active_video_cards: number; active_publications: number }
  views?: { total_views: number; current_period_new_views: number }
  finance?: { available_kopecks: number; reserved_kopecks: number; paid_kopecks: number; preliminary_kopecks: number }
  attention?: { pending_publications: number; changes_required_publications: number }
  top_video_cards?: { video_card_id: string; title: string; publications: number; total_views: number; thumbnail_url?: string | null }[]
}
type Props = {
  data: Dashboard; query: string; loading: boolean; error: string
  reload: () => void; openVideoCard: (id: string) => void
  createVideo: () => void; navigate: (page: 'publications' | 'finance') => void
}
const number = (value = 0) => new Intl.NumberFormat('ru-RU').format(value)
const money = (value = 0) => new Intl.NumberFormat('ru-RU', { style: 'currency', currency: 'RUB', maximumFractionDigits: 0 }).format(value / 100)

export function CreatorOverview({ data, query, loading, error, reload, openVideoCard, createVideo, navigate }: Props) {
  const cards = data.top_video_cards ?? []
  const filtered = cards.filter((card) => card.title.toLocaleLowerCase('ru').includes(query.toLocaleLowerCase('ru')))
  const maxViews = Math.max(1, ...cards.map((card) => card.total_views))
  const period = data.period ? new Intl.DateTimeFormat('ru-RU', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(new Date(data.period)) : ''
  const pending = data.attention?.pending_publications ?? 0
  const changes = data.attention?.changes_required_publications ?? 0
  return <div className="creator-overview">
    <header className="studio-heading">
      <div><span className="studio-kicker">AMP / КАБИНЕТ АВТОРА</span><h1>Рабочий стол<span>.</span></h1><p>Ваш контент. Ваш результат.</p></div>
      <div className="studio-actions"><button className="button button--primary" onClick={createVideo}><Plus size={18} />Добавить ролик</button><button className="icon-button" onClick={reload} disabled={loading} title="Обновить данные"><RefreshCw size={18} className={loading ? 'spin' : ''} /></button></div>
    </header>
    {loading ? <div className="request-state"><LoaderCircle className="spin" />Загружаем кабинет</div> : error ? <div className="request-state request-state--error" role="alert"><span>{error}</span><button onClick={reload}>Повторить</button></div> : <>
      <section className="studio-metrics" aria-label="Основные показатели">
        <div className="studio-metric studio-metric--reach"><span><Eye size={16} />Просмотры всего</span><strong>{number(data.views?.total_views)}</strong><small>На всех площадках</small></div>
        <div className="studio-metric"><span>Новые просмотры</span><strong>+{number(data.views?.current_period_new_views)}</strong><small>{period || 'Текущий период'}</small></div>
        <div className="studio-metric"><span>Активные ролики</span><strong>{number(data.content?.active_video_cards)}</strong><small>Публикаций: {number(data.content?.active_publications)}</small></div>
        <div className="studio-metric"><span>Выплачено</span><strong>{money(data.finance?.paid_kopecks)}</strong><small>За всё время</small></div>
      </section>
      <div className="studio-columns">
        <section className="studio-content queue-panel">
          <header className="studio-section-head"><div><span className="studio-kicker">КОНТЕНТ</span><h2>Ваши лучшие ролики <span>{cards.length}</span></h2></div><button className="studio-text-button" onClick={() => navigate('publications')}>Все ролики<ArrowUpRight size={17} /></button></header>
          {filtered.length ? <div className="studio-ranking"><table><thead><tr><th>Ролик</th><th>Просмотры</th><th><span className="sr-only">Открыть</span></th></tr></thead><tbody>{filtered.map((card) => <tr key={card.video_card_id} onClick={() => openVideoCard(card.video_card_id)}>
            <td><div className="studio-video"><span className="studio-rank">{String(cards.indexOf(card) + 1).padStart(2, '0')}</span><span className="studio-thumbnail"><Video size={23} />{card.thumbnail_url ? <img src={card.thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.hidden = true }} /> : null}</span><span className="studio-video-copy"><strong>{card.title}</strong><small>Публикаций: {card.publications}</small></span></div></td>
            <td><strong className="cell-value">{number(card.total_views)}</strong><span className="studio-view-bar" aria-hidden="true"><i style={{ width: Math.max(1, card.total_views / maxViews * 100) + '%' }} /></span></td>
            <td><button className="studio-open" title={'Открыть ролик: ' + card.title} aria-label={'Открыть ролик: ' + card.title}><ArrowUpRight size={19} /></button></td>
          </tr>)}</tbody></table></div> : <div className="studio-empty"><Video size={32} /><h3>{query ? 'Ролики не найдены' : 'Здесь будут ваши ролики'}</h3><p>{query ? 'Попробуйте другое название.' : 'Добавьте первую публикацию.'}</p>{!query && <button className="button button--primary" onClick={createVideo}><Plus size={17} />Добавить ролик</button>}</div>}
          <footer className="studio-content-footer"><span>По общему числу просмотров</span><span>{period}</span></footer>
        </section>
        <aside className="studio-aside">
          <section className="studio-wallet"><div className="studio-wallet-label"><WalletCards size={19} /><span>Доступно к выплате</span></div><strong>{money(data.finance?.available_kopecks)}</strong><dl><div><dt>Зарезервировано</dt><dd>{money(data.finance?.reserved_kopecks)}</dd></div><div><dt>Предварительно начислено</dt><dd>{money(data.finance?.preliminary_kopecks)}</dd></div></dl><button onClick={() => navigate('finance')}>Перейти к выплатам<ArrowRight size={18} /></button></section>
          <button className="studio-review" onClick={() => navigate('publications')}><CheckCircle2 size={20} /><span><strong>{changes ? 'На доработке: ' + changes : pending ? 'На проверке: ' + pending : 'Всё в порядке'}</strong><small>{changes || pending ? 'Перейти к публикациям' : 'Публикации не требуют доработки'}</small></span><ArrowUpRight size={17} /></button>
        </aside>
      </div>
    </>}
  </div>
}
