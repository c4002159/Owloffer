import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { PLATFORM_LABELS } from '@/lib/platforms'
import { SettingField } from './fields'

type PlatformId = 'boss' | 'zhilian' | 'zhilian_campus' | '51job' | 'liepin'
const PLATFORMS: PlatformId[] = ['boss', 'zhilian', 'zhilian_campus', '51job', 'liepin']
const EXPERIENCE = ['经验不限', '应届生', '1年以内', '1-3年', '3-5年', '5-10年', '10年以上', '在校生']
const DEGREE = ['学历不限', '大专', '本科', '硕士', '博士', '高中', '中专/中技', '初中及以下']

interface CommonSearchCardProps {
  config: Record<string, any>
  platformSearch: (platform: PlatformId) => Record<string, any>
  updatePlatformSearch: (platform: PlatformId, key: string, value: unknown) => void
  updatePlatformCities: (platform: PlatformId, cities: string[]) => void
  updateConfig: (path: string, value: unknown) => void
}

const list = (value: unknown): string[] => (Array.isArray(value) ? value.map(String) : [])
const same = (a: string[], b: string[]) => [...a].sort().join('|') === [...b].sort().join('|')

/** Settings every platform shares. "Apply" copies them into each platform, so what you see there is what runs. */
export function CommonSearchCard({ config, platformSearch, updatePlatformSearch, updatePlatformCities, updateConfig }: CommonSearchCardProps) {
  const common = (config.search_common && typeof config.search_common === 'object' ? config.search_common : {}) as Record<string, any>
  const cities = list(common.cities)
  const maxPages = Number(common.max_pages) || 0
  const sort = String(common.sort || '')
  const experience = list(common.experience_limit)
  const degree = list(common.degree_limit)

  const limitsOf = (platform: PlatformId, key: 'experience' | 'degree') => {
    const filters = (platformSearch(platform).filters ?? {}) as Record<string, any>
    return list(filters[platform === 'boss' ? key : `${key}_limit`])
  }
  const differs = (platform: PlatformId): string[] => {
    const search = platformSearch(platform)
    const out: string[] = []
    if (cities.length && !same(list(search.cities), cities)) out.push('城市')
    if (maxPages && Number(search.max_pages) !== maxPages) out.push('页数')
    if (sort && !(platform === '51job' && sort === 'newest') && String(search.sort || 'default') !== sort) out.push('排序')
    if (experience.length && !same(limitsOf(platform, 'experience'), experience)) out.push('经验')
    if (degree.length && !same(limitsOf(platform, 'degree'), degree)) out.push('学历')
    return out
  }

  const applyTo = (platforms: PlatformId[]) => {
    for (const platform of platforms) {
      if (cities.length) updatePlatformCities(platform, cities)
      if (maxPages) updatePlatformSearch(platform, 'max_pages', maxPages)
      if (sort && !(platform === '51job' && sort === 'newest')) updatePlatformSearch(platform, 'sort', sort)
      const prefix = platform === 'boss' ? '' : '_limit'
      if (experience.length) updatePlatformSearch(platform, `filters.experience${prefix}`, experience)
      if (degree.length) updatePlatformSearch(platform, `filters.degree${prefix}`, degree)
    }
  }
  const toggle = (key: 'experience_limit' | 'degree_limit', current: string[], option: string) =>
    updateConfig(`search_common.${key}`, current.includes(option) ? current.filter(item => item !== option) : [...current, option])
  const chips = (key: 'experience_limit' | 'degree_limit', options: string[], selected: string[]) => (
    <div className="flex flex-wrap gap-1.5">
      {options.map(option => (
        <button key={option} type="button" onClick={() => toggle(key, selected, option)}
          className={`rounded-full border px-2.5 py-1 text-xs font-bold ${selected.includes(option) ? 'border-primary bg-secondary text-primary' : 'border-card-border bg-card text-muted hover:border-primary/40'}`}>
          {option}
        </button>
      ))}
    </div>
  )

  return (
    <div className="space-y-4">
      <p className="text-xs leading-5 text-muted">在这里填一次，点「应用到所有平台」就写进每个平台的设置里；之后哪个平台单独改过，下面会标出和通用设置哪里不一样。留空的项不会被应用。</p>
      <div className="grid gap-4 md:grid-cols-3">
        <SettingField label="搜索城市" hint="逗号分隔">
          <Input aria-label="通用搜索城市" value={cities.join(', ')} onChange={event => updateConfig('search_common.cities', event.target.value.split(/[,，]/).map(item => item.trim()).filter(Boolean))} placeholder="如：上海, 杭州" />
        </SettingField>
        <SettingField label="每个关键词最大页数">
          <Input aria-label="通用最大页数" type="number" min={1} max={50} value={maxPages || ''} onChange={event => updateConfig('search_common.max_pages', Number(event.target.value))} placeholder="如：20" />
        </SettingField>
        <SettingField label="排序">
          <Select aria-label="通用排序" value={sort} onChange={event => updateConfig('search_common.sort', event.target.value)}>
            <option value="">不改</option>
            <option value="default">默认</option>
            <option value="newest">最新（前程无忧不支持）</option>
          </Select>
        </SettingField>
      </div>
      <SettingField label="工作经验限制" hint="采集时按岗位标签和 JD 筛掉不符的；不选 = 不限制">{chips('experience_limit', EXPERIENCE, experience)}</SettingField>
      <SettingField label="学历要求限制" hint="不选 = 不限制">{chips('degree_limit', DEGREE, degree)}</SettingField>

      <div className="space-y-2 rounded-xl border border-card-border bg-surface p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs font-black text-foreground">各平台和通用设置的差异</span>
          <Button size="sm" onClick={() => applyTo(PLATFORMS)}>应用到所有平台</Button>
        </div>
        {PLATFORMS.map(platform => {
          const diff = differs(platform)
          return (
            <div key={platform} className="flex flex-wrap items-center justify-between gap-2 text-xs">
              <span className="font-bold text-foreground">{PLATFORM_LABELS[platform]}</span>
              <span className="flex items-center gap-2">
                <span className={diff.length ? 'text-warning' : 'text-success'}>{diff.length ? `已单独调整：${diff.join('、')}` : '与通用一致'}</span>
                <button type="button" disabled={!diff.length} onClick={() => applyTo([platform])} className="font-bold text-primary hover:underline disabled:cursor-not-allowed disabled:text-muted disabled:no-underline">沿用通用</button>
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
