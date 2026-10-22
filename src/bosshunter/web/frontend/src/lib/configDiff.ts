import { CONFIG_CATEGORIES, type ConfigCategoryKey } from '@/lib/configCategories'
import { entryForPath, type SettingEntry } from '@/lib/settingsIndex'

export interface ConfigChange {
  path: string
  before: unknown
  after: unknown
}

export interface DescribedChange extends ConfigChange {
  label: string
  category: ConfigCategoryKey | 'other'
  restart: boolean
  risky: boolean
}

// derived / write-only fields the page adds: not something the user edited
const HIDDEN_PATHS = [/^ai\.(api_key_masked|has_api_key|auth_token_masked|has_auth_token|clear_credentials)$/]
const SECRET_PATHS = [/(^|\.)(api_key|auth_token)$/]

const isObject = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value)

/** What differs between two config trees (arrays are compared as a whole). */
export function diffConfig(before: unknown, after: unknown, prefix = ''): ConfigChange[] {
  if (isObject(before) && isObject(after)) {
    const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    return keys.flatMap(key => diffConfig(before[key], after[key], prefix ? `${prefix}.${key}` : key))
  }
  if (JSON.stringify(before ?? null) === JSON.stringify(after ?? null)) return []
  if (isObject(after) && before === undefined) {
    return Object.keys(after).flatMap(key => diffConfig(undefined, after[key], prefix ? `${prefix}.${key}` : key))
  }
  if (HIDDEN_PATHS.some(pattern => pattern.test(prefix))) return []
  return [{ path: prefix, before, after }]
}

export function formatValue(path: string, value: unknown): string {
  if (SECRET_PATHS.some(pattern => pattern.test(path))) return value ? '••••••（已设置）' : '（空）'
  if (value === undefined || value === null || value === '') return '（空）'
  if (typeof value === 'boolean') return value ? '开' : '关'
  if (Array.isArray(value)) {
    if (!value.length) return '（空）'
    const text = value.map(item => (typeof item === 'object' ? JSON.stringify(item) : String(item))).join('、')
    return text.length > 90 ? `${text.slice(0, 90)}…（共 ${value.length} 项）` : text
  }
  if (isObject(value)) return JSON.stringify(value).slice(0, 90)
  const text = String(value)
  return text.length > 90 ? `${text.slice(0, 90)}…` : text
}

/** Name each change by the setting it belongs to and flag the ones that need care. */
export function describeChanges(changes: ConfigChange[]): DescribedChange[] {
  return changes.map(change => {
    const entry: SettingEntry | undefined = entryForPath(change.path)
    return {
      ...change,
      label: entry?.label ?? change.path,
      category: entry?.category ?? 'other',
      restart: Boolean(entry?.restart),
      risky: Boolean(entry?.risky),
    }
  })
}

export function groupByCategory(changes: DescribedChange[]): Array<{ category: ConfigCategoryKey | 'other'; label: string; items: DescribedChange[] }> {
  const groups: Array<{ category: ConfigCategoryKey | 'other'; label: string; items: DescribedChange[] }> = []
  for (const category of [...CONFIG_CATEGORIES.map(item => item.key), 'other' as const]) {
    const items = changes.filter(change => change.category === category)
    if (items.length) groups.push({ category, label: category === 'other' ? '其他' : CONFIG_CATEGORIES.find(item => item.key === category)!.label, items })
  }
  return groups
}
