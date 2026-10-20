import { useEffect, useMemo, useState } from 'react'
import { Loader2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { TagsInput } from '@/components/ui/tags-input'

interface CompanySuggestion {
  company: string
  reasons: string[]
  job_count: number
}

interface BlockedCompaniesDialogProps {
  blocked: string[]
  onChange: (blocked: string[]) => void
  onClose: () => void
}

export function BlockedCompaniesDialog({ blocked, onChange, onClose }: BlockedCompaniesDialogProps) {
  const [suggestions, setSuggestions] = useState<CompanySuggestion[]>([])
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    fetch('/api/companies/suggestions', { cache: 'no-store' })
      .then(async res => {
        const data = await res.json()
        if (!res.ok) throw new Error(data.error || '读取推荐失败')
        return data.suggestions as CompanySuggestion[]
      })
      .then(items => { if (!cancelled) setSuggestions(items) })
      .catch(cause => { if (!cancelled) setError(cause instanceof Error ? cause.message : '读取推荐失败') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const visible = useMemo(
    () => suggestions.filter(item => !blocked.some(rule => rule.trim() && item.company.toLowerCase().includes(rule.trim().toLowerCase()))),
    [suggestions, blocked],
  )

  const toggle = (company: string) => setSelected(prev => {
    const next = new Set(prev)
    if (next.has(company)) next.delete(company)
    else next.add(company)
    return next
  })

  const allSelected = visible.length > 0 && visible.every(item => selected.has(item.company))
  const picked = visible.filter(item => selected.has(item.company)).map(item => item.company)

  const addSelected = () => {
    if (!picked.length) return
    onChange([...new Set([...blocked, ...picked])])
    setSelected(new Set())
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/35 p-4" role="dialog" aria-modal="true" aria-label="屏蔽公司管理">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-2xl border border-card-border bg-card p-5 shadow-xl">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-black text-foreground">屏蔽公司管理</h3>
            <p className="mt-1 text-xs text-muted">加入后不再采集、评分，也不会再进入审查列表。修改后请回到页面点“保存”。</p>
          </div>
          <button type="button" aria-label="关闭" onClick={onClose} className="text-muted hover:text-foreground"><X className="h-4 w-4" /></button>
        </div>

        <div className="mt-4">
          <div className="mb-1.5 text-xs font-bold text-foreground">已屏蔽（手动输入和已加入的都在这里，逐条删除）</div>
          <TagsInput value={blocked} onChange={onChange} placeholder="输入公司名称或关键词后回车" />
        </div>

        <div className="mt-4 flex min-h-0 flex-1 flex-col">
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <div className="text-xs font-bold text-foreground">推荐屏蔽（外包识别 + 重点标记的公司）</div>
            {visible.length > 0 && (
              <label className="flex items-center gap-1.5 text-xs text-muted">
                <input
                  type="checkbox"
                  className="accent-primary"
                  checked={allSelected}
                  onChange={() => setSelected(allSelected ? new Set() : new Set(visible.map(item => item.company)))}
                />
                全选
              </label>
            )}
          </div>
          <div className="min-h-[120px] flex-1 overflow-y-auto rounded-md border border-card-border">
            {loading && <div className="flex items-center gap-2 p-3 text-xs text-muted"><Loader2 className="h-3.5 w-3.5 animate-spin" />读取中…</div>}
            {!loading && error && <div className="p-3 text-xs text-danger">{error}</div>}
            {!loading && !error && visible.length === 0 && <div className="p-3 text-xs text-muted">暂时没有需要推荐屏蔽的公司。</div>}
            {visible.map(item => (
              <label key={item.company} className="flex cursor-pointer items-center gap-3 border-b border-card-border px-3 py-2 last:border-b-0 hover:bg-surface">
                <input type="checkbox" className="accent-primary" checked={selected.has(item.company)} onChange={() => toggle(item.company)} />
                <span className="min-w-0 flex-1 truncate text-sm text-foreground">{item.company}</span>
                <span className="flex flex-wrap justify-end gap-1">
                  {item.reasons.map(reason => (
                    <span key={reason} className="rounded-md bg-secondary px-1.5 py-0.5 text-[11px] font-bold text-primary">{reason}</span>
                  ))}
                </span>
              </label>
            ))}
          </div>
        </div>

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="secondary" onClick={onClose}>完成</Button>
          <Button onClick={addSelected} disabled={!picked.length}>批量加入黑名单{picked.length ? `（${picked.length}）` : ''}</Button>
        </div>
      </div>
    </div>
  )
}
