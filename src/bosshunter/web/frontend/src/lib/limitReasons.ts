import { entryForPath } from '@/lib/settingsIndex'

/** The setting each stop reason (a code the program sends) belongs to. The names shown are the settings' own names. */
const SETTING_OF: Record<string, string> = {
  daily_platform_page_limit: 'safety.daily_platform_page_limit',
  daily_search_page_limit: 'collection.daily_search_page_limit',
  daily_detail_page_limit: 'collection.daily_detail_page_limit',
  persistent_risk_lock: 'safety.risk_lock_minutes',
  daily_limit: 'throttle.daily_limit',
}
const OWN_BUDGET = ['51job', 'liepin', 'zhilian', 'zhilian_campus']

export interface LimitInfo {
  /** Same words as the label of the setting on the settings page. */
  label: string
  path: string
  /** The settings page, already on the right group and asking to flash the setting. */
  href: string
  /** A sentence for the user. */
  text: string
}

export function settingHref(path: string): string | null {
  const entry = entryForPath(path)
  return entry ? `/config?section=${entry.category}&focus=${encodeURIComponent(entry.label)}` : null
}

/** What a stop reason means and where to change it; null for reasons that are not a limit. */
export function describeLimit(reason: string, platform = 'boss'): LimitInfo | null {
  const code = Object.keys(SETTING_OF).find(key => reason.includes(key))
  if (!code) return null
  const path = code === 'daily_search_page_limit' && OWN_BUDGET.includes(platform)
    ? `collection.platform_limits.${platform}.daily_search_page_limit`
    : SETTING_OF[code]
  const entry = entryForPath(path)
  const href = settingHref(path)
  if (!entry || !href) return null
  const text = code === 'persistent_risk_lock' ? `平台风险冷却中（见「${entry.label}」）` : `已达到「${entry.label}」`
  return { label: entry.label, path, href, text }
}
