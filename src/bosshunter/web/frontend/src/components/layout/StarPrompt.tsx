import { useEffect, useState } from 'react'
import { askLater, neverAsk, openRepositoryForStar, shouldAskForStar, STAR_THRESHOLD, useOwl } from '@/lib/owl'

const POLL_MS = 5 * 60 * 1000

/** Once enough postings were collected, ask once (politely, dismissible) whether the user wants to star the project. */
export function StarPrompt() {
  const [collected, setCollected] = useState(0)
  const [version, setVersion] = useState(0)
  const { starred } = useOwl()

  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const res = await fetch('/api/workbench')
        const data = await res.json()
        if (alive) setCollected(Number(data?.funnel?.['采集总数']) || 0)
      } catch {
        // asked again on the next poll
      }
    }
    void load()
    const timer = window.setInterval(() => void load(), POLL_MS)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  const open = shouldAskForStar(collected) && !starred
  void version
  if (!open) return null
  const close = (action: () => void) => () => {
    action()
    setVersion(value => value + 1)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="给 OwlOffer 点个 Star">
      <div className="w-full max-w-sm rounded-2xl border border-card-border bg-card p-6 text-center shadow-xl">
        <img src="/owl-default.png" alt="" width={112} height={112} className="mx-auto h-28 w-28 rounded-full" />
        <h2 className="mt-3 text-lg font-black text-foreground">已经帮你采集了 {STAR_THRESHOLD}+ 个岗位</h2>
        <p className="mt-2 text-sm leading-6 text-muted">
          如果 OwlOffer 对你有帮助，愿意去 GitHub 给项目点个 Star 吗？这能让更多求职的人找到它。点下面的按钮会在新标签页打开仓库，你点右上角的 ⭐ 就行。
        </p>
        <div className="mt-5 flex flex-col gap-2">
          <button type="button" onClick={close(openRepositoryForStar)} className="rounded-xl bg-primary px-4 py-2.5 text-sm font-bold text-primary-foreground hover:opacity-90">
            去 GitHub 点 Star ⭐
          </button>
          <button type="button" onClick={close(() => askLater())} className="rounded-xl border border-card-border px-4 py-2 text-sm font-bold text-foreground hover:bg-secondary">
            以后再说
          </button>
          <button type="button" onClick={close(neverAsk)} className="text-xs text-muted hover:text-foreground">
            不再提醒
          </button>
        </div>
      </div>
    </div>
  )
}
