import { FormEvent, ReactNode, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CalendarDays, Download, FileSpreadsheet, LoaderCircle, RefreshCw, ArrowLeft } from 'lucide-react'
import { ApiError, createExport, downloadExport, loadExport } from './api'
import { exportFormatLabel, exportTypeLabel, exportTypeLabels, integrationErrorLabel, statusLabel } from './labels'

type Json = Record<string, any>
const ongoing = ['pending', 'queued', 'processing', 'retry_wait']

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="form-field"><span>{label}</span>{children}</label>
}
function localDate(offsetDays = 0) {
  const value = new Date(); value.setDate(value.getDate() + offsetDays)
  return new Date(value.getTime() - value.getTimezoneOffset() * 60_000).toISOString().slice(0, 10)
}

export function ExportDrawer({ exportId, close, changed }: { exportId?: string; close: () => void; changed: (message: string) => void }) {
  const [job, setJob] = useState<Json | null>(null)
  const [loading, setLoading] = useState(Boolean(exportId))
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [exportType, setExportType] = useState('publications')
  const key = useRef(crypto.randomUUID())
  const payoutExport = exportType.startsWith('payout_')

  const reload = async (quiet = false) => {
    if (!exportId) return
    if (!quiet) setLoading(true)
    try { setJob(await loadExport(exportId)); setError('') }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось загрузить выгрузку') }
    finally { if (!quiet) setLoading(false) }
  }
  useEffect(() => { void reload() }, [exportId])
  useEffect(() => {
    if (!exportId || !job || !ongoing.includes(job.status)) return
    const timer = window.setInterval(() => void reload(true), 4000)
    return () => window.clearInterval(timer)
  }, [exportId, job?.status])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setPending(true); setError('')
    const form = new FormData(event.currentTarget)
    const value = (name: string) => String(form.get(name) ?? '')
    const filters = payoutExport
      ? { requested_from: value('date_from'), requested_to: value('date_to'), status: value('status') || null, recipient_type: null, blogger_id: null, request_number: null, approved_from: null, approved_to: null }
      : { date_from: value('date_from'), date_to: value('date_to'), status: value('status') || null, blogger_id: null, platform: value('platform') || null, brand: null, product_id: null }
    try {
      await createExport({ idempotency_key: key.current, export_type: exportType, format: value('format'), filters })
      changed('Выгрузка поставлена в очередь'); close()
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось создать выгрузку') }
    finally { setPending(false) }
  }
  const getFile = async () => {
    if (!job?.id) return
    setPending(true); setError('')
    try { await downloadExport(job.id); changed('Скачивание началось') }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось скачать файл') }
    finally { setPending(false) }
  }

  return <div className="drawer-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}><aside className="work-drawer" role="dialog" aria-modal="true" aria-labelledby="export-title">
    <header className="work-drawer__head"><div><h2 id="export-title">{exportId ? 'Выгрузка' : 'Новая выгрузка'}</h2><p>{exportId ? exportTypeLabel(job?.export_type) : 'Файл формируется в фоновом режиме'}</p></div><button type="button" className="icon-button" onClick={close} title="Закрыть"><ArrowLeft size={19} /></button></header>
    {loading ? <div className="drawer-loading"><LoaderCircle className="spin" size={20} />Получаем состояние</div> : exportId && job ? <div className="work-form">
      <section className="export-state"><span className={`export-state__icon export-state__icon--${job.status}`}><FileSpreadsheet size={23} /></span><div><span>{statusLabel(job.status)}</span><strong>{exportTypeLabel(job.export_type)}</strong><small>{exportFormatLabel(job.format)} · {job.row_count == null ? 'число строк пока неизвестно' : job.row_count + ' строк'}</small></div></section>
      <section className="form-section"><h3><CalendarDays size={16} />Параметры</h3><div className="export-details"><span><small>Создано</small>{job.created_at ? new Date(job.created_at).toLocaleString('ru-RU') : '—'}</span><span><small>Попыток</small>{job.attempt_count ?? 0}</span><span><small>Актуальность данных</small>{job.data_as_of ? new Date(job.data_as_of).toLocaleString('ru-RU') : '—'}</span><span><small>Хранится до</small>{job.expires_at ? new Date(job.expires_at).toLocaleString('ru-RU') : '—'}</span></div>{job.error_code ? <div className="payout-warning"><AlertTriangle size={16} /><span><b>Не удалось сформировать файл</b>{integrationErrorLabel(job.error_code)}</span></div> : null}</section>
      {error ? <div className="auth-error"><AlertTriangle size={16} />{error}</div> : null}
      <footer className="work-form__actions"><button type="button" className="button button--secondary" onClick={() => void reload()}><RefreshCw size={17} />Обновить</button>{job.status === 'ready' ? <button type="button" className="button button--primary" onClick={getFile} disabled={pending}><Download size={17} />Скачать</button> : null}</footer>
    </div> : <form className="work-form" onSubmit={submit}>
      <section className="form-section"><h3><FileSpreadsheet size={16} />Состав файла</h3><div className="form-grid">
        <Field label="Тип данных"><select name="export_type" value={exportType} onChange={(event) => setExportType(event.target.value)}>{Object.entries(exportTypeLabels).map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select></Field>
        <Field label="Формат"><select name="format" defaultValue="xlsx"><option value="xlsx">Excel (.xlsx)</option><option value="csv">CSV (.csv)</option></select></Field>
        <Field label="Дата с"><input name="date_from" type="date" defaultValue={localDate(-30)} required /></Field>
        <Field label="Дата по"><input name="date_to" type="date" defaultValue={localDate()} required /></Field>
        <Field label="Статус">{payoutExport ? <select name="status" defaultValue=""><option value="">Все</option><option value="requested">Запрошено</option><option value="under_review">На проверке</option><option value="approved">Одобрено</option><option value="paid">Оплачено</option><option value="rejected">Отклонено</option></select> : <input name="status" placeholder="Все статусы" maxLength={64} />}</Field>
        {!payoutExport ? <Field label="Платформа"><select name="platform" defaultValue=""><option value="">Все</option><option value="vk">VK</option><option value="youtube">YouTube</option><option value="tiktok">TikTok</option><option value="dzen">Дзен</option><option value="rutube">Rutube</option></select></Field> : null}
      </div></section>{error ? <div className="auth-error"><AlertTriangle size={16} />{error}</div> : null}<footer className="work-form__actions"><button type="button" className="button button--secondary" onClick={close}>Отмена</button><button className="button button--primary" disabled={pending}>{pending ? <LoaderCircle className="spin" size={17} /> : <FileSpreadsheet size={17} />}{pending ? 'Создаём' : 'Сформировать'}</button></footer>
    </form>}
  </aside></div>
}
