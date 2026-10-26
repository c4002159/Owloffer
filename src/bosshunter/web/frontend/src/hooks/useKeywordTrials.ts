import { useCallback, useEffect, useState } from 'react'

export interface KeywordTrial {
  keyword: string
  role: string
  started: string
  seen?: number
}

export interface KeywordTrialResult {
  keyword: string
  result: 'kept' | 'dropped' | 'removed'
  reason: string
  jobs: number
  pass_rate: number
  at: string
}

export interface KeywordTrials {
  active: KeywordTrial[]
  history: KeywordTrialResult[]
  last_run: string
  slots: number
}

/** The role names being tried out as keywords and how earlier trials ended (see keyword_trials.py). */
export function useKeywordTrials() {
  const [trials, setTrials] = useState<KeywordTrials | null>(null)
  const [busy, setBusy] = useState(false)

  const reload = useCallback(async () => {
    try {
      const res = await fetch('/api/keywords/trials')
      if (res.ok) setTrials(await res.json())
    } catch {
      // asked again later
    }
  }, [])

  useEffect(() => { void reload() }, [reload])

  const runNow = async () => {
    setBusy(true)
    try {
      await fetch('/api/keywords/trials/run', { method: 'POST' })
    } finally {
      await reload()
      setBusy(false)
    }
  }

  return { trials, busy, runNow }
}
