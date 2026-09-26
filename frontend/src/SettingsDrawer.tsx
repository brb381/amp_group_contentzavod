import { FormEvent, ReactNode, useEffect, useRef, useState } from 'react'
import {
  Activity, AlertTriangle, Bell, CheckCircle2, FileCheck2, LayoutDashboard,
  LoaderCircle, LogOut, MailCheck, Save, ShieldAlert, UserRound, ArrowLeft, Building2, ExternalLink,
} from 'lucide-react'
import {
  ApiError, CurrentUser, acceptLegalDocument, cancelAccountDeletion, createNotificationTemplateVersion,
  createAccountDeletion, listNotificationTemplates, loadAccountSettings, requestEmailVerification, saveProgramSettings,
} from './api'
import { CreatorProfileSection } from './CreatorProfileSection'

type Json = Record<string, any>
type AccountSection = 'overview' | 'profile' | 'email' | 'documents' | 'program' | 'notifications' | 'activity' | 'account'

const roleLabels: Record<string, string> = {
  blogger: 'Блогер', moderator: 'Модератор', manager: 'Менеджер', finance: 'Финансы',
  analyst: 'Аналитик', admin: 'Администратор',
}
const documentLabels: Record<string, string> = {
  program_terms: 'Условия программы', personal_data_consent: 'Согласие на обработку данных',
  privacy_policy: 'Политика конфиденциальности',
}

function SectionButton({ active, icon, label, note, onClick }: {
  active: boolean; icon: ReactNode; label: string; note?: string; onClick: () => void;
}) {
  return <button type="button" role="tab" aria-selected={active} className={active ? 'account-nav__item account-nav__item--active' : 'account-nav__item'} onClick={onClick}>
    {icon}<span><b>{label}</b>{note ? <small>{note}</small> : null}</span>
  </button>
}

function StatusLine({ complete, title, description }: { complete: boolean; title: string; description: string }) {
  return <div className={complete ? 'account-check account-check--complete' : 'account-check'}>
    {complete ? <CheckCircle2 size={18} /> : <AlertTriangle size={18} />}
    <span><b>{title}</b><small>{description}</small></span>
  </div>
}

export function SettingsDrawer({ user, close, changed, signedOut }: {
  user: CurrentUser; close: () => void; changed: (message: string) => void; signedOut: () => void;
}) {
  const [data, setData] = useState<Json | null>(null)
  const [section, setSection] = useState<AccountSection>('overview')
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState('')
  const [error, setError] = useState('')
  const [selectedTemplate, setSelectedTemplate] = useState('')
  const keys = useRef<Record<string, string>>({})
  const keyFor = (name: string) => keys.current[name] ??= crypto.randomUUID()

  const reload = async (silent = false) => {
    if (!silent) setLoading(true)
    setError('')
    try {
      const account = await loadAccountSettings(user.role)
      let templates: Json | null = null
      if (user.role === 'admin') {
        try { templates = await listNotificationTemplates() }
        catch (caught) {
          templates = { items: [] }
          setError(caught instanceof ApiError ? caught.message : 'Не удалось загрузить шаблоны уведомлений')
        }
      }
      setData({ ...account, notificationTemplates: templates })
      if (templates?.items?.length) setSelectedTemplate((current) => current || templates.items[0].id)
    }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось загрузить личный кабинет') }
    finally { if (!silent) setLoading(false) }
  }
  useEffect(() => { void reload() }, [user.role])

  const verify = async () => {
    setPending('verify'); setError('')
    try { await requestEmailVerification(); changed('Письмо для подтверждения отправлено') }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось отправить письмо') }
    finally { setPending('') }
  }
  const accept = async (id: string) => {
    setPending(id); setError('')
    try { await acceptLegalDocument(id); changed('Документ принят'); await reload(true) }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось принять документ') }
    finally { setPending('') }
  }
  const requestDeletion = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setPending('delete'); setError('')
    const password = String(new FormData(event.currentTarget).get('password') ?? '')
    try {
      await createAccountDeletion(password, keyFor('delete'))
      changed('Подтверждение удаления отправлено на почту')
      await reload(true)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Не удалось запросить удаление')
    } finally { setPending('') }
  }
  const saveNotificationTemplate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setPending('template'); setError('')
    const form = new FormData(event.currentTarget)
    try {
      const created = await createNotificationTemplateVersion(String(form.get('code')), String(form.get('channel')), {
        title_template: String(form.get('title') || '') || null,
        subject_template: String(form.get('subject') || '') || null,
        body_template: String(form.get('body')),
        allowed_variables: String(form.get('variables') || '').split(',').map((item) => item.trim()).filter(Boolean),
      })
      setSelectedTemplate(String(created.id))
      changed('Новая версия шаблона опубликована')
      await reload(true)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Не удалось сохранить шаблон')
    } finally { setPending('') }
  }
  const saveProgram = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setPending('program'); setError('')
    const form = new FormData(event.currentTarget)
    const optional = (name: string) => String(form.get(name) ?? '').trim() || null
    try {
      const settings = await saveProgramSettings({
        program_name: String(form.get('program_name') ?? '').trim(), main_text: optional('main_text'),
        primary_logo_url: optional('primary_logo_url'), secondary_logo_url: optional('secondary_logo_url'), key_image_url: optional('key_image_url'),
        manager_name: optional('manager_name'), manager_email: optional('manager_email'), manager_phone: optional('manager_phone'), manager_telegram_url: optional('manager_telegram_url'),
        program_details: optional('program_details'), service_signature: optional('service_signature'),
        suspicious_growth_threshold: Number(form.get('suspicious_growth_threshold')), random_review_percent: Number(form.get('random_review_percent')),
        rejection_reasons: String(form.get('rejection_reasons') ?? '').split('\n').map((item) => item.trim()).filter(Boolean),
      })
      setData((current: Json | null) => current ? { ...current, programSettings: settings } : current)
      changed('Настройки программы сохранены')
    } catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось сохранить настройки программы') }
    finally { setPending('') }
  }
  const activeDeletion = data?.deletions?.items?.find((item: Json) => item.status === 'awaiting_confirmation')
  const cancelDeletionRequest = async () => {
    if (!activeDeletion) return
    setPending('cancel-delete'); setError('')
    try {
      await cancelAccountDeletion(activeDeletion.id, keyFor('cancel:' + activeDeletion.id))
      changed('Удаление аккаунта отменено')
      await reload(true)
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Не удалось отменить удаление')
    } finally { setPending('') }
  }

  const requiredDocuments: Json[] = data?.legalStatus?.required_acceptances ?? []
  const acceptedDocuments: Json[] = data?.acceptances?.items ?? []
  const emailComplete = Boolean(user.email_verified_at)
  const documentsComplete = requiredDocuments.length === 0
  const completedSteps = Number(emailComplete) + Number(documentsComplete)
  const sections: Array<{ id: AccountSection; label: string; note?: string; icon: ReactNode }> = [
    { id: 'overview', label: 'Обзор', note: `${completedSteps} из 2 шагов`, icon: <LayoutDashboard size={17} /> },
    ...(user.role === 'blogger' ? [{ id: 'profile' as const, label: 'Профиль и площадки', note: 'Личные данные', icon: <UserRound size={17} /> }] : []),
    { id: 'email', label: 'Почта', note: emailComplete ? 'Подтверждена' : 'Требует действия', icon: <MailCheck size={17} /> },
    { id: 'documents', label: 'Документы', note: documentsComplete ? 'Актуальны' : `Нужно принять: ${requiredDocuments.length}`, icon: <FileCheck2 size={17} /> },
    ...(user.role === 'admin' ? [{ id: 'program' as const, label: 'Программа', note: 'Контакты и правила', icon: <Building2 size={17} /> }, { id: 'notifications' as const, label: 'Уведомления', note: 'Шаблоны писем', icon: <Bell size={17} /> }] : []),
    ...(data?.lifecycle ? [{ id: 'activity' as const, label: 'Активность', note: 'Статус участия', icon: <Activity size={17} /> }] : []),
    { id: 'account', label: 'Аккаунт', note: 'Доступ и удаление', icon: <ShieldAlert size={17} /> },
  ]

  return <div className="drawer-layer" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) close() }}>
    <aside className="work-drawer work-drawer--account" role="dialog" aria-modal="true" aria-labelledby="settings-title">
      <header className="work-drawer__head">
        <div><h2 id="settings-title">Личный кабинет</h2><p>Профиль, подтверждения и управление аккаунтом</p></div>
        <button className="icon-button" onClick={close} title="Закрыть"><ArrowLeft size={19} /></button>
      </header>
      {loading ? <div className="drawer-loading"><LoaderCircle className="spin" size={20} />Загружаем кабинет</div> : <div className="account-layout">
        <nav className="account-nav" role="tablist" aria-label="Разделы личного кабинета">
          <div className="account-identity"><span><UserRound size={20} /></span><div><strong>{user.email}</strong><small>{roleLabels[user.role]}</small></div></div>
          <div className="account-nav__items">{sections.map((item) => <SectionButton key={item.id} active={section === item.id} icon={item.icon} label={item.label} note={item.note} onClick={() => setSection(item.id)} />)}</div>
          <button type="button" className="account-signout" onClick={signedOut}><LogOut size={16} />Выйти из аккаунта</button>
        </nav>

        <div className="account-content" role="tabpanel" key={section}>
          {section === 'overview' ? <>
            <div className="account-section-head"><span>Личный кабинет</span><h3>Состояние аккаунта</h3><p>Здесь собраны действия, необходимые для полного доступа к системе.</p></div>
            <div className="account-progress" aria-label={`Выполнено ${completedSteps} из 2 обязательных шагов`}><span style={{ width: `${completedSteps * 50}%` }} /></div>
            <div className="account-checks">
              <button type="button" onClick={() => setSection('email')}><StatusLine complete={emailComplete} title="Электронная почта" description={emailComplete ? 'Адрес подтверждён' : 'Подтвердите адрес почты'} /></button>
              <button type="button" onClick={() => setSection('documents')}><StatusLine complete={documentsComplete} title="Документы" description={documentsComplete ? 'Все обязательные документы приняты' : `Осталось принять: ${requiredDocuments.length}`} /></button>
            </div>
            <dl className="account-facts"><div><dt>Роль</dt><dd>{roleLabels[user.role]}</dd></div><div><dt>Статус</dt><dd>{user.status === 'active' ? 'Активен' : user.status}</dd></div><div><dt>Принято документов</dt><dd>{acceptedDocuments.length}</dd></div></dl>
            {data?.programSettings?.manager_telegram_url ? <a className="button button--secondary manager-link" href={data.programSettings.manager_telegram_url} target="_blank" rel="noreferrer"><ExternalLink size={16} />Написать менеджеру{data.programSettings.manager_name ? ` · ${data.programSettings.manager_name}` : ''}</a> : null}
          </> : null}

          {section === 'profile' && user.role === 'blogger' ? <CreatorProfileSection changed={changed} /> : null}

          {section === 'email' ? <>
            <div className="account-section-head"><span>Подтверждение</span><h3>Электронная почта</h3><p>На этот адрес приходят системные уведомления и ссылки для чувствительных действий.</p></div>
            <div className="account-email"><MailCheck size={22} /><div><small>Текущий адрес</small><strong>{user.email}</strong></div><b className={emailComplete ? 'status-pill status-pill--ok' : 'status-pill status-pill--warning'}>{emailComplete ? 'Подтверждена' : 'Не подтверждена'}</b></div>
            {emailComplete ? <StatusLine complete title="Адрес подтверждён" description={`Подтверждение выполнено ${new Date(user.email_verified_at!).toLocaleDateString('ru-RU')}`} /> : <div className="account-callout"><div><b>Подтвердите адрес</b><p>Мы отправим одноразовую ссылку. Повторный запрос не изменяет адрес и не завершает текущую сессию.</p></div><button className="button button--primary" onClick={verify} disabled={pending === 'verify'}>{pending === 'verify' ? <LoaderCircle className="spin" size={16} /> : <MailCheck size={16} />}Отправить письмо</button></div>}
          </> : null}

          {section === 'documents' ? <>
            <div className="account-section-head"><span>Юридический статус</span><h3>Документы</h3><p>Актуальные согласия и условия участия хранятся вместе с датой принятия.</p></div>
            {requiredDocuments.length ? <div className="legal-required"><h4>Требуют принятия</h4>{requiredDocuments.map((item: Json) => <div key={item.document_id}><span><b>{documentLabels[item.document_type] ?? item.document_type}</b><small>Версия {item.version}</small></span><button className="button button--secondary" onClick={() => void accept(item.document_id)} disabled={pending === item.document_id}>{pending === item.document_id ? <LoaderCircle className="spin" size={15} /> : null}Принять</button></div>)}</div> : <StatusLine complete title="Все документы приняты" description="Юридических ограничений для работы с системой нет." />}
            {acceptedDocuments.length ? <div className="accepted-documents"><h4>История принятия</h4>{acceptedDocuments.map((item: Json, index: number) => <div key={item.id ?? index}><FileCheck2 size={16} /><span><b>{documentLabels[item.document_type] ?? item.title ?? 'Документ'}</b><small>{item.accepted_at ? new Date(item.accepted_at).toLocaleString('ru-RU') : `Версия ${item.version ?? 'актуальная'}`}</small></span></div>)}</div> : null}
          </> : null}

          {section === 'program' ? <>
            <div className="account-section-head"><span>Управление программой</span><h3>Основные настройки</h3><p>Контакты, оформление и операционные пороги применяются без изменения кода.</p></div>
            <form className="notification-template-form" onSubmit={saveProgram} key={data?.programSettings?.updated_at ?? 'program'}>
              <label className="form-field"><span>Название программы</span><input name="program_name" defaultValue={data?.programSettings?.program_name ?? 'AMP Content Factory'} required maxLength={200} /></label>
              <label className="form-field"><span>Основной текст</span><textarea name="main_text" defaultValue={data?.programSettings?.main_text ?? ''} rows={5} /></label>
              <div className="form-grid"><label className="form-field"><span>Имя менеджера</span><input name="manager_name" defaultValue={data?.programSettings?.manager_name ?? ''} /></label><label className="form-field"><span>Email менеджера</span><input name="manager_email" type="email" defaultValue={data?.programSettings?.manager_email ?? ''} /></label><label className="form-field"><span>Телефон менеджера</span><input name="manager_phone" defaultValue={data?.programSettings?.manager_phone ?? ''} /></label><label className="form-field"><span>Telegram-ссылка</span><input name="manager_telegram_url" type="url" defaultValue={data?.programSettings?.manager_telegram_url ?? ''} /></label></div>
              <div className="form-grid"><label className="form-field"><span>Основной логотип, URL</span><input name="primary_logo_url" type="url" defaultValue={data?.programSettings?.primary_logo_url ?? ''} /></label><label className="form-field"><span>Дополнительный логотип, URL</span><input name="secondary_logo_url" type="url" defaultValue={data?.programSettings?.secondary_logo_url ?? ''} /></label><label className="form-field form-field--wide"><span>Ключевое изображение, URL</span><input name="key_image_url" type="url" defaultValue={data?.programSettings?.key_image_url ?? ''} /></label></div>
              <label className="form-field"><span>Реквизиты программы</span><textarea name="program_details" defaultValue={data?.programSettings?.program_details ?? ''} rows={4} /></label>
              <label className="form-field"><span>Служебная подпись</span><textarea name="service_signature" defaultValue={data?.programSettings?.service_signature ?? ''} rows={3} /></label>
              <div className="form-grid"><label className="form-field"><span>Порог подозрительного роста</span><input name="suspicious_growth_threshold" type="number" min="1" required defaultValue={data?.programSettings?.suspicious_growth_threshold ?? 500000} /></label><label className="form-field"><span>Случайная проверка, %</span><input name="random_review_percent" type="number" min="0" max="100" required defaultValue={data?.programSettings?.random_review_percent ?? 10} /></label></div>
              <label className="form-field"><span>Причины отклонения, по одной на строку</span><textarea name="rejection_reasons" rows={7} defaultValue={(data?.programSettings?.rejection_reasons ?? []).join('\n')} /></label>
              <button className="button button--primary" disabled={pending === 'program'}>{pending === 'program' ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}Сохранить настройки</button>
            </form>
          </> : null}

          {section === 'notifications' ? <>
            <div className="account-section-head"><span>Системные сообщения</span><h3>Шаблоны уведомлений</h3><p>Каждое сохранение создаёт новую неизменяемую версию выбранного шаблона.</p></div>
            <label className="form-field"><span>Шаблон</span><select value={selectedTemplate} onChange={(event) => setSelectedTemplate(event.target.value)}>{(data?.notificationTemplates?.items ?? []).map((item: Json) => <option key={item.id} value={item.id}>{item.code} · {item.channel} · v{item.version}</option>)}</select></label>
            {(() => {
              const template = (data?.notificationTemplates?.items ?? []).find((item: Json) => item.id === selectedTemplate)
              return template ? <form className="notification-template-form" key={template.id} onSubmit={saveNotificationTemplate}>
                <input type="hidden" name="code" value={template.code} /><input type="hidden" name="channel" value={template.channel} />
                {template.channel === 'in_app' ? <label className="form-field"><span>Заголовок</span><input name="title" defaultValue={template.title_template ?? ''} required /></label> : <label className="form-field"><span>Тема письма</span><input name="subject" defaultValue={template.subject_template ?? ''} required /></label>}
                <label className="form-field"><span>Текст</span><textarea name="body" defaultValue={template.body_template} rows={9} required /></label>
                <label className="form-field"><span>Переменные через запятую</span><input name="variables" defaultValue={(template.allowed_variables ?? []).join(', ')} /></label>
                <button className="button button--primary" disabled={pending === 'template'}>{pending === 'template' ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}Опубликовать новую версию</button>
              </form> : <div className="inline-note">Шаблоны пока не созданы.</div>
            })()}
          </> : null}
          {section === 'activity' && data?.lifecycle ? <>
            <div className="account-section-head"><span>Участие в программе</span><h3>Активность</h3><p>Система учитывает действия в кабинете и заранее показывает плановое изменение статуса.</p></div>
            <dl className="account-facts account-facts--stack"><div><dt>Последняя активность</dt><dd>{new Date(data.lifecycle.last_activity_at).toLocaleString('ru-RU')}</dd></div><div><dt>Тип активности</dt><dd>{data.lifecycle.last_activity_kind ?? 'Действие в кабинете'}</dd></div><div><dt>Следующее изменение</dt><dd>{data.lifecycle.next_transition_at ? new Date(data.lifecycle.next_transition_at).toLocaleString('ru-RU') : 'Не запланировано'}</dd></div></dl>
          </> : null}

          {section === 'account' ? <>
            <div className="account-section-head"><span>Безопасность</span><h3>Управление аккаунтом</h3><p>Текущая роль и состояние доступа назначаются системой и ответственными сотрудниками.</p></div>
            <dl className="account-facts"><div><dt>Роль</dt><dd>{roleLabels[user.role]}</dd></div><div><dt>Статус доступа</dt><dd>{user.status === 'active' ? 'Активен' : user.status}</dd></div></dl>
            {user.role === 'blogger' ? <section className="account-danger"><h4><ShieldAlert size={17} />Удаление аккаунта</h4>{activeDeletion ? <div className="settings-action"><span><b>Ожидается подтверждение</b><small>Ссылка действует до {new Date(activeDeletion.expires_at).toLocaleString('ru-RU')}.</small></span><button className="button button--secondary" onClick={cancelDeletionRequest} disabled={pending === 'cancel-delete'}>Отменить запрос</button></div> : <form onSubmit={requestDeletion}><p>После подтверждения по электронной почте данные будут обработаны согласно политике удаления.</p><label className="form-field"><span>Текущий пароль</span><input name="password" type="password" autoComplete="current-password" required /></label><button className="button button--secondary" disabled={pending === 'delete'}>{pending === 'delete' ? <LoaderCircle className="spin" size={16} /> : <ShieldAlert size={16} />}Запросить удаление</button></form>}</section> : null}
          </> : null}

          {error ? <div className="auth-error account-error"><AlertTriangle size={16} />{error}</div> : null}
        </div>
      </div>}
    </aside>
  </div>
}
