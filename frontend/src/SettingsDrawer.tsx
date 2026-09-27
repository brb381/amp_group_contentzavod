import { FormEvent, ReactNode, useEffect, useRef, useState } from 'react'
import {
  Activity, AlertTriangle, Bell, BookOpenText, CheckCircle2, FileCheck2, LayoutDashboard,
  LoaderCircle, LogOut, MailCheck, Save, ShieldAlert, UserRound, ArrowLeft, Building2, ExternalLink,
} from 'lucide-react'
import {
  ApiError, CurrentUser, acceptLegalDocument, cancelAccountDeletion, createNotificationTemplateVersion,
  createAccountDeletion, listNotificationTemplates, loadAccountSettings, requestEmailVerification, saveProgramSettings,
} from './api'
import { CreatorProfileSection } from './CreatorProfileSection'
import { documentLabel, lifecycleActivityLabel, roleLabels, statusLabel } from './labels'

type Json = Record<string, any>
type AccountSection = 'overview' | 'profile' | 'email' | 'documents' | 'program' | 'notifications' | 'activity' | 'account'
type LegalDocumentType = 'program_terms' | 'personal_data_consent' | 'privacy_policy'


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

function LegalDocumentText({ content }: { content: string }) {
  return <div className="legal-reader__content">
    {content.split(/\r?\n/).map((line, index) => {
      const text = line.trim()
      if (!text) return <span className="legal-reader__space" key={index} aria-hidden="true" />
      if (text.startsWith('### ')) return <h5 key={index}>{text.slice(4)}</h5>
      if (text.startsWith('## ')) return <h4 key={index}>{text.slice(3)}</h4>
      if (text.startsWith('# ')) return <h3 key={index}>{text.slice(2)}</h3>
      if (/^[-*]\s/.test(text)) return <p className="legal-reader__list-item" key={index}>{text.slice(2)}</p>
      if (/^\d+[.)]\s/.test(text)) return <p className="legal-reader__list-item legal-reader__list-item--numbered" key={index}>{text}</p>
      return <p key={index}>{text}</p>
    })}
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
  const [selectedDocumentType, setSelectedDocumentType] = useState<LegalDocumentType>('program_terms')
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
    const template = (data?.notificationTemplates?.items ?? []).find((item: Json) => item.id === selectedTemplate)
    if (!template) return setPending('')
    try {
      const created = await createNotificationTemplateVersion(String(form.get('code')), String(form.get('channel')), {
        title_template: String(form.get('title') || '') || null,
        subject_template: String(form.get('subject') || '') || null,
        body_template: String(form.get('body')),
        allowed_variables: template.allowed_variables ?? [],
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
  const legalDocuments: Json[] = Object.values(data?.legalDocuments ?? {})
  const selectedDocument = legalDocuments.find((item: Json) => item.document_type === selectedDocumentType) ?? legalDocuments[0]
  const selectedDocumentRequirement = requiredDocuments.find((item: Json) => item.document_id === selectedDocument?.id)
  const selectedDocumentAcceptance = acceptedDocuments.find((item: Json) => item.document_id === selectedDocument?.id)
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
            <dl className="account-facts"><div><dt>Роль</dt><dd>{roleLabels[user.role]}</dd></div><div><dt>Статус</dt><dd>{statusLabel(user.status)}</dd></div><div><dt>Принято документов</dt><dd>{acceptedDocuments.length}</dd></div></dl>
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
            <div className="legal-library">
              <div className="legal-library__list" role="tablist" aria-label="Актуальные документы">
                {legalDocuments.map((item: Json) => {
                  const accepted = acceptedDocuments.some((entry: Json) => entry.document_id === item.id)
                  const required = requiredDocuments.some((entry: Json) => entry.document_id === item.id)
                  return <button key={item.id} type="button" role="tab" aria-selected={selectedDocument?.id === item.id} className={selectedDocument?.id === item.id ? 'legal-library__item legal-library__item--active' : 'legal-library__item'} onClick={() => setSelectedDocumentType(item.document_type)}>
                    <BookOpenText size={18} />
                    <span><b>{item.title || documentLabel(item.document_type)}</b><small>Версия {item.version}</small></span>
                    <em className={required ? 'legal-library__state legal-library__state--required' : 'legal-library__state'}>{required ? 'Нужно принять' : accepted ? 'Принят' : 'Для ознакомления'}</em>
                  </button>
                })}
              </div>
              {selectedDocument ? <article className="legal-reader" aria-labelledby="legal-document-title">
                <header className="legal-reader__head">
                  <div><small>{documentLabel(selectedDocument.document_type)} · версия {selectedDocument.version}</small><h4 id="legal-document-title">{selectedDocument.title}</h4><p>Опубликован {new Date(selectedDocument.published_at).toLocaleDateString('ru-RU')}</p></div>
                  {selectedDocumentRequirement ? <button className="button button--primary" onClick={() => void accept(selectedDocument.id)} disabled={pending === selectedDocument.id}>{pending === selectedDocument.id ? <LoaderCircle className="spin" size={15} /> : <FileCheck2 size={15} />}Принять документ</button> : selectedDocumentAcceptance ? <span className="legal-reader__accepted"><CheckCircle2 size={15} />Принят {new Date(selectedDocumentAcceptance.accepted_at).toLocaleDateString('ru-RU')}</span> : null}
                </header>
                <LegalDocumentText content={selectedDocument.content_markdown} />
              </article> : <div className="request-state request-state--empty">Актуальные документы пока не опубликованы.</div>}
            </div>
            {acceptedDocuments.length ? <details className="accepted-documents accepted-documents--history"><summary>История принятия</summary>{acceptedDocuments.map((item: Json, index: number) => <div key={item.id ?? index}><FileCheck2 size={16} /><span><b>{item.document_title || documentLabel(item.document_type)}</b><small>{item.accepted_at ? `${new Date(item.accepted_at).toLocaleString('ru-RU')} · версия ${item.document_version}` : `Версия ${item.document_version ?? 'актуальная'}`}</small></span></div>)}</details> : null}
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
            <div className="account-section-head"><span>Системные сообщения</span><h3>Шаблоны уведомлений</h3><p>Получателя выбирает само событие: сообщение уходит только связанному пользователю. Здесь меняется только текст.</p></div>
            <label className="form-field"><span>Шаблон</span><select value={selectedTemplate} onChange={(event) => setSelectedTemplate(event.target.value)}>{(data?.notificationTemplates?.items ?? []).map((item: Json) => <option key={item.id} value={item.id}>{item.title_template || item.subject_template || 'Шаблон уведомления'} · {item.channel === 'email' ? 'Электронная почта' : item.channel === 'in_app' ? 'В приложении' : 'Канал уведомления'}</option>)}</select></label>
            {(() => {
              const template = (data?.notificationTemplates?.items ?? []).find((item: Json) => item.id === selectedTemplate)
              return template ? <form className="notification-template-form" key={template.id} onSubmit={saveNotificationTemplate}>
                <input type="hidden" name="code" value={template.code} /><input type="hidden" name="channel" value={template.channel} />
                {template.channel === 'in_app' ? <label className="form-field"><span>Заголовок</span><input name="title" defaultValue={template.title_template ?? ''} required /></label> : <label className="form-field"><span>Тема письма</span><input name="subject" defaultValue={template.subject_template ?? ''} required /></label>}
                <label className="form-field"><span>Текст</span><textarea name="body" defaultValue={template.body_template} rows={9} required /></label>
                <div className="inline-note">Поля со знаком $ заполняются системой автоматически. Их названия в тексте изменять не нужно.</div>
                <button className="button button--primary" disabled={pending === 'template'}>{pending === 'template' ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}Опубликовать новую версию</button>
              </form> : <div className="inline-note">Шаблоны пока не созданы.</div>
            })()}
          </> : null}
          {section === 'activity' && data?.lifecycle ? <>
            <div className="account-section-head"><span>Участие в программе</span><h3>Активность</h3><p>Система учитывает действия в кабинете и заранее показывает плановое изменение статуса.</p></div>
            <dl className="account-facts account-facts--stack"><div><dt>Последняя активность</dt><dd>{new Date(data.lifecycle.last_activity_at).toLocaleString('ru-RU')}</dd></div><div><dt>Тип активности</dt><dd>{lifecycleActivityLabel(data.lifecycle.last_activity_kind)}</dd></div><div><dt>Следующее изменение</dt><dd>{data.lifecycle.next_transition_at ? new Date(data.lifecycle.next_transition_at).toLocaleString('ru-RU') : 'Не запланировано'}</dd></div></dl>
          </> : null}

          {section === 'account' ? <>
            <div className="account-section-head"><span>Безопасность</span><h3>Управление аккаунтом</h3><p>Текущая роль и состояние доступа назначаются системой и ответственными сотрудниками.</p></div>
            <dl className="account-facts"><div><dt>Роль</dt><dd>{roleLabels[user.role]}</dd></div><div><dt>Статус доступа</dt><dd>{statusLabel(user.status)}</dd></div></dl>
            {user.role === 'blogger' ? <section className="account-danger"><h4><ShieldAlert size={17} />Удаление аккаунта</h4>{activeDeletion ? <div className="settings-action"><span><b>Ожидается подтверждение</b><small>Ссылка действует до {new Date(activeDeletion.expires_at).toLocaleString('ru-RU')}.</small></span><button className="button button--secondary" onClick={cancelDeletionRequest} disabled={pending === 'cancel-delete'}>Отменить запрос</button></div> : <form onSubmit={requestDeletion}><p>После подтверждения по электронной почте данные будут обработаны согласно политике удаления.</p><label className="form-field"><span>Текущий пароль</span><input name="password" type="password" autoComplete="current-password" required /></label><button className="button button--secondary" disabled={pending === 'delete'}>{pending === 'delete' ? <LoaderCircle className="spin" size={16} /> : <ShieldAlert size={16} />}Запросить удаление</button></form>}</section> : null}
          </> : null}

          {error ? <div className="auth-error account-error"><AlertTriangle size={16} />{error}</div> : null}
        </div>
      </div>}
    </aside>
  </div>
}
