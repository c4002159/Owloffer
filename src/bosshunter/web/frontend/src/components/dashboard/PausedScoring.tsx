import { useCallback, useEffect, useState } from 'react'

export interface ScoringRun {
  id: string
  status: string
  pause_reason?: string
  created_at?: string
  updated_at?: string
  remaining_job_ids?: string[]
  progress?: { ai_completed?: number; ai_total?: number; ai_passed?: number }
}

/** Scoring runs that stopped half way (user pause, restart, error) and wait to be continued or ended. */
export function useScoringRuns() {
  const [paused, setPaused] = useState<ScoringRun[]>([])
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/scoring/runs')
      if (!res.ok) return
      const runs: ScoringRun[] = await res.json()
      setPaused(runs.filter(run => run.status === 'paused'))
    } catch {
      // the next poll tries again
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 20000)
    return () => window.clearInterval(timer)
  }, [load])

  const act = async (run: ScoringRun, action: 'resume' | 'end', after?: () => void) => {
    setBusy(run.id)
    setError('')
    try {
      const res = await fetch(`/api/scoring/runs/${run.id}/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) throw new Error([data.error, ...(data.messages || [])].filter(Boolean).join('；') || '操作失败')
      await load()
      after?.()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '操作失败')
    } finally {
      setBusy('')
    }
  }

  return { paused, busy, error, resume: (run: ScoringRun, after?: () => void) => act(run, 'resume', after), end: (run: ScoringRun, after?: () => void) => act(run, 'end', after) }
}

export function describeScoringRun(run: ScoringRun) {
  const done = run.progress?.ai_completed
  const total = run.progress?.ai_total
  const remaining = run.remaining_job_ids?.length ?? 0
  const progress = typeof done === 'number' && typeof total === 'number' ? `已评 ${done}/${total}` : ''
  return `${progress}${progress ? '，' : ''}还剩 ${remaining} 个岗位没评分`
}

/** One clear line per paused scoring run, with what to do about it. */
export function PausedScoringBanner({ runs, onChanged }: { runs: ReturnType<typeof useScoringRuns>; onChanged?: () => void }) {
  if (!runs.paused.length) return null
  return (
    <div className="space-y-2" role="alert">
      {runs.paused.map(run => (
        <div key={run.id} className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-warning-border bg-warning-soft px-4 py-3 text-sm text-warning-strong">
          <div className="min-w-0">
            <div className="font-black">有一个评分任务停在半路，还没做完</div>
            <p className="mt-1 text-xs leading-5">
              {describeScoringRun(run)}。停下的原因：{run.pause_reason || '用户暂停或任务中断'}。
              {run.updated_at ? `（${run.updated_at.slice(5, 16)}）` : ''}
              在它处理之前，新的「一键 AI 评分」会被拦下。
            </p>
          </div>
          <div className="flex shrink-0 gap-2">
            <button type="button" disabled={runs.busy === run.id} onClick={() => void runs.resume(run, onChanged)} className="rounded-lg bg-primary px-3 py-1.5 text-xs font-bold text-primary-foreground disabled:opacity-50">继续评分</button>
            <button type="button" disabled={runs.busy === run.id} onClick={() => { if (window.confirm('结束这个评分任务？已经评完的结果会保留，没评的岗位保持待评分，之后可以重新评。')) void runs.end(run, onChanged) }} className="rounded-lg border border-warning-border bg-card px-3 py-1.5 text-xs font-bold text-foreground disabled:opacity-50">结束这个任务</button>
          </div>
        </div>
      ))}
      {runs.error && <p className="rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger">{runs.error}</p>}
    </div>
  )
}
