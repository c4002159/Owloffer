import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { PLATFORM_LABELS } from '@/lib/platforms'
import type { DailyLoopState } from '@/hooks/useDailyLoop'

const POLL_MS = 30 * 1000
const LATER_MS = 30 * 60 * 1000               // "later" hides a platform's question for half an hour in this browser tab; nothing changes on the server

const laterKey = (platform: string) => `platformHoldLater:${platform}`

function askedLater(platform: string): boolean {
  try {
    const at = Number(window.sessionStorage.getItem(laterKey(platform)) || 0)
    return at > 0 && Date.now() - at < LATER_MS
  } catch {
    return false
  }
}

function today(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

/** Platforms the loop took out because of a captcha only the user can pass: asked when it happens and again on every new day. */
export function PlatformHoldPopup() {
  const [state, setState] = useState<DailyLoopState | null>(null)
  const [busy, setBusy] = useState('')
  const [, setTick] = useState(0)

  const reload = useCallback(async () => {
    try {
      const res = await fetch('/api/daily-loop')
      if (res.ok) setState(await res.json())
    } catch {
      // asked again on the next poll
    }
  }, [])

  useEffect(() => {
    void reload()
    const timer = window.setInterval(() => void reload(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [reload])

  const act = async (platform: string, action: 'release' | 'skip') => {
    setBusy(platform)
    try {
      await fetch(`/api/daily-loop/platform/${platform}/${action}`, { method: 'POST' })
    } finally {
      await reload()
      setBusy('')
    }
  }

  const later = (platform: string) => {
    try { window.sessionStorage.setItem(laterKey(platform), String(Date.now())) } catch { /* it simply closes for now */ }
    setTick(value => value + 1)
  }

  const held = state?.held && typeof state.held === 'object' ? Object.entries(state.held) : []
  const pending = held.filter(([platform, item]) => item.skipped_day !== today() && !askedLater(platform))
  if (!pending.length) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="平台需要你确认">
      <div className="max-h-[85vh] w-full max-w-lg space-y-4 overflow-y-auto rounded-2xl border border-card-border bg-card p-6 shadow-xl">
        <h2 className="text-lg font-black text-foreground">有平台需要你确认</h2>
        {pending.map(([platform, item]) => {
          const name = PLATFORM_LABELS[platform as keyof typeof PLATFORM_LABELS] ?? platform
          const newDay = item.day && item.day < today()
          return (
            <div key={platform} className="space-y-2 rounded-xl border border-card-border bg-surface p-3 text-sm">
              <div className="font-black text-foreground">{name}</div>
              <p className="text-muted">
                {newDay
                  ? `新的一天：${name}之前遇到了验证码，一直没有加入循环。问题已经解决了吗？`
                  : `${name}遇到了验证码，已经退出循环。请先在浏览器里手动完成验证，再点下面的按钮。`}
              </p>
              {item.message && <p className="text-xs text-muted">{item.message}</p>}
              <div className="flex flex-wrap gap-2">
                <Button size="sm" disabled={busy === platform} onClick={() => void act(platform, 'release')}>已验证，加入循环</Button>
                <Button size="sm" variant="secondary" disabled={busy === platform} onClick={() => void act(platform, 'skip')}>今天先不加入</Button>
                <Button size="sm" variant="secondary" onClick={() => later(platform)}>稍后再问（30 分钟）</Button>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
