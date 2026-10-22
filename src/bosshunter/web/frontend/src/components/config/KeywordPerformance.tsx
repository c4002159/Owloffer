import { useCallback, useEffect, useState } from 'react'
import { Pin, PinOff } from 'lucide-react'

interface Score { n: number; pass_rate: number; active_share: number; reply_rate: number; novelty: number; score: number | null }
interface Move { keyword: string; from_tier: string; to_tier: string; reason: string; created_at: string }
interface Strategy {
  tiers: Record<'core' | 'expand' | 'explore' | 'paused', string[]>
  scores: Record<string, Score>
  pinned: string[]
  paused: Record<string, string>
  log: Move[]
}

const TIER_LABEL: Record<string, string> = { core: '核心', expand: '扩展', explore: '探索', paused: '暂停', intern: '实习' }
const percent = (value: number) => `${Math.round(value * 100)}%`

export function KeywordPerformance({ refreshKey }: { refreshKey?: unknown }) {
  const [data, setData] = useState<Strategy | null>(null)

  const load = useCallback(() => {
    fetch('/api/keywords/strategy', { cache: 'no-store' })
      .then(res => res.json())
      .then((next: Partial<Strategy> | null) => {
        if (next && next.tiers && next.scores) setData({ pinned: [], paused: {}, log: [], ...next } as Strategy)
      })
      .catch(() => { /* 没有数据时不显示这个面板 */ })
  }, [])
  useEffect(load, [load, refreshKey])

  const togglePin = async (keyword: string, tier: string, pinned: boolean) => {
    await fetch('/api/keywords/pin', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ keyword, pinned, tier: tier === 'paused' ? undefined : tier }),
    })
    load()
  }

  if (!data) return null
  const rows = (['core', 'expand', 'explore', 'paused'] as const).flatMap(tier => (data.tiers?.[tier] ?? []).map(word => ({ word, tier })))
  if (!rows.length) return null

  return (
    <div className="mb-3 rounded-lg border border-card-border bg-card p-3">
      <div className="text-xs font-bold text-foreground">关键词表现与梯队</div>
      <p className="mt-1 text-[11px] leading-5 text-muted">
        近 14 天：通过率、HR 活跃占比、回复率、新增占比综合成分数（至少 10 条才评分）。连续表现好的词自动升档，连续靠后的核心词降档，探索词 15 条仍 0 通过则暂停 7 天；固定后不再自动调整。
      </p>
      <div className="mt-2 overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-muted"><tr><th className="py-1 pr-3">关键词</th><th className="pr-3">梯队</th><th className="pr-3">采集</th><th className="pr-3">通过</th><th className="pr-3">HR 活跃</th><th className="pr-3">分数</th><th /></tr></thead>
          <tbody>
            {rows.map(({ word, tier }) => {
              const score = data.scores[word]
              const pinned = data.pinned.includes(word)
              return (
                <tr key={word} className="border-t border-card-border">
                  <td className="py-1 pr-3 font-semibold text-foreground">{word}</td>
                  <td className="pr-3">{TIER_LABEL[tier]}{tier === 'paused' && data.paused[word] ? `（至 ${data.paused[word]}）` : ''}</td>
                  <td className="pr-3">{score?.n ?? 0}</td>
                  <td className="pr-3">{score ? percent(score.pass_rate) : '-'}</td>
                  <td className="pr-3">{score ? percent(score.active_share) : '-'}</td>
                  <td className="pr-3">{score?.score != null ? score.score.toFixed(2) : '样本不足'}</td>
                  <td>
                    <button type="button" title={pinned ? '取消固定' : '固定在当前梯队'} className="text-muted hover:text-primary" onClick={() => void togglePin(word, tier, !pinned)}>
                      {pinned ? <PinOff className="h-3.5 w-3.5" /> : <Pin className="h-3.5 w-3.5" />}
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      {data.log.length > 0 && (
        <div className="mt-2 space-y-0.5 text-[11px] text-muted">
          <div className="font-bold text-foreground">最近的梯队变动</div>
          {data.log.slice(0, 6).map((move, index) => (
            <div key={`${move.keyword}-${index}`}>
              {move.to_tier === 'paused' ? '⏸' : ['core', 'expand'].indexOf(move.to_tier) < ['core', 'expand', 'explore'].indexOf(move.from_tier) ? '↑' : '↓'}
              {' '}「{move.keyword}」{TIER_LABEL[move.from_tier] ?? move.from_tier} → {TIER_LABEL[move.to_tier] ?? move.to_tier}：{move.reason}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
