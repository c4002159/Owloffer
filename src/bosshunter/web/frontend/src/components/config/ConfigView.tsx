import { createContext, useContext, useEffect, type RefObject } from 'react'

export const CONFIG_GROUPS = [
  { key: 'job', label: '求职偏好', sections: ['profile', 'scoring', 'freshness', 'dedup'] },
  { key: 'collect', label: '采集设置', sections: ['search'] },
  { key: 'deliver', label: '投递与招呼语', sections: ['automation'] },
  { key: 'monitor', label: '监测与跟进', sections: ['monitor', 'follow_up'] },
  { key: 'safety', label: '安全与反监测', sections: ['collection'] },
  { key: 'system', label: '系统', sections: ['ai', 'browser', 'system'] },
] as const

export type GroupKey = (typeof CONFIG_GROUPS)[number]['key']

export function groupOfSection(sectionKey: string): GroupKey {
  return CONFIG_GROUPS.find(g => (g.sections as readonly string[]).includes(sectionKey))?.key ?? 'job'
}

/** 需要重启服务才生效的分区。 */
export const RESTART_SECTIONS = new Set(['browser'])

interface ConfigView {
  group: GroupKey
  query: string
}

export const ConfigViewContext = createContext<ConfigView>({ group: 'job', query: '' })
export const useConfigView = () => useContext(ConfigViewContext)

/**
 * 搜索设置项：query 非空时，按文字隐藏不匹配的字段和整个分区。
 * 字段由 `data-field` 标记，分区由 `data-section` 标记（标题放在 `data-title`）。
 */
export function useSettingsSearch(root: RefObject<HTMLElement | null>, query: string, deps: unknown[]) {
  useEffect(() => {
    const el = root.current
    if (!el) return
    const q = query.trim().toLowerCase()
    el.querySelectorAll<HTMLElement>('[data-field]').forEach(f => {
      f.style.display = !q || (f.textContent ?? '').toLowerCase().includes(q) ? '' : 'none'
    })
    el.querySelectorAll<HTMLElement>('[data-section]').forEach(s => {
      if (!q) { s.style.display = ''; return }
      const titleHit = (s.dataset.title ?? '').toLowerCase().includes(q)
      if (titleHit) {
        s.querySelectorAll<HTMLElement>('[data-field]').forEach(f => { f.style.display = '' })
        s.style.display = ''
        return
      }
      const anyField = Array.from(s.querySelectorAll<HTMLElement>('[data-field]')).some(f => f.style.display !== 'none')
      s.style.display = anyField ? '' : 'none'
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query, ...deps])
}
