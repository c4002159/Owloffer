import { useState } from 'react'
import { TagsInput } from '@/components/ui/tags-input'
import { Button } from '@/components/ui/button'
import { KeywordRecommendations } from '@/components/config/KeywordRecommendations'
import { KeywordPerformance } from '@/components/config/KeywordPerformance'
import { SettingField } from './fields'
import { PLATFORM_LABELS } from '@/lib/platforms'

export type KeywordPlatform = 'boss' | 'zhilian' | 'zhilian_campus' | '51job' | 'liepin'
const ALL_PLATFORMS: KeywordPlatform[] = ['boss', 'zhilian', 'zhilian_campus', '51job', 'liepin']

interface KeywordSettingsProps {
  config: Record<string, any>
  platformSearch: (platform: KeywordPlatform) => Record<string, any>
  updatePlatformSearch: (platform: KeywordPlatform, key: string, value: unknown) => void
  updateConfig: (path: string, value: unknown) => void
}

/**
 * One place for the search keywords of every platform: pick the platforms a change applies to (or all of them), add the AI's
 * recommended words, and copy a platform's list to all the others.
 */
export function KeywordSettings({ config, platformSearch, updatePlatformSearch, updateConfig }: KeywordSettingsProps) {
  const enabled = (platform: KeywordPlatform) => config.platforms?.[platform]?.enabled ?? platform === 'boss'
  const [targets, setTargets] = useState<KeywordPlatform[]>(() => {
    const on = ALL_PLATFORMS.filter(enabled)
    return on.length ? on : ['boss']
  })
  const wordsOf = (platform: KeywordPlatform): string[] => {
    const value = platformSearch(platform).keywords
    return Array.isArray(value) ? value : []
  }
  const shown = wordsOf(targets[0] ?? 'boss')
  const apply = (words: string[]) => targets.forEach(platform => updatePlatformSearch(platform, 'keywords', words))
  const toggle = (platform: KeywordPlatform) =>
    setTargets(current => current.includes(platform) ? (current.length > 1 ? current.filter(item => item !== platform) : current) : [...current, platform])
  const differs = targets.some(platform => wordsOf(platform).join('\n') !== shown.join('\n'))
  const internKeywords = Array.isArray(config.search?.intern_keywords) ? config.search.intern_keywords : []

  return (
    <div data-tour="search-keywords" className="space-y-4">
      <div className="space-y-2 rounded-xl border border-card-border bg-surface p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs font-black text-foreground">这次修改应用到哪些平台</span>
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" onClick={() => setTargets([...ALL_PLATFORMS])}>全选</Button>
            <Button size="sm" variant="secondary" disabled={!differs} onClick={() => apply(shown)} title="把当前显示的词表写到所有已选平台">同步词表到已选平台</Button>
            <Button size="sm" onClick={() => { setTargets([...ALL_PLATFORMS]); ALL_PLATFORMS.forEach(platform => updatePlatformSearch(platform, 'keywords', shown)) }} title="把当前显示的词表应用到全部 5 个平台">应用到所有平台</Button>
          </div>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {ALL_PLATFORMS.map(platform => (
            <button key={platform} type="button" onClick={() => toggle(platform)}
              className={`rounded-full border px-3 py-1 text-xs font-bold ${targets.includes(platform) ? 'border-primary bg-secondary text-primary' : 'border-card-border bg-card text-muted hover:border-primary/40'}`}>
              {PLATFORM_LABELS[platform]} · {wordsOf(platform).length}
              {!enabled(platform) && <span className="ml-1 text-[10px] text-muted">未启用</span>}
            </button>
          ))}
        </div>
        {differs && <p className="text-[11px] leading-4 text-warning">已选平台的词表不一样，下面显示的是「{PLATFORM_LABELS[targets[0]]}」的；点「同步词表到已选平台」可统一。</p>}
      </div>

      <SettingField label="搜索关键词" hint="每个岗位是一个独立词条：输入后回车添加，或点下方推荐词条；修改会同时写到已选的平台。">
        <KeywordRecommendations keywords={shown} onChange={apply} internKeywords={internKeywords} onInternChange={value => updateConfig('search.intern_keywords', value)} />
        {targets.includes('boss') && <KeywordPerformance />}
        <TagsInput value={shown} onChange={apply} placeholder="如：人力、产品运营" />
      </SettingField>
      <SettingField label="实习关键词（开发 / 产品）" hint="“补满”时单独用这些词搜实习岗位。">
        <TagsInput value={internKeywords} onChange={value => updateConfig('search.intern_keywords', value)} placeholder="如：AI开发实习生、产品实习生" />
      </SettingField>
    </div>
  )
}
