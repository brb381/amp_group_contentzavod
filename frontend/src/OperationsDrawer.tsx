import { FormEvent, ReactNode, useEffect, useState } from 'react'
import { AlertTriangle, Calculator, Copy, ExternalLink, FileText, LoaderCircle, Package, Save, ShieldCheck, ArrowLeft } from 'lucide-react'
import {
  ApiError, Role, createBillingRate, loadCalculationPeriod, loadLegalDocument,
  loadProduct, loadSecurityEvent, publishLegalDocument, runCalculationPeriod, saveProduct,
} from './api'

type Json = Record<string, any>
export type OperationKind = 'product' | 'period' | 'rate' | 'security' | 'legal'
const rubles = (value = 0) => new Intl.NumberFormat('ru-RU').format(Math.round(value / 100)) + ' ₽'
const numeric = (value = 0) => new Intl.NumberFormat('ru-RU').format(value)

function Field({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  return <label className={wide ? 'form-field form-field--wide' : 'form-field'}><span>{label}</span>{children}</label>
}

export function OperationsDrawer({ kind, id, role, close, changed }: { kind: OperationKind; id?: string; role: Role; close: () => void; changed: (message: string) => void }) {
  const [data, setData] = useState<Json | null>(null)
  const [loading, setLoading] = useState(Boolean(id))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [documentType, setDocumentType] = useState('program_terms')
  const editableProduct = ['moderator', 'admin'].includes(role)
  const calculationReviewer = ['moderator', 'admin'].includes(role)
  const load = async () => {
    if (!id) return
    setLoading(true); setError('')
    try {
      const value = kind === 'product' ? await loadProduct(id) : kind === 'period' ? await loadCalculationPeriod(id) : kind === 'security' ? await loadSecurityEvent(id) : await loadLegalDocument(id)
      setData(value)
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось загрузить данные') }
    finally { setLoading(false) }
  }
  useEffect(() => { void load() }, [kind, id])
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setPending(true); setError('')
    const form = new FormData(event.currentTarget)
    const value = (name: string) => String(form.get(name) ?? '').trim()
    try {
      if (kind === 'product') {
        await saveProduct(id, { brand: value('brand'), model_name: value('model_name'), publication_name: value('publication_name'), sku: value('sku'), required_hashtags: value('hashtags').split(',').map((item) => item.trim()).filter(Boolean), content_hint: value('content_hint') || null, marketplace_links: value('marketplace_links').split(/\r?\n/).map((line) => { const [label, ...url] = line.split('|'); return { label: label.trim(), url: url.join('|').trim() } }).filter((item) => item.label && item.url), is_active: form.get('is_active') === 'on' })
        changed(id ? 'Товар обновлён' : 'Товар создан'); return close()
      }
      if (kind === 'rate') {
        await createBillingRate({ rate_kopecks_per_view: Number(value('rate')), effective_from_period: value('period') + '-01', reason: value('reason') })
        changed('Новая ставка создана'); return close()
      }
      if (kind === 'legal') {
        await publishLegalDocument({ document_type: value('document_type'), version: value('version'), title: value('title'), content_markdown: value('content'), requires_reacceptance: form.get('requires_reacceptance') === 'on', change_summary: value('change_summary') || null })
        changed('Новая версия документа опубликована'); return close()
      }
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось сохранить данные') }
    finally { setPending(false) }
  }
  const periodCommand = async (command: 'recalculations' | 'confirmations') => {
    if (!id) return
    setPending(true); setError('')
    try { await runCalculationPeriod(id, command); changed(command === 'confirmations' ? 'Период подтверждён' : 'Пересчёт запрошен'); await load() }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось выполнить команду') }
    finally { setPending(false) }
  }
  const titles: Record<OperationKind, string> = { product: id ? 'Товар' : 'Новый товар', period: 'Расчётный период', rate: 'Новая ставка', security: 'Событие безопасности', legal: id ? 'Юридический документ' : 'Новая версия документа' }
  return <div className="drawer-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}><aside className="work-drawer" role="dialog" aria-modal="true" aria-labelledby="operation-title"><header className="work-drawer__head"><div><h2 id="operation-title">{titles[kind]}</h2><p>{data?.publication_name || data?.title || data?.period || data?.action || 'Параметры и состояние'}</p></div><button className="icon-button" onClick={close} title="Закрыть"><ArrowLeft size={19} /></button></header>{loading ? <div className="drawer-loading"><LoaderCircle className="spin" size={20} />Загружаем данные</div> : <form className="work-form" onSubmit={submit}>
    {kind === 'product' ? <section className="form-section"><h3><Package size={16} />Карточка товара</h3><fieldset className="form-grid" disabled={!editableProduct}><Field label="Бренд"><select name="brand" defaultValue={data?.brand ?? 'AMP'}><option>AMP</option><option>AirTone</option><option>CrioLight</option></select></Field><Field label="Артикул"><input name="sku" defaultValue={data?.sku ?? ''} required /></Field><Field label="Модель"><input name="model_name" defaultValue={data?.model_name ?? ''} required /></Field><Field label="Название для публикации"><input name="publication_name" defaultValue={data?.publication_name ?? ''} required /></Field><Field label="Обязательные хэштеги" wide><input name="hashtags" defaultValue={(data?.required_hashtags ?? []).join(', ')} placeholder="#amp, #обзор" required /></Field><Field label="Подсказка автору" wide><textarea name="content_hint" defaultValue={data?.content_hint ?? ''} rows={4} /></Field><Field label="Ссылки маркетплейсов" wide><textarea name="marketplace_links" defaultValue={(data?.marketplace_links ?? []).map((item: Json) => item.label + ' | ' + item.url).join('\n')} placeholder={'Ozon | https://...\nWildberries | https://...'} rows={4} /></Field><label className="check-field"><input name="is_active" type="checkbox" defaultChecked={data?.is_active ?? true} />Товар активен</label></fieldset>{data ? <div className="catalog-reference"><button type="button" onClick={() => void navigator.clipboard.writeText(data.publication_name)}><Copy size={14} />Название для публикации</button><button type="button" onClick={() => void navigator.clipboard.writeText(data.sku)}><Copy size={14} />Артикул</button><button type="button" onClick={() => void navigator.clipboard.writeText((data.required_hashtags ?? []).join(' '))}><Copy size={14} />Хэштеги</button>{(data.marketplace_links ?? []).map((item: Json) => <a key={item.url} href={item.url} target="_blank" rel="noreferrer"><ExternalLink size={14} />{item.label}</a>)}</div> : null}{!editableProduct ? <div className="inline-note">Для вашей роли каталог доступен только для чтения.</div> : null}</section> : null}
    {kind === 'rate' ? <section className="form-section"><h3><Calculator size={16} />Условия ставки</h3><div className="form-grid"><Field label="Копеек за просмотр"><input name="rate" type="number" min="1" max="1000000" required /></Field><Field label="Действует с месяца"><input name="period" type="month" required /></Field><Field label="Причина" wide><textarea name="reason" minLength={3} rows={4} required /></Field></div></section> : null}
    {kind === 'period' && data ? <><section className="operation-summary"><span><Calculator size={22} /></span><div><small>К выплате</small><strong>{rubles(data.total_payable_kopecks)}</strong></div><b>{data.status}</b></section><section className="form-section"><h3>Итоги периода</h3><div className="export-details"><span><small>Просмотры</small>{numeric(data.total_views)}</span><span><small>Начислено</small>{rubles(data.total_amount_kopecks)}</span><span><small>Корректировки</small>{rubles(data.total_adjustment_kopecks)}</span><span><small>Публикаций</small>{data.accruals?.length ?? 0}</span></div>{data.accruals?.some((item: Json) => item.risk_flags?.length) ? <div className="payout-warning"><AlertTriangle size={16} /><span><b>Есть риск-флаги</b>Проверьте начисления перед подтверждением.</span></div> : null}</section>{calculationReviewer && data.status !== 'confirmed' ? <section className="form-section"><h3><ShieldCheck size={16} />Команды</h3><div className="payout-actions"><button type="button" onClick={() => void periodCommand('recalculations')} disabled={pending}>Запросить пересчёт</button>{data.status === 'preliminary' ? <button type="button" onClick={() => void periodCommand('confirmations')} disabled={pending}>Подтвердить период</button> : null}</div></section> : null}</> : null}
    {kind === 'security' && data ? <><section className="operation-summary"><span><ShieldCheck size={22} /></span><div><small>Действие</small><strong>{data.action}</strong></div><b>{data.result}</b></section><section className="form-section"><h3>Контекст запроса</h3><div className="export-details"><span><small>Время</small>{new Date(data.occurred_at).toLocaleString('ru-RU')}</span><span><small>Роль</small>{data.actor_role || 'Система'}</span><span><small>IP-адрес</small>{data.ip_address}</span><span><small>Request ID</small>{data.request_id}</span></div><pre className="metadata-view">{JSON.stringify(data.event_metadata ?? {}, null, 2)}</pre></section></> : null}
    {kind === 'legal' ? id && data ? <section className="form-section"><h3><FileText size={16} />{data.title}</h3><div className="document-meta">Версия {data.version} · редакция {data.revision} · {data.is_current ? 'действующая' : 'архивная'}</div><pre className="document-content">{data.content_markdown}</pre></section> : <section className="form-section"><h3><FileText size={16} />Публикация документа</h3><div className="form-grid"><Field label="Тип"><select name="document_type" value={documentType} onChange={(event) => setDocumentType(event.target.value)}><option value="program_terms">Условия программы</option><option value="personal_data_consent">Согласие на обработку данных</option><option value="privacy_policy">Политика конфиденциальности</option></select></Field><Field label="Версия"><input name="version" pattern="[A-Za-z0-9._-]+" required /></Field><Field label="Название" wide><input name="title" minLength={3} required /></Field><Field label="Текст Markdown" wide><textarea name="content" minLength={20} rows={12} required /></Field><Field label="Описание изменений" wide><textarea name="change_summary" rows={3} /></Field><label className="check-field"><input name="requires_reacceptance" type="checkbox" disabled={documentType === 'privacy_policy'} />Требовать повторное принятие</label></div></section> : null}
    {error ? <div className="auth-error"><AlertTriangle size={16} />{error}</div> : null}<footer className="work-form__actions"><button type="button" className="button button--secondary" onClick={close}>Закрыть</button>{(kind === 'product' && editableProduct) || kind === 'rate' || (kind === 'legal' && !id) ? <button className="button button--primary" disabled={pending}>{pending ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />}{pending ? 'Сохраняем' : 'Сохранить'}</button> : null}</footer>
  </form>}</aside></div>
}
