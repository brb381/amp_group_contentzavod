import { FormEvent, ReactNode, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, CheckCircle2, CircleDollarSign, Clock3, CreditCard,
  FileCheck2, LoaderCircle, ReceiptText, Save, ShieldCheck, ArrowLeft,
} from 'lucide-react'
import {
  ApiError, PayoutCommand, PayoutMode, Role, createPayoutRequest,
  loadPayout, loadPayoutDetails, runPayoutCommand, savePayoutDetails,
} from './api'

type Json = Record<string, any>

const statusLabels: Record<string, string> = {
  requested: 'Запрошено', under_review: 'На проверке', approved: 'Одобрено',
  paid: 'Оплачено', rejected: 'Отклонено', awaiting_receipt: 'Ожидается чек',
  overdue: 'Просрочено', received: 'Получен', pending_payment: 'До оплаты',
  not_applicable: 'Не требуется',
}
const eventLabels: Record<string, string> = {
  requested: 'Заявка создана', review_started: 'Проверка начата',
  approved: 'Выплата одобрена', rejected: 'Выплата отклонена',
  paid: 'Оплата зафиксирована', receipt_received: 'Чек получен',
}
const commandLabels: Record<PayoutCommand, string> = {
  review: 'Взять в работу', approve: 'Одобрить', reject: 'Отклонить',
  payment: 'Зафиксировать оплату', receipt: 'Зафиксировать чек',
}

const rubles = (kopecks = 0) => new Intl.NumberFormat('ru-RU').format(Math.round(kopecks / 100)) + ' ₽'
const showDate = (value?: string) => value ? new Intl.DateTimeFormat('ru-RU', {
  day: '2-digit', month: '2-digit', year: 'numeric',
}).format(new Date(value)) : '—'
const today = () => {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 10)
}

function Field({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  return <label className={wide ? 'form-field form-field--wide' : 'form-field'}><span>{label}</span>{children}</label>
}

function Detail({ label, value }: { label: string; value: string }) {
  return <div className="payout-detail"><span>{label}</span><strong>{value}</strong></div>
}

function availableCommands(payout: Json, role: Role): PayoutCommand[] {
  const commands: PayoutCommand[] = []
  if (payout.status === 'requested' && ['manager', 'admin'].includes(role)) commands.push('review')
  if (payout.status === 'under_review' && ['manager', 'admin'].includes(role)) commands.push('approve', 'reject')
  if (payout.status === 'approved') {
    if (['finance', 'admin'].includes(role)) commands.push('payment')
    if (['manager', 'finance', 'admin'].includes(role)) commands.push('reject')
  }
  if (payout.status === 'paid' && ['awaiting_receipt', 'overdue'].includes(payout.receipt_status)
    && ['manager', 'finance', 'admin'].includes(role)) commands.push('receipt')
  return commands
}

export function PayoutDrawer({ mode, payoutId, role, close, completed }: {
  mode: PayoutMode; payoutId?: string; role: Role; close: () => void;
  completed: (message: string) => void;
}) {
  const [data, setData] = useState<Json | null>(null)
  const [loading, setLoading] = useState(mode !== 'request')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [command, setCommand] = useState<PayoutCommand | null>(null)
  const idempotencyKeys = useRef<Record<string, string>>({})
  const keyFor = (operation: string) => {
    idempotencyKeys.current[operation] ??= crypto.randomUUID()
    return idempotencyKeys.current[operation]
  }

  useEffect(() => {
    if (mode === 'request') return
    const request = mode === 'requisites'
      ? loadPayoutDetails().catch((caught) => {
        if (caught instanceof ApiError && caught.status === 404) return {}
        throw caught
      })
      : loadPayout(payoutId!, role)
    request.then(setData).catch((caught) => {
      setError(caught instanceof ApiError ? caught.message : 'Не удалось получить данные')
    }).finally(() => setLoading(false))
  }, [mode, payoutId, role])

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setPending(true); setError('')
    const form = new FormData(event.currentTarget)
    const value = (name: string) => String(form.get(name) ?? '').trim()
    try {
      if (mode === 'requisites') {
        await savePayoutDetails(value('sbp_phone'), value('bank_name'))
        return completed('Реквизиты сохранены')
      }
      if (mode === 'request') {
        await createPayoutRequest(keyFor('request'))
        return completed('Заявка на выплату создана')
      }
      if (!command || !payoutId) return
      const payload: Json = {}
      if (command === 'review') payload.comment = value('comment') || null
      if (command === 'approve') {
        payload.requisites_verified = true
        payload.self_employment_verified = data?.recipient_type === 'self_employed'
          ? form.get('self_employment_verified') === 'on' : null
        payload.comment = value('comment') || null
      }
      if (command === 'reject') {
        payload.reason = value('reason')
        payload.comment = value('comment') || null
      }
      if (command === 'payment') {
        payload.paid_on = value('paid_on')
        payload.payment_reference = value('payment_reference') || null
      }
      if (command === 'receipt') payload.received_on = value('received_on')
      await runPayoutCommand(payoutId, command, keyFor(command), payload)
      completed(commandLabels[command] + ': сохранено')
    } catch (caught) {
      setError(caught instanceof ApiError || caught instanceof Error ? caught.message : 'Не удалось выполнить операцию')
    } finally { setPending(false) }
  }

  const commands = data ? availableCommands(data, role) : []
  const titles: Record<PayoutMode, [string, string]> = {
    requisites: ['Реквизиты для выплаты', 'СБП и банк получателя'],
    request: ['Запросить выплату', 'На весь доступный подтверждённый баланс'],
    details: ['Заявка на выплату', data?.request_number ?? 'Состояние и история обработки'],
  }
  const [title, subtitle] = titles[mode]
  return <div className="drawer-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}>
    <aside className="work-drawer payout-drawer" role="dialog" aria-modal="true" aria-labelledby="payout-title">
      <header className="work-drawer__head"><div><h2 id="payout-title">{title}</h2><p>{subtitle}</p></div><button type="button" className="icon-button" onClick={close} title="Закрыть"><ArrowLeft size={19} /></button></header>
      {loading ? <div className="drawer-loading"><LoaderCircle className="spin" size={20} />Загружаем данные</div> :
      <form className="work-form" onSubmit={submit}>
        {mode === 'requisites' ? <section className="form-section"><h3><CreditCard size={16} />Получение денег</h3><p className="form-hint">Реквизиты копируются в новую заявку и после её создания уже не меняются.</p><div className="form-grid">
          <Field label="Телефон СБП" wide><input name="sbp_phone" defaultValue={data?.sbp_phone ?? '+7'} pattern="\+7[0-9]{10}" placeholder="+79001234567" required /></Field>
          <Field label="Банк" wide><input name="bank_name" defaultValue={data?.bank_name ?? ''} maxLength={255} placeholder="Название банка" /></Field>
        </div></section> : null}
        {mode === 'request' ? <section className="payout-confirm"><span><CircleDollarSign size={22} /></span><h3>Создать заявку на выплату?</h3><p>Текущие реквизиты будут сохранены в заявке, а доступный подтверждённый баланс — зарезервирован для выплаты.</p><div><ShieldCheck size={16} />Заявка будет создана один раз</div></section> : null}
        {mode === 'details' && data ? <>
          <section className="payout-summary"><div><span>Сумма</span><strong>{rubles(data.amount_kopecks)}</strong></div><span className={`payout-status payout-status--${data.status}`}>{statusLabels[data.status] ?? data.status}</span></section>
          <section className="form-section"><h3><CreditCard size={16} />Получатель</h3><div className="payout-details-grid">
            <Detail label="Получатель" value={data.recipient_display_name || data.recipient_full_name} />
            <Detail label="Тип" value={data.recipient_type === 'self_employed' ? 'Самозанятый' : 'Физлицо'} />
            <Detail label="Телефон СБП" value={data.sbp_phone} /><Detail label="Банк" value={data.bank_name || 'Не указан'} />
            <Detail label="Запрошено" value={showDate(data.requested_at)} /><Detail label="Срок оплаты" value={showDate(data.payment_due_date)} />
            {data.paid_on ? <Detail label="Оплачено" value={showDate(data.paid_on)} /> : null}
            {data.receipt_status ? <Detail label="Чек" value={statusLabels[data.receipt_status] ?? data.receipt_status} /> : null}
          </div>{data.rejection_reason ? <div className="payout-warning"><AlertTriangle size={16} /><span><b>Причина отклонения</b>{data.rejection_reason}</span></div> : null}</section>
          {Array.isArray(data.history) ? <section className="form-section"><h3><Clock3 size={16} />История</h3><div className="payout-history">{data.history.map((item: Json) => <div key={item.id}><span><CheckCircle2 size={15} /></span><div><strong>{eventLabels[item.action] ?? item.action}</strong><small>{showDate(item.created_at)}{item.comment ? ' · ' + item.comment : ''}</small></div></div>)}</div></section> : null}
          {commands.length ? <section className="form-section"><h3><FileCheck2 size={16} />Действия</h3><div className="payout-actions">{commands.map((item) => <button key={item} type="button" className={command === item ? 'active' : ''} onClick={() => setCommand(item)}>{item === 'reject' ? <AlertTriangle size={16} /> : item === 'payment' ? <CircleDollarSign size={16} /> : item === 'receipt' ? <ReceiptText size={16} /> : <FileCheck2 size={16} />}{commandLabels[item]}</button>)}</div>
            {command ? <div className="payout-command-form">
              {['review', 'approve'].includes(command) ? <Field label="Комментарий" wide><textarea name="comment" rows={3} /></Field> : null}
              {command === 'approve' && data.recipient_type === 'self_employed' ? <label className="check-field"><input name="self_employment_verified" type="checkbox" required /><span>Статус самозанятого проверен</span></label> : null}
              {command === 'reject' ? <><Field label="Причина отклонения" wide><textarea name="reason" rows={3} minLength={3} required /></Field><Field label="Внутренний комментарий" wide><textarea name="comment" rows={2} /></Field></> : null}
              {command === 'payment' ? <div className="form-grid"><Field label="Дата оплаты"><input name="paid_on" type="date" defaultValue={today()} required /></Field><Field label="Номер платежа"><input name="payment_reference" maxLength={255} /></Field></div> : null}
              {command === 'receipt' ? <Field label="Дата получения чека"><input name="received_on" type="date" defaultValue={today()} required /></Field> : null}
            </div> : null}
          </section> : null}
        </> : null}
        {error ? <div className="auth-error"><AlertTriangle size={16} />{error}</div> : null}
        <footer className="work-form__actions"><button type="button" className="button button--secondary" onClick={close}>{mode === 'details' && !commands.length ? 'Закрыть' : 'Отмена'}</button>{mode !== 'details' || commands.length ? <button className="button button--primary" disabled={pending || (mode === 'details' && !command)}>{pending ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />}{pending ? 'Сохраняем' : mode === 'request' ? 'Создать заявку' : mode === 'details' ? 'Выполнить действие' : 'Сохранить'}</button> : null}</footer>
      </form>}
    </aside>
  </div>
}
