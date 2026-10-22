import { useCallback, useEffect, useState } from 'react'

export interface AgentRequestItem {
  id: string
  time: string
  label: string
  status: 'pending' | 'approved' | 'denied'
  source: string
}

/** Requests Claude or Codex made that wait for the user's yes (the "ask" permission mode). */
export function useAgentRequests() {
  const [pending, setPending] = useState<AgentRequestItem[]>([])
  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/agent-access')
      if (!res.ok) return
      const data = await res.json()
      setPending((data.requests || []).filter((item: AgentRequestItem) => item.status === 'pending'))
    } catch {
      // the next poll tries again
    }
  }, [])
  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 15000)
    return () => window.clearInterval(timer)
  }, [load])
  const decide = async (id: string, decision: 'approve' | 'deny') => {
    await fetch(`/api/agent-access/requests/${id}/${decision}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
    await load()
  }
  return { pending, decide }
}

export function AgentRequestsInbox({ pending, decide }: ReturnType<typeof useAgentRequests>) {
  if (!pending.length) return <p className="py-2 text-xs text-muted">没有等你确认的智能体请求。</p>
  return (
    <div className="space-y-2">
      {pending.map(item => (
        <div key={item.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-card-border bg-card p-2 text-xs">
          <span className="font-bold">{item.time.slice(11, 16)} · {item.source || '智能体'} 请求：{item.label}</span>
          <span className="flex gap-2">
            <button type="button" className="rounded-lg bg-primary px-3 py-1 font-bold text-primary-foreground" onClick={() => void decide(item.id, 'approve')}>同意</button>
            <button type="button" className="rounded-lg border border-card-border px-3 py-1 font-bold" onClick={() => void decide(item.id, 'deny')}>拒绝</button>
          </span>
        </div>
      ))}
    </div>
  )
}
