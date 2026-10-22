import { useCallback, useEffect, useState } from 'react'
import { Play, Square } from 'lucide-react'

export interface DailyLoopIncident {
  id: string
  time: string
  last_seen: string
  platform: string
  platform_name: string
  step: string
  code: string
  message: string
  detail: string
  count: number
  resolved: boolean
}

export interface DailyLoopState {
  alive?: boolean
  running?: boolean
  started_at?: string
  end_at?: string
  start_at?: string
  delivery_cap?: number
  round?: number
  platform?: string
  step?: string
  message?: string
  totals?: Record<string, Record<string, number>>
  incidents?: DailyLoopIncident[]
  order?: string[]
}

const STEP_LABELS: Record<string, string> = {
  starting: '启动', check: '检查', collect: '采集', approve: '批准', greet: '生成招呼语', deliver: '投递', idle: '等待', finished: '已结束', api: '接口', loop: '循环',
}

const TOTAL_LABELS: Record<string, string> = { collected: '新增', approved: '已批准', delivered: '已投递', failed: '失败' }

const SETTINGS_KEY = 'bosshunter.dailyLoop.settings'

function loadSettings() {
  try {
    const saved = JSON.parse(window.localStorage.getItem(SETTINGS_KEY) || '{}')
    return { start: String(saved.start || '07:00'), end: String(saved.end || '20:00'), cap: String(saved.cap ?? '150') }
  } catch {
    return { start: '07:00', end: '20:00', cap: '150' }
  }
}

function clock(iso?: string) {
  const match = /T(\d{2}:\d{2})/.exec(iso || '')
  return match ? match[1] : ''
}

export function DailyLoopPanel({ setNotice }: { setNotice: (message: string) => void }) {
  const [state, setState] = useState<DailyLoopState>({})
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState<string>('')
  const [settings, setSettings] = useState(loadSettings)

  const change = (patch: Partial<typeof settings>) => {
    const next = { ...settings, ...patch }
    setSettings(next)
    try {
      window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(next))
    } catch {
      // the settings just are not remembered
    }
  }

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/daily-loop')
      if (res.ok) setState(await res.json())
    } catch {
      // the next poll tries again
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), state.running ? 5000 : 20000)
    return () => window.clearInterval(timer)
  }, [load, state.running])

  const post = async (path: string, body: Record<string, unknown> = {}) => {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.error || '操作失败')
    return data
  }

  const start = async () => {
    if (!window.confirm('启动每日循环？\n\n会按“智联校招 50 → 智联 → BOSS → 前程无忧 → 猎聘（各 80）”依次采集，每个平台采完自动批准待确认岗位并投递（智联校招除外，留给你手动投递），投完再采下一轮，到结束时间或所有平台都到投递上限就自动停止。')) return
    setBusy(true)
    try {
      setState(await post('/api/daily-loop/start', { start_time: settings.start, end_time: settings.end, delivery_cap: Math.max(Number.parseInt(settings.cap, 10) || 0, 0) }))
      setNotice('每日循环已启动。')
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '启动失败')
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
    if (!window.confirm('停止每日循环？正在运行的采集或投递任务会在当前一步结束后停止。')) return
    setBusy(true)
    try {
      setState(await post('/api/daily-loop/stop'))
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '停止失败')
    } finally {
      setBusy(false)
    }
  }

  const resolve = async (id: string) => {
    try {
      setState(await post(`/api/daily-loop/incidents/${id}/resolve`))
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '操作失败')
    }
  }

  const running = Boolean(state.running)
  const incidents = (state.incidents || []).filter(item => !item.resolved)
  const totals = state.totals || {}

  const copyIncidents = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(incidents, null, 1))
      setNotice('异常详情已复制。')
    } catch {
      setNotice('复制失败，请手动选择文字。')
    }
  }

  return (
    <section className="mt-3 rounded-xl border border-card-border bg-surface p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="text-lg font-black tracking-tight">每日循环{running && <span className="ml-2 rounded-full bg-primary px-2 py-0.5 text-[11px] font-bold text-primary-foreground">运行中</span>}</div>
          <p className="mt-1 text-xs leading-5 text-muted">
            固定流程：先采智联校招 50 个，再依次采智联、BOSS、前程无忧、猎聘（各 80 个）；每个平台采完自动批准并投递（智联校招留给你手动投递），投完再采下一轮；出错只记录、不中断。
            {running && state.end_at ? ` 运行到 ${clock(state.end_at)}，每个平台最多投递 ${state.delivery_cap || '不限'} 个。` : ''}
          </p>
          {!running && (
            <div className="mt-2 flex flex-wrap items-center gap-3 text-xs font-bold text-foreground">
              <label className="flex items-center gap-1">开始<input type="time" value={settings.start} onChange={event => change({ start: event.target.value })} className="rounded-lg border border-card-border bg-card px-2 py-1" /></label>
              <label className="flex items-center gap-1">结束<input type="time" value={settings.end} onChange={event => change({ end: event.target.value })} className="rounded-lg border border-card-border bg-card px-2 py-1" /></label>
              <label className="flex items-center gap-1">每个平台投递上限<input type="number" min={0} max={1000} value={settings.cap} onChange={event => change({ cap: event.target.value })} className="w-20 rounded-lg border border-card-border bg-card px-2 py-1" />（0 = 不限）</label>
            </div>
          )}
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => void (running ? stop() : start())}
          className={`inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm font-bold ${running ? 'border border-card-border bg-card text-foreground' : 'bg-primary text-primary-foreground'} disabled:opacity-50`}
        >
          {running ? <Square className="h-4 w-4 fill-current" /> : <Play className="h-4 w-4 fill-current" />}
          {running ? '停止每日循环' : '启动每日循环'}
        </button>
      </div>
      {(running || state.step === 'finished') && (
        <div className="mt-3 rounded-lg bg-card px-3 py-2 text-xs text-foreground">
          第 {state.round || 0} 轮 · {STEP_LABELS[state.step || ''] || state.step} · {state.message}
        </div>
      )}
      {Object.keys(totals).length > 0 && (
        <div className="mt-3 flex flex-wrap gap-2 text-xs">
          {Object.entries(totals).map(([platform, values]) => (
            <span key={platform} className="rounded-full border border-card-border bg-card px-3 py-1 font-bold text-foreground">
              {platform} {Object.entries(values).map(([key, value]) => `${TOTAL_LABELS[key] || key} ${value}`).join(' · ')}
            </span>
          ))}
        </div>
      )}
      {incidents.length > 0 && (
        <div className="mt-3 rounded-lg border border-danger-border bg-danger-soft p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm font-black text-danger">异常 {incidents.length} 条（待处理）</div>
            <button type="button" onClick={() => void copyIncidents()} className="rounded-lg border border-danger-border bg-card px-2 py-1 text-xs font-bold text-danger">复制全部详情</button>
          </div>
          <ul className="mt-2 space-y-2">
            {incidents.map(item => (
              <li key={item.id} className="rounded-lg bg-card p-2 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="font-bold">
                    {clock(item.time)} · {item.platform_name || '全局'} · {STEP_LABELS[item.step] || item.step}
                    {item.count > 1 ? ` · 重复 ${item.count} 次` : ''}
                  </div>
                  <div className="flex gap-2">
                    {item.detail && <button type="button" onClick={() => setOpen(open === item.id ? '' : item.id)} className="font-bold text-primary">{open === item.id ? '收起' : '详情'}</button>}
                    <button type="button" onClick={() => void resolve(item.id)} className="font-bold text-muted">已处理</button>
                  </div>
                </div>
                <div className="mt-1 text-danger">{item.message}</div>
                {open === item.id && <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap rounded bg-surface p-2 text-[11px] text-muted">{item.detail}</pre>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}
