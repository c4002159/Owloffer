import { useEffect, useRef, useState } from 'react'
import { Play, Settings2, Square } from 'lucide-react'
import { cn } from '@/lib/utils'
import { PLATFORM_LABELS } from '@/lib/platforms'
import type { DailyLoopController, DailyLoopIncident } from '@/hooks/useDailyLoop'

const STEP_LABELS: Record<string, string> = {
  starting: '启动', waiting: '等待开始', check: '检查', collect: '采集', approve: '批准', greet: '生成招呼语', deliver: '投递', idle: '等待', finished: '已结束', api: '接口', loop: '循环',
}
const TOTAL_LABELS: Record<string, string> = { collected: '新增', approved: '已批准', delivered: '已投递', failed: '失败' }

function clock(iso?: string) {
  const match = /T(\d{2}:\d{2})/.exec(iso || '')
  return match ? match[1] : ''
}

/** "运行全流程": the daily loop as one big card, with its settings behind the gear. */
export function DailyLoopCard({ loop, blocked, onBlocked }: { loop: DailyLoopController; blocked: boolean; onBlocked: () => void }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const { settings, change, running, busy } = loop
  // the settings popover closes on a click anywhere else, and on Escape
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
  const onClick = () => {
    if (running) {
      if (window.confirm('停止每日循环？正在运行的采集或投递任务会在当前一步结束后停止。')) void loop.stop()
      return
    }
    if (blocked) {
      onBlocked()
      return
    }
    const cap = Number.parseInt(settings.cap, 10) || 0
    if (window.confirm(`启动每日循环？\n\n时间：${settings.start}–${settings.end}；每个平台最多投递 ${cap || '不限'} 个。\n按“智联校招 ${settings.campusTarget} → 智联 → BOSS → 前程无忧 → 猎聘（各 ${settings.platformTarget}）”依次采集，每个平台采完自动批准并投递（智联校招留给你手动投递），投完再采下一轮；到结束时间或所有平台都到投递上限就自动停止。`)) void loop.start()
  }
  return (
    <div ref={rootRef} className="relative">
      <div
        className={cn(
          'flex min-h-32 flex-col justify-between rounded-xl border p-4 transition md:p-5',
          running ? 'border-primary bg-primary text-primary-foreground' : blocked ? 'border-card-border bg-card text-muted opacity-60' : 'border-card-border bg-surface text-foreground hover:border-primary/60 hover:shadow-md',
        )}
      >
        <button type="button" disabled={busy} onClick={onClick} className="flex w-full items-start justify-between gap-2 text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">
          <span className="text-2xl font-bold tracking-tight">{running ? '运行全流程中' : '运行全流程'}</span>
          <span className="flex h-9 w-9 shrink-0 items-center justify-center">{running ? <Square className="h-4 w-4 fill-current" /> : <Play className="h-5 w-5 fill-current" />}</span>
        </button>
        <div className="flex items-end justify-between gap-2">
          <p className={cn('text-xs leading-5', running ? 'text-primary-foreground/85' : 'text-muted')}>
            {running ? `${settings.start}–${clock(loop.state.end_at) || settings.end} · 每平台最多投递 ${loop.state.delivery_cap || '不限'}` : '每日自动：采集 → 批准 → 投递，循环到结束时间'}
          </p>
          {!running && (
            <button type="button" aria-label="每日循环设置" aria-expanded={open} onClick={() => setOpen(value => !value)} className="shrink-0 rounded-lg p-1.5 text-muted hover:bg-secondary hover:text-primary">
              <Settings2 className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>
      {open && !running && (
        <div className="absolute left-0 right-0 top-full z-20 mt-2 space-y-3 rounded-xl border border-card-border bg-card p-4 text-xs shadow-lg">
          <div className="font-black text-foreground">每日循环设置</div>
          <div className="grid grid-cols-2 gap-3">
            <label className="space-y-1 font-bold">开始时间<input type="time" value={settings.start} onChange={e => change({ start: e.target.value })} className="w-full rounded-lg border border-card-border bg-surface px-2 py-1.5" /></label>
            <label className="space-y-1 font-bold">结束时间<input type="time" value={settings.end} onChange={e => change({ end: e.target.value })} className="w-full rounded-lg border border-card-border bg-surface px-2 py-1.5" /></label>
            <label className="space-y-1 font-bold">校招目标（新增岗位）<input type="number" min={1} value={settings.campusTarget} onChange={e => change({ campusTarget: e.target.value })} className="w-full rounded-lg border border-card-border bg-surface px-2 py-1.5" /></label>
            <label className="space-y-1 font-bold">其他平台目标（各）<input type="number" min={1} value={settings.platformTarget} onChange={e => change({ platformTarget: e.target.value })} className="w-full rounded-lg border border-card-border bg-surface px-2 py-1.5" /></label>
            <label className="col-span-2 space-y-1 font-bold">每个平台每天投递上限（0 = 不限）<input type="number" min={0} max={1000} value={settings.cap} onChange={e => change({ cap: e.target.value })} className="w-full rounded-lg border border-card-border bg-surface px-2 py-1.5" /></label>
          </div>
          <p className="leading-5 text-muted">设置会记住。采集时间窗口、发送窗口在「配置 → 反监测」里调整。</p>
        </div>
      )}
    </div>
  )
}

/** The loop's progress line and per-platform totals (shown in today's activity). */
export function DailyLoopProgress({ loop }: { loop: DailyLoopController }) {
  const { state, running } = loop
  const totals = state.totals || {}
  if (!running && state.step !== 'finished') return null
  return (
    <div className="space-y-2">
      <div className="rounded-lg bg-card px-3 py-2 text-xs text-foreground">
        <span className="mr-2 font-black">每日循环</span>
        第 {state.round || 0} 轮 · {STEP_LABELS[state.step || ''] || state.step} · {state.message}
      </div>
      {Object.keys(totals).length > 0 && (
        <div className="flex flex-wrap gap-2 text-xs">
          {Object.entries(totals).map(([platform, values]) => (
            <span key={platform} className="rounded-full border border-card-border bg-card px-3 py-1 font-bold text-foreground">
              {PLATFORM_LABELS[platform] || platform} {Object.entries(values).map(([key, value]) => `${TOTAL_LABELS[key] || key} ${value}`).join(' · ')}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

/** Open incidents of the loop: what went wrong and needs a person (or a watcher) to fix. */
export function DailyLoopIncidents({ loop }: { loop: DailyLoopController }) {
  const [open, setOpen] = useState('')
  const { incidents } = loop
  if (!incidents.length) return <p className="py-2 text-xs text-muted">没有待处理的异常。</p>
  const copy = () => navigator.clipboard.writeText(JSON.stringify(incidents, null, 1)).catch(() => undefined)
  return (
    <div className="space-y-2">
      <div className="flex justify-end">
        <button type="button" onClick={() => void copy()} className="rounded-lg border border-card-border bg-card px-2 py-1 text-xs font-bold text-foreground">复制全部详情</button>
      </div>
      {incidents.map((item: DailyLoopIncident) => (
        <div key={item.id} className="rounded-lg border border-danger-border bg-danger-soft p-2 text-xs">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="font-bold">
              {clock(item.time)} · {item.platform_name || '全局'} · {STEP_LABELS[item.step] || item.step}
              {item.count > 1 ? ` · 重复 ${item.count} 次` : ''}
            </div>
            <div className="flex gap-2">
              {item.detail && <button type="button" onClick={() => setOpen(open === item.id ? '' : item.id)} className="font-bold text-primary">{open === item.id ? '收起' : '详情'}</button>}
              <button type="button" onClick={() => void loop.resolve(item.id)} className="font-bold text-muted">已处理</button>
            </div>
          </div>
          <div className="mt-1 text-danger">{item.message}</div>
          {open === item.id && <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-card p-2 text-[11px] text-muted">{item.detail}</pre>}
        </div>
      ))}
    </div>
  )
}
