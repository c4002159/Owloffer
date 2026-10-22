import { useEffect, useState } from 'react'

export const BRAND_NAME = 'OwlOffer'
export const GITHUB_URL = 'https://github.com/c4002159/ai-job'
export const STAR_THRESHOLD = 100                    // collected postings after which the star request appears
const LATER_DAYS = 7

const KEY_STARRED = 'owloffer.starred'
const KEY_PROMPT = 'owloffer.starPrompt'             // '' (never asked) | 'never' | 'later:<ISO date>'
const CHANGE_EVENT = 'owloffer:change'

function read(key: string): string {
  try {
    return window.localStorage.getItem(key) || ''
  } catch {
    return ''
  }
}

function write(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // the choice is just not remembered
  }
  window.dispatchEvent(new Event(CHANGE_EVENT))
}

export function isStarred(): boolean {
  return read(KEY_STARRED) === '1'
}

/** Whether the star request should be on screen now. */
export function shouldAskForStar(collected: number, now: Date = new Date()): boolean {
  if (collected < STAR_THRESHOLD || isStarred()) return false
  const prompt = read(KEY_PROMPT)
  if (prompt === 'never') return false
  if (prompt.startsWith('later:')) {
    const until = new Date(prompt.slice(6))
    return Number.isNaN(until.getTime()) || now >= until
  }
  return true
}

export function markStarred() {
  write(KEY_STARRED, '1')
}

export function askLater(now: Date = new Date()) {
  const until = new Date(now.getTime() + LATER_DAYS * 24 * 3600 * 1000)
  write(KEY_PROMPT, `later:${until.toISOString()}`)
}

export function neverAsk() {
  write(KEY_PROMPT, 'never')
}

/** Open the repository in a new tab, where the user presses Star by hand; the owl turns happy. */
export function openRepositoryForStar() {
  window.open(GITHUB_URL, '_blank', 'noopener,noreferrer')
  markStarred()
}

export function useOwl() {
  const [starred, setStarred] = useState(isStarred)
  useEffect(() => {
    const update = () => setStarred(isStarred())
    window.addEventListener(CHANGE_EVENT, update)
    window.addEventListener('storage', update)
    return () => {
      window.removeEventListener(CHANGE_EVENT, update)
      window.removeEventListener('storage', update)
    }
  }, [])
  return { starred, happy: starred }
}
