import { FormEvent, useEffect, useState } from 'react'
import { AlertTriangle, Ban, LoaderCircle, Save, ShieldCheck, UserCog, ArrowLeft } from 'lucide-react'
import { ApiError, CurrentUser, Role, changeAdminUserAccess, changeAdminUserRole, loadAdminUser } from './api'

type Json = Record<string, any>
const roleLabels: Record<Role, string> = { blogger: 'Блогер', moderator: 'Модератор', manager: 'Менеджер', finance: 'Финансы', analyst: 'Аналитик', admin: 'Администратор' }
const statusLabels: Record<string, string> = { active: 'Активен', blocked: 'Заблокирован', suspended: 'Приостановлен', email_pending: 'Ждёт подтверждения email' }

export function AdminUserDrawer({ userId, currentUser, close, changed }: { userId: string; currentUser: CurrentUser; close: () => void; changed: (message: string) => void }) {
  const [account, setAccount] = useState<Json | null>(null)
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState('')
  const [operation, setOperation] = useState<'role' | 'access'>('role')
  useEffect(() => { loadAdminUser(userId).then(setAccount).catch((caught) => setError(caught instanceof ApiError ? caught.message : 'Не удалось загрузить пользователя')).finally(() => setLoading(false)) }, [userId])
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setPending(true); setError('')
    const form = new FormData(event.currentTarget)
    const reason = String(form.get('reason') ?? '').trim()
    try {
      const updated = operation === 'role'
        ? await changeAdminUserRole(userId, String(form.get('role')) as Role, reason)
        : await changeAdminUserAccess(userId, account?.status !== 'blocked', reason)
      setAccount(updated); changed(operation === 'role' ? 'Роль пользователя изменена' : updated.status === 'blocked' ? 'Пользователь заблокирован' : 'Доступ восстановлен')
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось изменить пользователя') }
    finally { setPending(false) }
  }
  return <div className="drawer-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}><aside className="work-drawer" role="dialog" aria-modal="true" aria-labelledby="admin-user-title">
    <header className="work-drawer__head"><div><h2 id="admin-user-title">Пользователь</h2><p>{account?.email ?? 'Доступ и роль'}</p></div><button className="icon-button" onClick={close} title="Закрыть"><ArrowLeft size={19} /></button></header>
    {loading ? <div className="drawer-loading"><LoaderCircle className="spin" size={20} />Загружаем пользователя</div> : <form className="work-form" onSubmit={submit}>
      {account ? <><section className="admin-user-summary"><span><UserCog size={22} /></span><div><strong>{account.email}</strong><small>Создан {new Date(account.created_at).toLocaleDateString('ru-RU')}</small></div><b>{statusLabels[account.status] ?? account.status}</b></section>
      {account.lifecycle ? <section className="form-section"><h3>Жизненный цикл блогера</h3><div className="export-details"><span><small>Последняя активность</small>{new Date(account.lifecycle.last_activity_at).toLocaleString('ru-RU')}</span><span><small>Тип активности</small>{account.lifecycle.last_activity_kind}</span><span><small>Следующий переход</small>{account.lifecycle.next_transition || 'Не запланирован'}</span><span><small>Дата перехода</small>{account.lifecycle.next_transition_at ? new Date(account.lifecycle.next_transition_at).toLocaleString('ru-RU') : '—'}</span></div></section> : null}
      <section className="form-section"><h3><ShieldCheck size={16} />Управление доступом</h3>{account.id === currentUser.id ? <div className="inline-note">Собственную роль и доступ изменять нельзя.</div> : <><div className="payout-actions"><button type="button" className={operation === 'role' ? 'active' : ''} onClick={() => setOperation('role')}><UserCog size={16} />Изменить роль</button><button type="button" className={operation === 'access' ? 'active' : ''} onClick={() => setOperation('access')}><Ban size={16} />{account.status === 'blocked' ? 'Разблокировать' : 'Заблокировать'}</button></div><div className="payout-command-form">{operation === 'role' ? <label className="form-field"><span>Новая роль</span><select name="role" defaultValue={account.role}>{Object.entries(roleLabels).map(([id, label]) => <option value={id} key={id}>{label}</option>)}</select></label> : <div className="inline-note">{account.status === 'blocked' ? 'Будет восстановлен статус, который был до блокировки.' : 'Активные сессии пользователя будут отозваны.'}</div>}<label className="form-field"><span>Причина</span><textarea name="reason" rows={4} minLength={3} maxLength={500} required /></label></div></>}</section></> : null}
      {error ? <div className="auth-error"><AlertTriangle size={16} />{error}</div> : null}<footer className="work-form__actions"><button type="button" className="button button--secondary" onClick={close}>Закрыть</button>{account?.id !== currentUser.id ? <button className="button button--primary" disabled={pending}>{pending ? <LoaderCircle className="spin" size={17} /> : <Save size={17} />}{pending ? 'Сохраняем' : 'Применить'}</button> : null}</footer>
    </form>}
  </aside></div>
}
