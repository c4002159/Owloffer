import { useEffect, useRef, useState } from 'react'
import { Search } from 'lucide-react'
import { CONFIG_CATEGORIES, type ConfigCategoryKey } from '@/lib/configCategories'
import { searchSettings, type SettingEntry } from '@/lib/settingsIndex'

const HIGHLIGHT_MS = 2200

/** Scroll to the control called ``label`` and make it flash, once its category has been drawn. */
function revealLabel(label: string, attempts = 12) {
  const wanted = label.replace(/（.*?）|\(.*?\)/g, '').trim()
  const found = [...document.querySelectorAll<HTMLElement>('main label, main h3, main p.text-xs, main span.text-xs, main div.text-sm')]
    .find(element => element.children.length < 3 && (element.textContent || '').includes(wanted))
  if (!found) {
    if (attempts > 0) window.setTimeout(() => revealLabel(label, attempts - 1), 120)
    return
  }
  const target = (found.closest('div') as HTMLElement | null) ?? found
  target.scrollIntoView({ block: 'center', behavior: 'smooth' })
  target.classList.add('ring-2', 'ring-primary', 'ring-offset-2', 'rounded-md', 'transition-shadow')
  window.setTimeout(() => target.classList.remove('ring-2', 'ring-primary', 'ring-offset-2'), HIGHLIGHT_MS)
}

/** Type a word ("发送窗口", "代理", "冷却") and jump to that setting. */
export function SettingsSearch({ onPick }: { onPick: (category: ConfigCategoryKey) => void }) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const results = searchSettings(query)

  useEffect(() => {
    const close = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', close)
    return () => document.removeEventListener('mousedown', close)
  }, [])

  const pick = (entry: SettingEntry) => {
    setOpen(false)
    setQuery('')
    onPick(entry.category)
    window.setTimeout(() => revealLabel(entry.label), 150)
  }

  return (
    <div ref={rootRef} className="relative w-full max-w-sm">
      <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-muted" />
      <input
        type="search"
        aria-label="搜索设置项"
        placeholder="搜索设置项，如：发送窗口、代理、冷却"
        value={query}
        onChange={event => { setQuery(event.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onKeyDown={event => {
          if (event.key === 'Escape') setOpen(false)
          if (event.key === 'Enter' && results[0]) pick(results[0])
        }}
        className="h-9 w-full rounded-md border border-card-border bg-card pl-9 pr-3 text-sm text-foreground outline-none focus:border-primary"
      />
      {open && query.trim() && (
        <div role="listbox" className="absolute left-0 right-0 top-full z-30 mt-1 max-h-80 overflow-y-auto rounded-xl border border-card-border bg-card p-1 shadow-lg">
          {results.length ? results.map(entry => (
            <button key={`${entry.category}:${entry.label}`} type="button" role="option" aria-selected={false} onClick={() => pick(entry)} className="flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm hover:bg-secondary">
              <span className="font-bold text-foreground">{entry.label}</span>
              <span className="shrink-0 text-[11px] text-muted">{CONFIG_CATEGORIES.find(item => item.key === entry.category)?.label}</span>
            </button>
          )) : <p className="px-3 py-3 text-xs text-muted">没有找到「{query}」相关的设置项。</p>}
        </div>
      )}
    </div>
  )
}
