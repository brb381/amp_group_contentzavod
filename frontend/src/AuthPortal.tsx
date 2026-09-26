import { FormEvent, useEffect, useRef, useState } from 'react'
import {
  AlertTriangle, ArrowLeft, ArrowRight, CheckCircle2, Eye, EyeOff,
  LoaderCircle, LockKeyhole, LogIn, Mail, UserPlus,
} from 'lucide-react'
import {
  ApiError, CurrentUser, confirmAccountDeletion, getCurrentLegalDocuments, login,
  registerAccount, requestPasswordReset, resetPassword, verifyEmail,
} from './api'

type Json = Record<string, any>
type Mode = 'login' | 'register' | 'forgot' | 'reset' | 'verify' | 'delete'

function initialMode(): Mode {
  const query = new URLSearchParams(window.location.search)
  if (query.get('verifyToken')) return 'verify'
  if (query.get('resetToken')) return 'reset'
  if (query.get('deletionToken') && query.get('deletionRequestId')) return 'delete'
  return 'login'
}

export function AuthPortal({ signedIn, backendError, retryBackend }: { signedIn: (user: CurrentUser) => void; backendError: string; retryBackend: () => void }) {
  const [mode, setMode] = useState<Mode>(initialMode)
  const [documents, setDocuments] = useState<Json | null>(null)
  const [pending, setPending] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const deletionKey = useRef(crypto.randomUUID())

  useEffect(() => {
    if (mode !== 'register' || documents) return
    getCurrentLegalDocuments().then(setDocuments).catch((caught) => setError(caught instanceof ApiError ? caught.message : 'Не удалось загрузить документы'))
  }, [mode, documents])

  const switchMode = (next: Mode) => {
    const update = () => { setMode(next); setError(''); setSuccess(''); setShowPassword(false) }
    const start = (document as Document & { startViewTransition?: (callback: () => void) => unknown }).startViewTransition
    if (start && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) start.call(document, update)
    else update()
  }

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setPending(true); setError(''); setSuccess('')
    const form = new FormData(event.currentTarget)
    const value = (name: string) => String(form.get(name) ?? '').trim()
    const query = new URLSearchParams(window.location.search)
    try {
      if (mode === 'login') return signedIn(await login(value('email'), value('password')))
      if (mode === 'register') {
        if (value('password') !== value('password_repeat')) throw new Error('Пароли не совпадают')
        await registerAccount({
          email: value('email'), password: value('password'),
          program_terms_document_id: documents!.program_terms.id, program_terms_accepted: true,
          personal_data_consent_document_id: documents!.personal_data_consent.id, personal_data_consent_granted: true,
        })
        setSuccess('Аккаунт создан. Проверьте почту и подтвердите адрес.'); return
      }
      if (mode === 'forgot') { await requestPasswordReset(value('email')); setSuccess('Если аккаунт существует, письмо уже отправлено.'); return }
      if (mode === 'reset') {
        if (value('password') !== value('password_repeat')) throw new Error('Пароли не совпадают')
        await resetPassword(query.get('resetToken')!, value('password')); setSuccess('Пароль изменён. Теперь можно войти.'); return
      }
      if (mode === 'verify') { await verifyEmail(query.get('verifyToken')!); setSuccess('Почта подтверждена. Теперь можно войти.'); return }
      await confirmAccountDeletion(query.get('deletionRequestId')!, query.get('deletionToken')!, deletionKey.current)
      setSuccess('Удаление аккаунта подтверждено.')
    } catch (caught) { setError(caught instanceof ApiError || caught instanceof Error ? caught.message : 'Не удалось выполнить запрос') }
    finally { setPending(false) }
  }

  const title = mode === 'login' ? 'Добро пожаловать' : mode === 'register' ? 'Создайте аккаунт' : mode === 'forgot' ? 'Восстановление доступа' : mode === 'reset' ? 'Новый пароль' : mode === 'verify' ? 'Подтверждение почты' : 'Удаление аккаунта'
  const description = mode === 'login' ? 'Войдите, чтобы продолжить работу в кабинете.' : mode === 'register' ? 'Зарегистрируйтесь для участия в программе AMP Group.' : mode === 'forgot' ? 'Укажите почту, к которой привязан аккаунт.' : mode === 'reset' ? 'Введите новый пароль длиной от 12 символов.' : mode === 'verify' ? 'Подтвердите адрес электронной почты.' : 'Подтвердите окончательное удаление данных.'
  const hasPassword = ['login', 'register', 'reset'].includes(mode)
  const showAccountTabs = mode === 'login' || mode === 'register'

  return <>
    {backendError ? <div className="backend-banner"><AlertTriangle size={16} />{backendError}<button onClick={retryBackend}>Повторить</button></div> : null}
    <main className="auth-page">
      <section className="auth-panel">
        <header className="auth-panel__top">
          <div className="auth-brand"><div className="brand__mark"><span>A</span></div><div><strong>AMP</strong><span>Content Factory</span></div></div>
        </header>

        <div className="auth-panel__body" key={mode}>
          {showAccountTabs ? <nav className="auth-mode-switch" aria-label="Доступ к аккаунту">
            <button type="button" className={mode === 'login' ? 'is-active' : ''} onClick={() => switchMode('login')}>Вход</button>
            <button type="button" className={mode === 'register' ? 'is-active' : ''} onClick={() => switchMode('register')}>Регистрация</button>
          </nav> : <button type="button" className="auth-back" onClick={() => switchMode('login')}><ArrowLeft size={16} />Вернуться ко входу</button>}

          <div className="auth-copy"><span className="auth-kicker">Личный кабинет</span><h1>{title}</h1><p>{description}</p></div>

          <div className="auth-form-surface">
            <form className={`auth-form ${mode === 'register' ? 'auth-form--register' : ''}`} onSubmit={submit}>
              {['login', 'register', 'forgot'].includes(mode) ? <label className="auth-field">
                <span>Email</span>
                <span className="auth-input"><Mail size={18} /><input name="email" type="email" autoComplete="email" required placeholder="name@ampgroup.ru" /></span>
              </label> : null}

              {hasPassword ? <label className="auth-field">
                <span className="auth-field__head"><span>{mode === 'reset' ? 'Новый пароль' : 'Пароль'}</span>{mode === 'login' ? <button type="button" onClick={() => switchMode('forgot')}>Забыли пароль?</button> : null}</span>
                <span className="auth-input"><LockKeyhole size={18} /><input name="password" type={showPassword ? 'text' : 'password'} minLength={mode === 'login' ? 1 : 12} maxLength={128} autoComplete={mode === 'login' ? 'current-password' : 'new-password'} required /><button type="button" className="auth-password-toggle" onClick={() => setShowPassword((value) => !value)} aria-label={showPassword ? 'Скрыть пароль' : 'Показать пароль'} title={showPassword ? 'Скрыть пароль' : 'Показать пароль'}>{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}</button></span>
              </label> : null}

              {['register', 'reset'].includes(mode) ? <label className="auth-field">
                <span>Повторите пароль</span>
                <span className="auth-input"><LockKeyhole size={18} /><input name="password_repeat" type={showPassword ? 'text' : 'password'} minLength={12} maxLength={128} autoComplete="new-password" required /></span>
              </label> : null}

              {mode === 'register' ? documents ? <div className="auth-legal">
                <label><input type="checkbox" required /><span>Принимаю «{documents.program_terms.title}»</span></label>
                <label><input type="checkbox" required /><span>Даю «{documents.personal_data_consent.title}»</span></label>
                <details><summary>{documents.privacy_policy.title}</summary><p>{documents.privacy_policy.content_markdown}</p></details>
              </div> : <div className="drawer-loading"><LoaderCircle className="spin" size={17} />Загружаем документы</div> : null}

              {success ? <div className="auth-success"><CheckCircle2 size={18} />{success}</div> : null}
              {error ? <div className="auth-error"><AlertTriangle size={17} />{error}</div> : null}

              {!success ? <button className="button button--primary auth-submit" disabled={pending || (mode === 'register' && !documents)}>
                {pending ? <LoaderCircle className="spin" size={19} /> : mode === 'register' ? <UserPlus size={19} /> : mode === 'forgot' ? <Mail size={19} /> : mode === 'login' ? <LogIn size={19} /> : <LockKeyhole size={19} />}
                <span>{pending ? 'Отправляем' : mode === 'login' ? 'Войти в кабинет' : mode === 'register' ? 'Создать аккаунт' : mode === 'forgot' ? 'Отправить ссылку' : mode === 'reset' ? 'Изменить пароль' : mode === 'verify' ? 'Подтвердить почту' : 'Удалить аккаунт'}</span>
                {!pending && ['login', 'register'].includes(mode) ? <ArrowRight className="auth-submit__arrow" size={18} /> : null}
              </button> : null}
            </form>
          </div>
        </div>
      </section>
      <aside className="auth-visual"><div className="auth-visual__content"><span>Контент-завод</span><strong>От публикации<br />до выплаты</strong><p>Единое рабочее пространство AMP Group</p></div></aside>
    </main>
  </>
}
