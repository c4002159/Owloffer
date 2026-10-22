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
  targets?: Record<string, number>
}

export interface DailyLoopSettings {
  start: string
  end: string
  cap: string
  campusTarget: string
  platformTarget: string
}

const SETTINGS_KEY = 'owloffer.dailyLoop.settings'
const DEFAULTS: DailyLoopSettings = { start: '07:00', end: '20:00', cap: '150', campusTarget: '50', platformTarget: '80' }

function loadSettings(): DailyLoopSettings {
  try {
    return { ...DEFAULTS, ...JSON.parse(window.localStorage.getItem(SETTINGS_KEY) || '{}') }
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

/** The daily loop: the fixed collect → approve → deliver → repeat job, its settings and its incident list. */
export function useDailyLoop(setNotice: (message: string) => void) {
  const [state, setState] = useState<DailyLoopState>({})
  const [busy, setBusy] = useState(false)
  const [settings, setSettings] = useState<DailyLoopSettings>(loadSettings)

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

  const change = (patch: Partial<DailyLoopSettings>) => {
    const next = { ...settings, ...patch }
    setSettings(next)
    try {
      window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(next))
    } catch {
      // the settings just are not remembered
    }
  }

  const start = async () => {
    const campus = count(settings.campusTarget, 50)
    const other = count(settings.platformTarget, 80)
    setBusy(true)
    try {
      setState(await post('/api/daily-loop/start', {
        start_time: settings.start,
        end_time: settings.end,
        delivery_cap: count(settings.cap, 150),
        targets: { zhilian_campus: campus, zhilian: other, boss: other, '51job': other, liepin: other },
      }))
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
  return { state, running: Boolean(state.running), busy, settings, change, start, stop, resolve, incidents }
}

export type DailyLoopController = ReturnType<typeof useDailyLoop>
