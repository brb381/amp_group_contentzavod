import { useEffect, useState } from 'react'
import { Bell, CheckCheck, ChevronLeft, ChevronRight, LoaderCircle, X } from 'lucide-react'
import {
  ApiError, PageId, getUnreadNotificationCount, listNotifications,
  markAllNotificationsRead, markNotificationRead,
} from './api'

type Json = Record<string, any>

function targetPage(path?: string | null): PageId | null {
  if (!path) return null
  if (path.includes('support-ticket')) return 'support'
  if (path.includes('payout')) return 'finance'
  if (path.includes('view-reading')) return 'readings'
  if (path.includes('publication') || path.includes('video-card')) return 'publications'
  if (path.includes('profile') || path.includes('social-account')) return 'creators'
  if (path.includes('export')) return 'exports'
  return 'overview'
}

export function NotificationMenu({ navigate }: { navigate: (page: PageId) => void }) {
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<Json[]>([])
  const [unread, setUnread] = useState(0)
  const [unreadOnly, setUnreadOnly] = useState(false)
  const [page, setPage] = useState(1)
  const [totalPages, setTotalPages] = useState(1)
  const [totalItems, setTotalItems] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const loadCount = () => getUnreadNotificationCount()
    .then((value) => setUnread(Number(value.unread_count ?? 0))).catch(() => {})
  const load = async () => {
    setLoading(true); setError('')
    try {
      const value = await listNotifications(unreadOnly, page)
      setItems(value.items ?? [])
      setTotalPages(Number(value.total_pages ?? 1))
      setTotalItems(Number(value.total_items ?? 0))
    } catch (caught) {
      setError(caught instanceof ApiError ? caught.message : 'Не удалось получить уведомления')
    } finally { setLoading(false) }
  }

  useEffect(() => { void loadCount() }, [])
  useEffect(() => { if (open) void load() }, [open, page, unreadOnly])

  const select = async (item: Json) => {
    if (!item.read_at) {
      try {
        await markNotificationRead(item.id)
        setUnread((value) => Math.max(0, value - 1))
        setItems((value) => value.map((entry) => entry.id === item.id ? { ...entry, read_at: new Date().toISOString() } : entry))
      } catch { /* Переход к связанному разделу всё равно полезен. */ }
    }
    const pageId = targetPage(item.action_path)
    if (pageId) navigate(pageId)
    setOpen(false)
  }

  const readAll = async () => {
    try {
      await markAllNotificationsRead()
      setUnread(0)
      setItems((value) => value.map((item) => ({ ...item, read_at: item.read_at ?? new Date().toISOString() })))
    } catch { /* Архив остается доступным. */ }
  }

  return <div className="notification-menu">
    <button className="icon-button notification-button" onClick={() => setOpen((value) => !value)} title="Уведомления" aria-expanded={open}>
      <Bell size={19} />{unread ? <span /> : null}
    </button>
    {open ? <>
      <button className="notification-backdrop" onClick={() => setOpen(false)} aria-label="Закрыть уведомления" />
      <section className="notification-popover" aria-label="Архив уведомлений">
        <header><div><strong>Уведомления</strong><small>{unread ? unread + ' непрочитанных' : 'Новых уведомлений нет'}</small></div><button className="icon-button" onClick={() => setOpen(false)} title="Закрыть"><X size={17} /></button></header>
        <div className="notification-tools">
          <label><input type="checkbox" checked={unreadOnly} onChange={(event) => { setUnreadOnly(event.target.checked); setPage(1) }} />Только непрочитанные</label>
          {unread ? <button className="notification-read-all" onClick={readAll}><CheckCheck size={15} />Прочитать все</button> : null}
        </div>
        {loading ? <div className="notification-state"><LoaderCircle className="spin" size={18} />Загружаем</div>
          : error ? <div className="notification-state notification-state--error">{error}</div>
          : <div className="notification-list">{items.map((item) => <button key={item.id} className={item.read_at ? '' : 'unread'} onClick={() => void select(item)}><i /><span><strong>{item.title}</strong><small>{item.body}</small><time>{new Date(item.created_at).toLocaleString('ru-RU', { timeZone: 'Europe/Moscow' })}</time></span></button>)}{!items.length ? <div className="notification-state">Уведомлений пока нет</div> : null}</div>}
        <footer className="notification-pagination">
          <span>{totalItems} уведомлений · {page}/{totalPages}</span>
          <div><button disabled={page <= 1} onClick={() => setPage((value) => value - 1)} title="Предыдущая страница"><ChevronLeft size={16} /></button><button disabled={page >= totalPages} onClick={() => setPage((value) => value + 1)} title="Следующая страница"><ChevronRight size={16} /></button></div>
        </footer>
      </section>
    </> : null}
  </div>
}