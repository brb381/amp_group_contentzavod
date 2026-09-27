import { FormEvent, ReactNode, useEffect, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, LoaderCircle, MessageSquareText, Send, UserCheck, ArrowLeft } from 'lucide-react'
import {
  ApiError, CurrentUser, assignSupportTicket, createSupportTicket, decideRecovery,
  loadSupportTicket, postSupportMessage, transitionSupportTicket,
} from './api'
import { statusLabel, supportCategoryLabel, supportCategoryLabels } from './labels'

type Json = Record<string, any>
type SupportMode = 'create' | 'details'

const transitions: Record<string, string[]> = {
  new: ['in_progress', 'waiting_blogger', 'resolved'],
  in_progress: ['waiting_blogger', 'resolved'],
  waiting_blogger: ['in_progress', 'resolved'],
  resolved: ['in_progress', 'closed'], closed: [],
}

function Field({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  return <label className={wide ? 'form-field form-field--wide' : 'form-field'}><span>{label}</span>{children}</label>
}

const displayTime = (value?: string) => value ? new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit',
}).format(new Date(value)) : ''

export function SupportDrawer({ mode, ticketId, user, close, changed }: {
  mode: SupportMode; ticketId?: string; user: CurrentUser; close: () => void; changed: (message: string) => void;
}) {
  const [ticket, setTicket] = useState<Json | null>(null)
  const [loading, setLoading] = useState(mode === 'details')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [operation, setOperation] = useState('message')
  const keys = useRef<Record<string, string>>({})
  const keyFor = (name: string) => keys.current[name] ??= crypto.randomUUID()
  const staff = user.role !== 'blogger'

  const reload = async () => {
    if (!ticketId) return
    setLoading(true); setError('')
    try { setTicket(await loadSupportTicket(ticketId, user.role)) }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось загрузить обращение') }
    finally { setLoading(false) }
  }
  useEffect(() => { void reload() }, [ticketId, user.role])

  const finishInline = async (message: string) => {
    keys.current = {}; changed(message); await reload()
  }
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setPending(true); setError('')
    const form = new FormData(event.currentTarget)
    const value = (name: string) => String(form.get(name) ?? '').trim()
    try {
      if (mode === 'create') {
        await createSupportTicket({
          idempotency_key: keyFor('create'), category: value('category'),
          subject: value('subject'), body: value('body'), related_object_type: null, related_object_id: null,
        })
        changed('Обращение создано'); return close()
      }
      if (!ticketId) return
      if (operation === 'message') {
        await postSupportMessage(ticketId, user.role, value('body'), keyFor('message:' + value('body')))
        ;(event.currentTarget.elements.namedItem('body') as HTMLTextAreaElement).value = ''
        return await finishInline('Сообщение отправлено')
      }
      if (operation === 'assign') {
        await assignSupportTicket(ticketId, user.id, value('reason'), keyFor('assign'))
        return await finishInline('Обращение назначено вам')
      }
      if (operation === 'status') {
        await transitionSupportTicket(ticketId, value('to_status'), value('reason'), keyFor('status:' + value('to_status')))
        return await finishInline('Статус обращения обновлён')
      }
      await decideRecovery(ticketId, value('decision'), value('reason'), keyFor('recovery:' + value('decision')))
      await finishInline('Решение по восстановлению сохранено')
    } catch (caught) {
      setError(caught instanceof ApiError || caught instanceof Error ? caught.message : 'Не удалось выполнить действие')
    } finally { setPending(false) }
  }

  const canMessage = ticket?.status !== 'closed' && !(staff && ticket?.status === 'resolved')
  const allowedTransitions = transitions[ticket?.status ?? ''] ?? []
  return <div className="drawer-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}>
    <aside className="work-drawer support-drawer" role="dialog" aria-modal="true" aria-labelledby="support-title">
      <header className="work-drawer__head"><div><h2 id="support-title">{mode === 'create' ? 'Новое обращение' : ticket?.subject ?? 'Обращение'}</h2><p>{mode === 'create' ? 'Вопрос команде поддержки' : ticket?.ticket_number ?? 'Загружаем данные'}</p></div><button type="button" className="icon-button" onClick={close} title="Закрыть"><ArrowLeft size={19} /></button></header>
      {loading ? <div className="drawer-loading"><LoaderCircle className="spin" size={20} />Загружаем диалог</div> : <form className="work-form" onSubmit={submit}>
        {mode === 'create' ? <section className="form-section"><h3><MessageSquareText size={16} />Суть вопроса</h3><div className="form-grid">
          <Field label="Категория"><select name="category" defaultValue="general">{Object.entries(supportCategoryLabels).map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select></Field>
          <Field label="Тема" wide><input name="subject" minLength={3} maxLength={200} required /></Field>
          <Field label="Сообщение" wide><textarea name="body" rows={7} maxLength={10000} required /></Field>
        </div></section> : ticket ? <>
          <section className="support-meta"><span>{supportCategoryLabel(ticket.category)}</span><b>{statusLabel(ticket.status)}</b>{staff ? <small>{ticket.assigned_to_user_id ? 'Назначено сотруднику' : 'Без исполнителя'}</small> : null}</section>
          <section className="support-thread" aria-label="Переписка">{(ticket.messages ?? []).map((message: Json) => {
            const mine = staff ? message.author_type === 'staff' && message.author_user_id === user.id : message.author_type !== 'staff'
            return <article className={mine ? 'support-message support-message--mine' : 'support-message'} key={message.id}><div><b>{message.author_type === 'staff' ? 'Команда AMP' : 'Блогер'}</b><time>{displayTime(message.created_at)}</time></div><p>{message.body}</p></article>
          })}{!ticket.messages?.length ? <div className="support-empty">Сообщений пока нет</div> : null}</section>
          {staff ? <section className="support-operations"><button type="button" className={operation === 'message' ? 'active' : ''} onClick={() => setOperation('message')}><MessageSquareText size={15} />Ответить</button>{!ticket.assigned_to_user_id ? <button type="button" className={operation === 'assign' ? 'active' : ''} onClick={() => setOperation('assign')}><UserCheck size={15} />Взять себе</button> : null}{allowedTransitions.length ? <button type="button" className={operation === 'status' ? 'active' : ''} onClick={() => setOperation('status')}><CheckCircle2 size={15} />Статус</button> : null}{ticket.category === 'account_recovery' && ['moderator', 'admin'].includes(user.role) ? <button type="button" className={operation === 'recovery' ? 'active' : ''} onClick={() => setOperation('recovery')}><UserCheck size={15} />Восстановление</button> : null}</section> : null}
          <section className="form-section support-compose">
            {operation === 'message' ? <Field label="Сообщение" wide><textarea name="body" rows={4} maxLength={10000} required disabled={!canMessage} placeholder={canMessage ? 'Введите ответ' : 'Для ответа измените статус обращения'} /></Field> : null}
            {operation === 'assign' ? <Field label="Комментарий" wide><textarea name="reason" rows={3} maxLength={2000} /></Field> : null}
            {operation === 'status' ? <div className="form-grid"><Field label="Новый статус"><select name="to_status">{allowedTransitions.map((item) => <option value={item} key={item}>{statusLabel(item)}</option>)}</select></Field><Field label="Причина" wide><textarea name="reason" rows={3} /></Field></div> : null}
            {operation === 'recovery' ? <div className="form-grid"><Field label="Решение"><select name="decision"><option value="approve">Восстановить</option><option value="reject">Отказать</option></select></Field><Field label="Обоснование" wide><textarea name="reason" minLength={3} rows={3} required /></Field></div> : null}
          </section>
        </> : null}
        {error ? <div className="auth-error"><AlertTriangle size={16} />{error}</div> : null}
        <footer className="work-form__actions"><button type="button" className="button button--secondary" onClick={close}>Закрыть</button><button className="button button--primary" disabled={pending || (mode === 'details' && operation === 'message' && !canMessage)}>{pending ? <LoaderCircle className="spin" size={17} /> : <Send size={17} />}{pending ? 'Отправляем' : mode === 'create' ? 'Создать' : operation === 'message' ? 'Отправить' : 'Применить'}</button></footer>
      </form>}
    </aside>
  </div>
}
