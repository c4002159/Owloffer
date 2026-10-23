import { describe, expect, it } from 'vitest'
import { describeLimit, settingHref } from './limitReasons'
import { entryForPath } from './settingsIndex'

describe('limit reasons', () => {
  it('names the limit exactly as the setting is named and links to it', () => {
    const info = describeLimit('daily_platform_page_limit')
    expect(info?.label).toBe(entryForPath('safety.daily_platform_page_limit')?.label)
    expect(info?.text).toBe('已达到「BOSS 单日页面访问总上限」')
    expect(info?.href).toBe(`/config?section=safety&focus=${encodeURIComponent('BOSS 单日页面访问总上限')}`)
  })

  it('finds the code inside a longer message and picks the platform own search budget', () => {
    expect(describeLimit('BOSS 采集已达安全上限：daily_detail_page_limit')?.label).toBe('BOSS 单日详情页尝试上限')
    expect(describeLimit('daily_search_page_limit', 'liepin')?.label).toBe('猎聘 单日搜索页上限')
    expect(describeLimit('daily_search_page_limit', 'boss')?.label).toBe('BOSS 单日搜索页上限')
  })

  it('sends the delivery quota to its own group and ignores other reasons', () => {
    expect(describeLimit('daily_limit')?.href).toContain('section=delivery')
    expect(settingHref('throttle.daily_limit')).toContain('focus=')
    expect(describeLimit('outside_window')).toBeNull()
    expect(describeLimit('stopped')).toBeNull()
  })
})
