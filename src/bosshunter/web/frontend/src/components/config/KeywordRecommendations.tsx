import { useEffect, useState } from 'react'
import { Loader2, Plus, Sparkles } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface Direction {
  name: string
  evidence: string
  keywords: string[]
}

interface KeywordPlan {
  directions: Direction[]
  core: string[]
  expand: string[]
  explore: string[]
  intern?: string[]
  excludes: string[]
}

interface KeywordRecommendationsProps {
  keywords: string[]
  onChange: (keywords: string[]) => void
  internKeywords?: string[]
  onInternChange?: (keywords: string[]) => void
}

const TIERS: Array<{ key: 'core' | 'expand' | 'explore' | 'intern'; label: string; hint: string }> = [
  { key: 'core', label: '核心词', hint: '与简历高度吻合，每次都搜' },
  { key: 'expand', label: '扩展词', hint: '相邻方向，建议轮换搜' },
  { key: 'explore', label: '探索词', hint: '证据较弱，少量试探' },
  { key: 'intern', label: '实习词', hint: '开发类和产品类实习，单独搜实习岗位' },
]

export function KeywordRecommendations({ keywords, onChange, internKeywords = [], onInternChange }: KeywordRecommendationsProps) {
  const [plan, setPlan] = useState<KeywordPlan | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    fetch('/api/keywords/plan', { cache: 'no-store' })
      .then(res => res.json())
      .then(data => { if (!cancelled) setPlan(data.plan ?? null) })
      .catch(() => { /* 没有缓存方案时只显示“生成推荐”按钮。 */ })
    return () => { cancelled = true }
  }, [])

  const generate = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch('/api/keywords/generate', { method: 'POST' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '生成失败，请重试')
      setPlan(data.plan)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '生成失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  const listFor = (tier: string) => (tier === 'intern' ? internKeywords : keywords)
  const has = (tier: string, word: string) => listFor(tier).some(item => item.trim().toLowerCase() === word.trim().toLowerCase())
  const add = (tier: string, words: string[]) => {
    const fresh = words.filter(word => word.trim() && !has(tier, word))
    if (!fresh.length) return
    if (tier === 'intern') onInternChange?.([...internKeywords, ...fresh])
    else onChange([...keywords, ...fresh])
  }

  return (
    <div className="mb-3 rounded-xl border border-card-border bg-card p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 text-xs font-bold text-foreground"><Sparkles className="h-3.5 w-3.5 text-primary" />AI 推荐关键词</div>
        <Button size="sm" variant="secondary" onClick={() => void generate()} disabled={loading}>
          {loading ? <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" /> : null}
          {plan ? '重新生成' : '根据简历生成推荐'}
        </Button>
      </div>
      {error && <p className="mt-2 text-xs text-danger">{error}</p>}
      {!plan && !error && <p className="mt-2 text-xs text-muted">读取简历和 GitHub 项目证据，按岗位方向分层推荐，点击词条即可单独添加到下面的搜索关键词。</p>}
      {plan && (
        <div className="mt-3 space-y-3">
          {TIERS.map(tier => (plan[tier.key] ?? []).length > 0 && (
            <div key={tier.key}>
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-xs text-foreground"><b>{tier.label}</b><span className="ml-2 text-muted">{tier.hint}</span></span>
                <button type="button" className="text-[11px] font-bold text-primary hover:underline" onClick={() => add(tier.key, plan[tier.key] ?? [])}>全部添加</button>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {(plan[tier.key] ?? []).map(word => (
                  <button
                    key={word}
                    type="button"
                    disabled={has(tier.key, word)}
                    onClick={() => add(tier.key, [word])}
                    className="inline-flex items-center gap-1 rounded-md border border-card-border bg-surface px-2 py-0.5 text-xs text-foreground hover:border-primary hover:text-primary disabled:cursor-default disabled:opacity-50"
                  >
                    {!has(tier.key, word) && <Plus className="h-3 w-3" />}{word}
                  </button>
                ))}
              </div>
            </div>
          ))}
          {plan.directions.length > 0 && (
            <div className="text-[11px] leading-5 text-muted">
              岗位方向：{plan.directions.map(direction => `${direction.name}（${direction.evidence}）`).join('；')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
