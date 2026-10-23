import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Bell } from 'lucide-react'
import { AgentRequestsInbox, useAgentRequests } from '@/components/dashboard/AgentRequests'

interface Message {
  id: number
  time: string
  kind: string
  level: string
  platform: string
  message: string
  data?: Record<string, unknown>
}

const TABS = [
  { key: 'all', label: '全部', kinds: null },
  { key: 'alert', label: '异常', kinds: ['incident', 'platform'] },
  { key: 'task', label: '任务', kinds: ['task', 'loop', 'keyword'] },
  { key: 'agent', label: '智能体', kinds: ['agent', 'request', 'access'] },
  { key: 'reply', label: 'HR 回复', kinds: ['reply'] },
] as const

const TARGETS: Record<string, string> = { incident: '/', platform: '/', task: '/?activity=queue', loop: '/', keyword: '/config?section=search', reply: '/monitor', agent: '/config?section=agent', request: '/config?section=agent', access: '/config?section=agent' }
const KIND_LABELS: Record<string, string> = { incident: '异常', platform: '平台', task: '任务', loop: '每日循环', keyword: '关键词', agent: '智能体', request: '请求', access: '权限', reply: 'HR 回复' }
const POLL_MS = 15000

function stamp(iso: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2})/.exec(iso)
  if (!match) return iso
  const today = new Date().toISOString().slice(0, 10)
  return `${iso.slice(0, 10) === today ? '' : `${match[2]}-${match[3]} `}${match[4]}`
}

/** The bell in the top bar: what happened (tasks, paused platforms, incidents, HR replies, agent actions) and what waits for you. */
export function MessageCenter() {
  const [open, setOpen] = useState(false)
  const [tab, setTab] = useState<(typeof TABS)[number]['key']>('all')
  const [messages, setMessages] = useState<Message[]>([])
  const [unread, setUnread] = useState(0)
  const rootRef = useRef<HTMLDivElement>(null)
  const requests = useAgentRequests()
  const navigate = useNavigate()

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/messages?limit=100')
      if (!res.ok) return
      const data = await res.json()
      setMessages(data.messages || [])
      setUnread(Number(data.unread) || 0)
    } catch {
      // the next poll tries again
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [load])

  useEffect(() => {
    if (!open) return
    const close = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', escape)
    return () => {
      document.removeEventListener('mousedown', close)
      document.removeEventListener('keydown', escape)
    }
  }, [open])

  const markAllRead = async () => {
    await fetch('/api/messages/read', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }).catch(() => undefined)
    await load()
  }

  const kinds = TABS.find(item => item.key === tab)?.kinds
  const shown = messages.filter(item => !kinds || (kinds as readonly string[]).includes(item.kind))
  const waiting = requests.pending.length
  const badge = unread + waiting

  return (
    <div ref={rootRef} className="relative">
      <button type="button" aria-label={badge ? `消息中心，${badge} 条未读` : '消息中心'} aria-expanded={open} onClick={() => setOpen(value => !value)}
        className="relative rounded-full border border-card-border bg-card p-2 text-muted hover:text-primary">
        <Bell className="h-4 w-4" />
        {badge > 0 && <span className="absolute -right-1 -top-1 min-w-4 rounded-full bg-danger px-1 text-center text-[10px] font-black leading-4 text-white">{badge > 99 ? '99+' : badge}</span>}
      </button>
      {open && (
        <div role="dialog" aria-label="消息中心" className="absolute right-0 top-full z-40 mt-2 w-[min(26rem,92vw)] rounded-2xl border border-card-border bg-card shadow-xl">
          <div className="flex items-center justify-between border-b border-card-border px-4 py-3">
            <span className="text-sm font-black text-foreground">消息中心</span>
            <button type="button" disabled={!unread} onClick={() => void markAllRead()} className="text-xs font-bold text-primary hover:underline disabled:cursor-not-allowed disabled:text-muted disabled:no-underline">全部标为已读</button>
          </div>
          <div className="flex gap-1 overflow-x-auto px-3 pt-2">
            {TABS.map(item => (
              <button key={item.key} type="button" onClick={() => setTab(item.key)}
                className={`whitespace-nowrap rounded-full px-3 py-1 text-xs font-black ${tab === item.key ? 'bg-primary text-primary-foreground' : 'text-muted hover:text-primary'}`}>
                {item.label}
              </button>
            ))}
          </div>
          <div className="max-h-[60vh] overflow-y-auto px-3 py-2">
            {waiting > 0 && (tab === 'all' || tab === 'agent') && (
              <div className="mb-2 rounded-xl border border-warning-border bg-warning-soft p-2">
                <div className="mb-1 text-xs font-black text-warning-strong">等你确认 {waiting} 条</div>
                <AgentRequestsInbox {...requests} />
              </div>
            )}
            {shown.length === 0 ? (
              <p className="py-6 text-center text-xs text-muted">这里还没有消息。</p>
            ) : (
              <ul className="space-y-1">
                {shown.map(item => (
                  <li key={item.id}>
                    <button type="button" onClick={() => { setOpen(false); navigate(TARGETS[item.kind] || '/') }}
                      className="flex w-full items-start gap-2 rounded-lg px-2 py-2 text-left hover:bg-secondary">
                      <span className={`mt-1.5 h-2 w-2 shrink-0 rounded-full ${item.level === 'warning' ? 'bg-danger' : 'bg-primary/50'}`} />
                      <span className="min-w-0 flex-1">
                        <span className="block break-words text-xs leading-5 text-foreground">{item.message}</span>
                        <span className="mt-0.5 block text-[10px] text-muted">{stamp(item.time)} · {KIND_LABELS[item.kind] || item.kind}</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
