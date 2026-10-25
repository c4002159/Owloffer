import { useCallback, useEffect, useState } from 'react'

export interface KeywordSuggestion {
  id: string
  kind: 'pause' | 'add'
  keyword: string
  reason: string
}

export interface KeywordAdvice {
  level3: boolean
  reasons: { code: string; message: string }[]
  suggestions: KeywordSuggestion[]
  popup: boolean
  popup_enabled: boolean
  last_applied: { time: string; changes: { kind: string; keyword: string }[] } | null
  mined?: { baseline: number; lift: { term: string; scored: number; passed: number; rate: number; lift: number }[]; frequent: { term: string; seen: number }[] }
}

const POLL_MS = 5 * 60 * 1000

async function post(path: string, body: unknown): Promise<{ ok: boolean; error?: string }> {
  try {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const data = await res.json().catch(() => ({}))
    return res.ok ? { ok: true } : { ok: false, error: String(data.error || '操作失败') }
  } catch {
    return { ok: false, error: '连不上本地服务' }
  }
}

/** What the keywords' results say (see keyword_advisor.py), with the actions the pop-up and the settings page offer. */
export function useKeywordAdvice() {
  const [advice, setAdvice] = useState<KeywordAdvice | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const reload = useCallback(async () => {
    try {
      const res = await fetch('/api/keywords/advice')
      if (res.ok) setAdvice(await res.json())
    } catch {
      // asked again on the next poll
    }
  }, [])

  useEffect(() => {
    void reload()
    const timer = window.setInterval(() => void reload(), POLL_MS)
    return () => window.clearInterval(timer)
  }, [reload])

  const run = async (path: string, body: unknown) => {
    setBusy(true)
    setError('')
    const result = await post(path, body)
    if (!result.ok) setError(result.error || '操作失败')
    await reload()
    setBusy(false)
    return result.ok
  }

  return {
    advice,
    busy,
    error,
    reload,
    apply: (ids: string[]) => run('/api/keywords/advice/apply', { ids }),
    undo: () => run('/api/keywords/advice/undo', {}),
    /** Ask the model for directions and words the search does not cover; they come back as suggestions. */
    expand: () => run('/api/keywords/expand', {}),
    snooze: (hours = 24) => run('/api/keywords/advice/snooze', { hours }),
    setPopup: (enabled: boolean) => run('/api/keywords/advice/popup', { enabled }),
  }
}
