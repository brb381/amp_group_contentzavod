import { FormEvent, useEffect, useMemo, useState } from 'react'
import { AlertTriangle, ArrowLeft, Eye, Link2, LoaderCircle, Pencil, Plus, Save, Send, Trash2, Video, X } from 'lucide-react'
import {
  ApiError, createPublication, deletePublication, loadVideoCardDetails,
  submitPublication, updateManualReading, updatePublication, updateVideoCard,
} from './api'
import { platformConfig, readingSourceLabels, riskFlagLabels, visibleRiskFlags } from './platforms'

type Json = Record<string, any>
const statusLabels: Record<string, string> = {
  draft: 'Черновик', pending: 'На проверке', pending_review: 'На модерации',
  approved: 'Одобрено', rejected: 'Отклонено', changes_required: 'Нужны изменения',
  re_review_required: 'Нужна повторная модерация', inactive: 'Неактивно',
  accepted: 'Принято', corrected: 'Исправлено',
}
const number = (value?: number) => value == null ? '—' : new Intl.NumberFormat('ru-RU').format(value)

export function EntityDrawer({ kind, id, payload, close, changed }: {
  kind: 'video-card' | 'reading'; id: string; payload?: Json
  close: () => void; changed: (message: string) => void
}) {
  const [data, setData] = useState<Json | null>(kind === 'reading' ? payload ?? null : null)
  const [loading, setLoading] = useState(kind === 'video-card')
  const [error, setError] = useState('')
  const [pending, setPending] = useState(false)
  const [editingPublication, setEditingPublication] = useState<Json | null>(null)
  const [addingPublication, setAddingPublication] = useState(false)
  const [accountId, setAccountId] = useState('')

  const reload = async () => {
    if (kind === 'video-card') setData(await loadVideoCardDetails(id))
  }
  useEffect(() => {
    if (kind !== 'video-card') return
    let active = true
    loadVideoCardDetails(id)
      .then((value) => { if (active) setData(value) })
      .catch((caught) => { if (active) setError(caught instanceof ApiError ? caught.message : 'Не удалось загрузить карточку') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [kind, id])

  const run = async (task: () => Promise<void>, fallback: string) => {
    setPending(true); setError('')
    try { await task() }
    catch (caught) { setError(caught instanceof ApiError || caught instanceof Error ? caught.message : fallback) }
    finally { setPending(false) }
  }

  const saveCard = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    void run(async () => {
      await updateVideoCard(id, {
        title: String(form.get('title')),
        description: String(form.get('description') || '') || null,
      })
      changed('Карточка ролика обновлена'); await reload()
    }, 'Не удалось обновить карточку')
  }

  const addPublication = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    void run(async () => {
      const created = await createPublication(id, {
        social_account_id: String(form.get('social_account_id')),
        url: String(form.get('url')),
      })
      const submitNow = form.get('submit_now') === 'on'
      if (submitNow) await submitPublication(created.publication?.id ?? created.id)
      setAddingPublication(false); setAccountId('')
      changed(submitNow ? 'Публикация добавлена и отправлена на модерацию' : 'Публикация сохранена как черновик')
      await reload()
    }, 'Не удалось добавить публикацию')
  }

  const savePublication = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const publication = editingPublication
    if (!publication) return
    const url = String(new FormData(event.currentTarget).get('url'))
    void run(async () => {
      await updatePublication(publication.id, url)
      setEditingPublication(null); changed('Ссылка публикации обновлена'); await reload()
    }, 'Не удалось обновить публикацию')
  }
  const removePublication = (publicationId: string) => {
    if (!window.confirm('Удалить черновик публикации из карточки?')) return
    void run(async () => {
      await deletePublication(publicationId); changed('Публикация удалена'); await reload()
    }, 'Не удалось удалить публикацию')
  }
  const sendPublication = (publicationId: string) => {
    void run(async () => {
      await submitPublication(publicationId); changed('Публикация отправлена на модерацию'); await reload()
    }, 'Не удалось отправить публикацию')
  }
  const saveReading = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const value = Number(new FormData(event.currentTarget).get('reading_value'))
    void run(async () => {
      const updated = await updateManualReading(id, value)
      setData(updated); changed('Показание обновлено')
    }, 'Не удалось обновить показание')
  }

  const card = data?.card ?? {}
  const reading = data ?? {}
  const publications: Json[] = data?.publications ?? []
  const approvedAccounts: Json[] = (data?.socialAccounts ?? []).filter((item: Json) =>
    item.status === 'approved' && !item.deleted_at,
  )
  const selectedAccount = approvedAccounts.find((item) => item.id === accountId)
  const platformCounts = useMemo(() => publications.reduce((result: Record<string, number>, item: Json) => {
    result[item.platform] = (result[item.platform] ?? 0) + 1
    return result
  }, {}), [publications])

  return <div className="drawer-layer" role="presentation" onMouseDown={(event) => {
    if (event.target === event.currentTarget) close()
  }}>
    <aside className="work-drawer" role="dialog" aria-modal="true" aria-labelledby="entity-title">
      <header className="work-drawer__head">
        <div>
          <h2 id="entity-title">{kind === 'video-card' ? card.title || 'Карточка ролика' : 'Показание просмотров'}</h2>
          <p>{kind === 'video-card' ? 'Один ролик и все публикации по площадкам' : 'Источник и результат проверки'}</p>
        </div>
        <button className="icon-button" onClick={close} title="Закрыть"><ArrowLeft size={19} /></button>
      </header>
      {loading ? <div className="drawer-loading"><LoaderCircle className="spin" size={20} />Загружаем данные</div> : <div className="work-form">
        {kind === 'video-card' && data ? <>
          <section className="operation-summary">
            <span><Video size={22} /></span>
            <div><small>Товар</small><strong>{card.product_snapshot?.publication_name || card.reported_product?.name || 'Не указан'}</strong></div>
            <b>{statusLabels[card.status] ?? card.status}</b>
          </section>
          <section className="form-section">
            <h3>Карточка</h3>
            <form className="form-grid" onSubmit={saveCard}>
              <label className="form-field form-field--wide"><span>Название</span><input name="title" defaultValue={card.title} required /></label>
              <label className="form-field form-field--wide"><span>Описание</span><textarea name="description" defaultValue={card.description ?? ''} rows={3} /></label>
              <button className="button button--secondary form-inline-action" disabled={pending}><Save size={15} />Сохранить карточку</button>
            </form>
          </section>
          <section className="form-section">
            <div className="section-heading">
              <h3><Link2 size={16} />Публикации</h3>
              <button type="button" className="button button--secondary button--compact" onClick={() => setAddingPublication((value) => !value)}>
                {addingPublication ? <X size={15} /> : <Plus size={15} />}{addingPublication ? 'Отмена' : 'Добавить ссылку'}
              </button>
            </div>
            {addingPublication ? <form className="publication-add" onSubmit={addPublication}>
              <label className="form-field"><span>Социальный аккаунт</span>
                <select name="social_account_id" value={accountId} onChange={(event) => setAccountId(event.target.value)} required>
                  <option value="">Выберите площадку</option>
                  {approvedAccounts.map((item) => <option value={item.id} key={item.id}>{platformConfig(item.platform).label}: {item.url}</option>)}
                </select>
              </label>
              <label className="form-field"><span>Ссылка на публикацию</span>
                <input name="url" type="url" placeholder={selectedAccount ? platformConfig(selectedAccount.platform).publicationPlaceholder : 'Сначала выберите площадку'} disabled={!selectedAccount} required />
              </label>
              <label className="check-field"><input name="submit_now" type="checkbox" defaultChecked />Сразу отправить на модерацию</label>
              <button className="button button--primary" disabled={pending || !selectedAccount}><Plus size={16} />Добавить</button>
              {!approvedAccounts.length ? <div className="inline-note">Сначала добавьте и подтвердите социальный аккаунт в профиле.</div> : null}
            </form> : null}
            <div className="publication-list">
              {publications.map((item: Json) => {
                const platform = platformConfig(item.platform)
                const editable = ['draft', 'changes_required'].includes(item.status)
                const submittable = editable || item.status === 're_review_required'
                const viewCount = item.current_views
                return <article key={item.id}>
                  <span className="publication-preview"><Video size={18} />{item.external_thumbnail_url ? <img src={item.external_thumbnail_url} alt="" loading="lazy" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.hidden = true }} /> : null}<small>{platform.shortLabel}</small></span>
                  <div>
                    <strong>{item.external_title || item.external_author_name || platform.label}</strong>
                    <small className="publication-platform-label">{platform.label}</small>
                    <a href={item.submitted_url} target="_blank" rel="noreferrer">Открыть публикацию</a>
                    <small className="publication-views"><Eye size={13} />{viewCount == null ? 'Просмотры ещё не собраны' : `${number(viewCount)} просмотров`}</small>
                    {item.moderation_reason ? <small className="moderation-reason">{item.moderation_reason}</small> : null}
                  </div>
                  <b>{statusLabels[item.status] ?? item.status}</b>
                  {submittable ? <span className="publication-actions">
                    {editable ? <button type="button" title="Изменить ссылку" onClick={() => setEditingPublication(item)}><Pencil size={14} /></button> : null}
                    <button type="button" title="Отправить на модерацию" onClick={() => sendPublication(item.id)}><Send size={14} /></button>
                    {editable ? <button type="button" title="Удалить черновик" onClick={() => removePublication(item.id)}><Trash2 size={14} /></button> : null}
                  </span> : null}
                </article>
              })}
              {!publications.length ? <div className="inline-note">Публикации ещё не добавлены.</div> : null}
            </div>
            {Object.keys(platformCounts).length > 1 ? <div className="multipublication-summary">
              <strong>{publications.length} ссылок</strong>
              <span>{Object.entries(platformCounts).map(([platform, count]) => `${platformConfig(platform).label}: ${count}`).join(' · ')}</span>
            </div> : null}
            {editingPublication ? <form className="publication-edit" onSubmit={savePublication}>
              <label className="form-field"><span>Новая ссылка</span><input name="url" type="url" placeholder={platformConfig(editingPublication.platform).publicationPlaceholder} defaultValue={editingPublication.submitted_url} required /></label>
              <div><button type="button" className="button button--secondary" onClick={() => setEditingPublication(null)}>Отмена</button><button className="button button--primary" disabled={pending}>Сохранить</button></div>
            </form> : null}
          </section>
        </> : null}
        {kind === 'reading' && data ? <>
          <section className="operation-summary">
            <span><Eye size={22} /></span>
            <div><small>Заявленное значение</small><strong>{number(reading.reported_value)} просмотров</strong></div>
            <b>{statusLabels[reading.status] ?? reading.status}</b>
          </section>
          <section className="form-section">
            <h3>Проверка</h3>
            <div className="export-details">
              <span><small>Источник</small>{readingSourceLabels[reading.source] ?? reading.source ?? readingSourceLabels.manual}</span>
              <span><small>Принятое значение</small>{number(reading.accepted_value ?? reading.reported_value)}</span>
              <span><small>Период</small>{reading.reporting_period ? new Date(reading.reporting_period).toLocaleDateString('ru-RU', { timeZone: 'Europe/Moscow' }) : '—'}</span>
              <span><small>Зафиксировано</small>{reading.captured_at ? new Date(reading.captured_at).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' }) : '—'}</span>
            </div>
            {visibleRiskFlags(reading.risk_flags).length ? <div className="payout-warning"><AlertTriangle size={16} /><span><b>Особенности данных</b>{visibleRiskFlags(reading.risk_flags).map((flag: string) => riskFlagLabels[flag] ?? flag).join(', ')}</span></div> : null}
            {reading.source === 'manual' && reading.status === 'pending' ? <form className="reading-edit" onSubmit={saveReading}>
              <label className="form-field"><span>Исправленное значение</span><input name="reading_value" type="number" min="0" defaultValue={reading.reported_value} required /></label>
              <button className="button button--primary" disabled={pending}><Save size={16} />Сохранить показание</button>
            </form> : null}
          </section>
        </> : null}
        {error ? <div className="auth-error"><AlertTriangle size={16} />{error}</div> : null}
        <footer className="work-form__actions"><button className="button button--secondary" onClick={close}>Закрыть</button></footer>
      </div>}
    </aside>
  </div>
}
