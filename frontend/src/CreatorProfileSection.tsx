import { FormEvent, useEffect, useState } from 'react'
import { AlertTriangle, Camera, Link2, LoaderCircle, Pencil, Save, Send, Trash2, UserRound } from 'lucide-react'
import {
  ApiError, addSocialAccount, deleteProfileAvatar, deleteSocialAccount, loadCreatorWorkspace,
  saveCreatorProfile, submitCreatorProfile, updateSocialAccount, uploadProfileAvatar,
} from './api'
import { platformConfig, platformIds, platforms, type PlatformId } from './platforms'
import { statusLabel } from './labels'

type Json = Record<string, any>

export function CreatorProfileSection({ changed }: { changed: (message: string) => void }) {
  const [workspace, setWorkspace] = useState<Json | null>(null)
  const [loading, setLoading] = useState(true)
  const [pending, setPending] = useState('')
  const [error, setError] = useState('')
  const [editingAccount, setEditingAccount] = useState<Json | null>(null)
  const [accountPlatform, setAccountPlatform] = useState<PlatformId>('vk')

  const reload = async () => {
    setError('')
    try { setWorkspace(await loadCreatorWorkspace('profile')) }
    catch (caught) { setError(caught instanceof ApiError ? caught.message : 'Не удалось загрузить профиль') }
    finally { setLoading(false) }
  }
  useEffect(() => { void reload() }, [])

  if (loading) return <div className="drawer-loading"><LoaderCircle className="spin" size={20} />Загружаем профиль</div>
  if (!workspace) return <div className="request-state request-state--error" role="alert"><span>{error || 'Не удалось загрузить профиль'}</span><button onClick={() => { setLoading(true); void reload() }}>Повторить</button></div>

  const profile = workspace.profile ?? {}
  const accounts: Json[] = workspace?.socialAccounts ?? []
  const profileEditable = !profile.status || ['draft', 'submitted', 'in_review', 'rejected'].includes(profile.status)
  const profileSubmittable = !profile.status || ['draft', 'rejected'].includes(profile.status)

  const uploadAvatar = async (file: File | undefined) => {
    if (!file) return
    if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type)) {
      setError('Выберите изображение JPEG, PNG или WebP')
      return
    }
    if (file.size > 1024 * 1024) {
      setError('Размер аватара не должен превышать 1 МБ')
      return
    }
    setPending('avatar'); setError('')
    try {
      await uploadProfileAvatar(file)
      changed('Аватар обновлён')
      await reload()
    } catch (caught) {
      setError(caught instanceof ApiError || caught instanceof Error ? caught.message : 'Не удалось загрузить аватар')
    } finally { setPending('') }
  }

  const removeAvatar = async () => {
    setPending('avatar'); setError('')
    try {
      await deleteProfileAvatar()
      changed('Аватар удалён')
      await reload()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Не удалось удалить аватар')
    } finally { setPending('') }
  }
  const saveProfile = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const value = (name: string) => String(form.get(name) ?? '').trim()
    const action = ((event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null)?.value
    setPending('profile'); setError('')
    try {
      await saveCreatorProfile({
        full_name: value('full_name') || null,
        display_name: value('display_name') || null,
        phone: value('phone') || null,
        telegram: value('telegram') || null,
        city_country: value('city_country') || null,
        content_topics: value('content_topics') || null,
        recipient_status: value('recipient_status') || null,
      })
      if (action === 'submit') {
        await submitCreatorProfile()
        changed('Профиль отправлен на модерацию')
      } else changed('Профиль сохранён')
      await reload()
    } catch (caught) {
      setError(caught instanceof ApiError || caught instanceof Error ? caught.message : 'Не удалось сохранить профиль')
    } finally { setPending('') }
  }

  const saveSocialAccount = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const payload = {
      platform: String(editingAccount?.platform ?? form.get('platform') ?? ''),
      url: String(form.get('social_url') ?? '').trim(),
      follower_count: form.get('followers') ? Number(form.get('followers')) : null,
    }
    setPending('social'); setError('')
    try {
      if (editingAccount) await updateSocialAccount(editingAccount.id, payload)
      else await addSocialAccount(payload)
      changed(editingAccount ? 'Площадка обновлена' : 'Площадка добавлена')
      setEditingAccount(null)
      setAccountPlatform('vk')
      await reload()
    } catch (caught) {
      setError(caught instanceof ApiError || caught instanceof Error ? caught.message : 'Не удалось сохранить площадку')
    } finally { setPending('') }
  }

  const removeSocialAccount = async (account: Json) => {
    if (!window.confirm('Удалить социальный аккаунт?')) return
    setPending(`delete:${account.id}`); setError('')
    try {
      await deleteSocialAccount(account.id)
      if (editingAccount?.id === account.id) setEditingAccount(null)
      changed('Площадка удалена')
      await reload()
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Не удалось удалить площадку')
    } finally { setPending('') }
  }

  return <div className="creator-profile-settings">
    <div className="account-section-head"><span>Данные блогера</span><h3>Профиль и площадки</h3><p>Контактные данные и социальные аккаунты, с которых принимаются публикации.</p></div>
    {error ? <div className="auth-error" role="alert"><AlertTriangle size={16} />{error}</div> : null}
    <section className="creator-avatar-settings">
      <span className="creator-avatar-preview">{profile.avatar_url ? <img src={profile.avatar_url} alt="Аватар профиля" /> : <UserRound size={30} />}</span>
      <div><strong>Аватар профиля</strong><small>JPEG, PNG или WebP, не более 1 МБ</small></div>
      <div className="creator-avatar-actions"><label className="button button--secondary"><Camera size={16} />{profile.avatar_url ? 'Заменить' : 'Загрузить'}<input type="file" accept="image/jpeg,image/png,image/webp" disabled={!profile.id || pending === 'avatar'} onChange={(event) => { void uploadAvatar(event.target.files?.[0]); event.currentTarget.value = '' }} /></label>{profile.avatar_url ? <button type="button" className="icon-button" title="Удалить аватар" disabled={pending === 'avatar'} onClick={() => void removeAvatar()}><Trash2 size={16} /></button> : null}</div>
      {!profile.id ? <p>Сначала сохраните основные данные профиля.</p> : null}
    </section>

    <form className="profile-settings-block" onSubmit={saveProfile} key={profile.updated_at ?? 'profile'}>
      <header className="profile-settings-head"><div><h4>Основные данные</h4><p>Статус профиля: {statusLabel(profile.status, 'Черновик')}</p></div></header>
      {!profileEditable ? <div className="inline-note">Одобренный профиль доступен только для чтения. Площадки можно добавлять отдельно.</div> : null}
      <fieldset className="form-grid" disabled={!profileEditable || pending === 'profile'}>
        <label className="form-field"><span>ФИО</span><input name="full_name" defaultValue={profile.full_name ?? ''} required /></label>
        <label className="form-field"><span>Публичное имя</span><input name="display_name" defaultValue={profile.display_name ?? ''} required /></label>
        <label className="form-field"><span>Телефон</span><input name="phone" defaultValue={profile.phone ?? ''} /></label>
        <label className="form-field"><span>Telegram</span><input name="telegram" defaultValue={profile.telegram ?? ''} /></label>
        <label className="form-field"><span>Город и страна</span><input name="city_country" defaultValue={profile.city_country ?? ''} /></label>
        <label className="form-field"><span>Статус получателя</span><select name="recipient_status" defaultValue={profile.recipient_status ?? 'self_employed'}><option value="self_employed">Самозанятый</option><option value="individual">Физлицо</option></select></label>
        <label className="form-field form-field--wide"><span>Темы контента</span><textarea name="content_topics" defaultValue={profile.content_topics ?? ''} rows={3} /></label>
      </fieldset>
      {profile.moderation_reason ? <div className="moderation-reason moderation-reason--block"><AlertTriangle size={16} /><span><b>Причина решения</b>{profile.moderation_reason}</span></div> : null}
      {profileEditable ? <footer className="creator-profile-actions"><button className="button button--secondary" value="save" disabled={pending === 'profile'}>{pending === 'profile' ? <LoaderCircle className="spin" size={16} /> : <Save size={16} />}Сохранить</button>{profileSubmittable ? <button className="button button--primary" value="submit" disabled={pending === 'profile'}><Send size={16} />Отправить на модерацию</button> : null}</footer> : null}
    </form>

    <section className="profile-settings-block">
      <header className="profile-settings-head"><div><h4>Социальные площадки</h4><p>Каждый аккаунт проходит отдельную проверку.</p></div><Link2 size={18} /></header>
      <form className="creator-social-form" onSubmit={saveSocialAccount} key={editingAccount?.id ?? 'new'}>
        <div className="form-grid">
          <label className="form-field"><span>Платформа</span><select name="platform" value={editingAccount?.platform ?? accountPlatform} onChange={(event) => setAccountPlatform(event.target.value as PlatformId)} disabled={Boolean(editingAccount)}>{platformIds.map((id) => <option value={id} key={id}>{platforms[id].label}</option>)}</select></label>
          <label className="form-field"><span>Подписчики</span><input name="followers" type="number" min="0" placeholder="0" defaultValue={editingAccount?.follower_count ?? ''} /></label>
          <label className="form-field form-field--wide"><span>Ссылка</span><input name="social_url" type="url" required placeholder={platformConfig(editingAccount?.platform ?? accountPlatform).accountPlaceholder} defaultValue={editingAccount?.url ?? ''} /></label>
        </div>
        <div className="creator-profile-actions"><button className="button button--secondary" disabled={pending === 'social'}>{pending === 'social' ? <LoaderCircle className="spin" size={16} /> : editingAccount ? <Save size={16} /> : <Link2 size={16} />}{editingAccount ? 'Сохранить площадку' : 'Добавить площадку'}</button>{editingAccount ? <button type="button" className="text-button" onClick={() => { setEditingAccount(null); setAccountPlatform('vk') }}>Отменить</button> : null}</div>
      </form>
      {accounts.length ? <div className="creator-social-list">{accounts.map((item) => <article key={item.id}><span className="creator-social-mark">{platformConfig(item.platform).shortLabel}</span><div><strong>{platformConfig(item.platform).label}</strong><a href={item.url} target="_blank" rel="noreferrer">{item.url}</a></div><b className={`creator-social-status creator-social-status--${item.status}`}>{statusLabel(item.status)}</b><div className="creator-social-actions"><button type="button" onClick={() => { setEditingAccount(item); setAccountPlatform(item.platform as PlatformId) }} title="Изменить площадку"><Pencil size={15} /></button><button type="button" onClick={() => void removeSocialAccount(item)} disabled={pending === `delete:${item.id}`} title="Удалить площадку">{pending === `delete:${item.id}` ? <LoaderCircle className="spin" size={15} /> : <Trash2 size={15} />}</button></div></article>)}</div> : <div className="inline-note">Площадки пока не добавлены.</div>}
    </section>
  </div>
}
