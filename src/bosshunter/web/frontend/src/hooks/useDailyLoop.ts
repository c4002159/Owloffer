import { useCallback, useEffect, useState } from 'react'

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

/** One finished run of the loop: how it started, how it ended and whether the ending deserves a decision from the user. */
export interface LoopRun {
  started_at: string
  ended_at: string
  started_by: 'manual' | 'schedule' | 'agent' | string
  reason: 'user' | 'end_time' | 'cap' | 'limit' | 'crash' | 'unknown' | string
  reason_label: string
  message: string
  needs_attention: boolean
  suggestion: string
  rounds: number
  totals: Record<string, Record<string, number>>
  open_incidents: number
}

export interface DailyLoopState {
  ended?: LoopRun | null
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
  out_today?: Record<string, { code: string; label: string }>      // platforms that reached a day-long limit
  incidents?: DailyLoopIncident[]
  order?: string[]
  modes?: Record<string, string>
  targets?: Record<string, number>
}

export type LoopMode = 'auto' | 'manual' | 'off'

export const LOOP_PLATFORMS = ['zhilian_campus', 'zhilian', 'boss', '51job', 'liepin'] as const
export type LoopPlatform = (typeof LOOP_PLATFORMS)[number]

export interface DailyLoopSettings {
  /** Off: the loop runs around the clock until it is stopped or every platform has used up its day. */
  windowOn: boolean
  start: string
  end: string
  cap: string
  campusTarget: string
  platformTarget: string
  /** Look for HR replies on BOSS now and then (a read-only pass; the replies show up in the message center). */
  monitorOn: boolean
  monitorMinutes: string
  /** The time of day (24-hour clock) the app starts the loop by itself when the schedule is on. */
  scheduleTime: string
  modes: Record<LoopPlatform, LoopMode>
}

export interface DailyLoopSchedule {
  enabled: boolean
  time: string
  next_run_at?: string
  last_start_at?: string
  last_error?: string
  /** A change for today only (skip the automatic start, or start at another time). */
  override?: { day: string; action: 'skip' | 'time'; time?: string } | null
}

const SETTINGS_KEY = 'owloffer.dailyLoop.settings'
const DEFAULTS: DailyLoopSettings = {
  windowOn: false, start: '07:00', end: '20:00', cap: '150', campusTarget: '50', platformTarget: '80',
  monitorOn: true, monitorMinutes: '60', scheduleTime: '07:30',
  modes: { zhilian_campus: 'manual', zhilian: 'auto', boss: 'auto', '51job': 'auto', liepin: 'auto' },
}

function loadSettings(): DailyLoopSettings {
  try {
    const saved = JSON.parse(window.localStorage.getItem(SETTINGS_KEY) || '{}')
    return { ...DEFAULTS, ...saved, modes: { ...DEFAULTS.modes, ...(saved.modes || {}) } }
  } catch {
    return { ...DEFAULTS }
  }
}

async function post(path: string, body: Record<string, unknown> = {}) {
  const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || '操作失败')
  return data
}

const count = (text: string, fallback: number) => Math.max(Number.parseInt(text, 10) || fallback, 0)

/** What the loop is started with: the same for a start by hand and for the app's own daily start. */
export function loopOptions(settings: DailyLoopSettings) {
  const campus = count(settings.campusTarget, 50)
  const other = count(settings.platformTarget, 80)
  return {
    time_window: settings.windowOn,
    ...(settings.windowOn ? { start_time: settings.start, end_time: settings.end } : {}),
    delivery_cap: count(settings.cap, 150),
    monitor_minutes: settings.monitorOn ? Math.max(count(settings.monitorMinutes, 60), 1) : 0,
    modes: settings.modes,
    targets: { zhilian_campus: campus, zhilian: other, boss: other, '51job': other, liepin: other },
  }
}

/** The daily loop: the fixed collect → approve → deliver → repeat job, its settings and its incident list. */
export function useDailyLoop(setNotice: (message: string) => void) {
  const [state, setState] = useState<DailyLoopState>({})
  const [busy, setBusy] = useState(false)
  const [settings, setSettings] = useState<DailyLoopSettings>(loadSettings)
  const [schedule, setSchedule] = useState<DailyLoopSchedule>({ enabled: false, time: DEFAULTS.scheduleTime })
  const [history, setHistory] = useState<LoopRun[]>([])

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/daily-loop')
      if (res.ok) setState(await res.json())
    } catch {
      // the next poll tries again
    }
  }, [])

  const loadHistory = useCallback(async () => {
    try {
      const res = await fetch('/api/daily-loop/history')
      if (res.ok) setHistory((await res.json()).runs ?? [])
    } catch {
      // shown again after the next run
    }
  }, [])

  useEffect(() => {
    void loadHistory()
  }, [loadHistory, state.ended?.ended_at])

  const loadSchedule = useCallback(async () => {
    try {
      const res = await fetch('/api/daily-loop/schedule')
      if (res.ok) setSchedule(await res.json())
    } catch {
      // the next poll tries again
    }
  }, [])

  useEffect(() => {
    void loadSchedule()
    const timer = window.setInterval(() => void loadSchedule(), 60000)
    return () => window.clearInterval(timer)
  }, [loadSchedule])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), state.running ? 5000 : 20000)
    return () => window.clearInterval(timer)
  }, [load, state.running])

  const change = (patch: Partial<DailyLoopSettings>) => {
    const next = { ...settings, ...patch }
    setSettings(next)
    try {
      window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(next))
    } catch {
      // the settings just are not remembered
    }
  }

  /** Turn the app's own daily start on or off (and send it the loop options as they are now). */
  const saveSchedule = async (enabled: boolean, time = settings.scheduleTime) => {
    try {
      setSchedule(await post('/api/daily-loop/schedule', { enabled, time, options: loopOptions(settings) }))
      setNotice(enabled ? `已开启定时：每天 ${time} 自动启动每日循环。` : '已关闭定时启动。')
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '保存定时失败')
      void loadSchedule()
    }
  }

  // While the schedule is on, a change of the loop settings is sent along, so the 07:30 start is the one the user sees here.
  useEffect(() => {
    if (!schedule.enabled) return
    const timer = window.setTimeout(() => {
      post('/api/daily-loop/schedule', { enabled: true, time: schedule.time, options: loopOptions(settings) }).then(setSchedule).catch(() => undefined)
    }, 800)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(loopOptions(settings)), schedule.enabled, schedule.time])

  /** Change only today's automatic start: skip it, move it, or take the change back. */
  const overrideSchedule = async (action: 'skip' | 'time' | 'clear', time?: string) => {
    try {
      setSchedule(await post('/api/daily-loop/schedule/override', { action, ...(time ? { time } : {}) }))
      setNotice(action === 'skip' ? '今天不会自动启动，明天照常。' : action === 'time' ? `今天改到 ${time} 自动启动，明天照常。` : '已取消今天的临时更改。')
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '临时更改失败')
    }
  }

  const start = async () => {
    setBusy(true)
    try {
      setState(await post('/api/daily-loop/start', loopOptions(settings)))
      setNotice('每日循环已启动。')
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '启动失败')
    } finally {
      setBusy(false)
    }
  }

  const stop = async () => {
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

  const incidents = (state.incidents || []).filter(item => !item.resolved)
  return { state, running: Boolean(state.running), busy, settings, change, start, stop, resolve, incidents, schedule, saveSchedule, history, overrideSchedule }
}

export type DailyLoopController = ReturnType<typeof useDailyLoop>
