import { describe, expect, it } from 'vitest'
import { entryForPath, searchSettings, SETTINGS_INDEX } from './settingsIndex'

describe('settings index', () => {
  it('finds a setting by its label, an alias or part of its name, best match first', () => {
    expect(searchSettings('发送窗口')[0].path).toBe('throttle.send_windows')
    expect(searchSettings('代理').map(entry => entry.path)).toContain('browser.proxy_host')
    expect(searchSettings('冷却').map(entry => entry.path)).toContain('automation.company_cooldown_days')
    expect(searchSettings('9222')[0].path).toBe('browser.chrome_ports')
    expect(searchSettings('不存在的词')).toEqual([])
    expect(searchSettings('   ')).toEqual([])
  })

  it('names a changed config path by the longest matching entry', () => {
    expect(entryForPath('throttle.daily_limit')?.label).toBe('每日发送上限')
    expect(entryForPath('platforms.liepin.search.filters.experience_limit')?.label).toBe('启用平台 / 各平台搜索设置')
    expect(entryForPath('profile.resumes.0.direction')?.label).toBe('方向简历')
    expect(entryForPath('nothing.here')).toBeUndefined()
  })

  it('marks what needs a restart or a warning', () => {
    expect(SETTINGS_INDEX.find(entry => entry.path === 'browser.proxy_port')?.restart).toBe(true)
    expect(SETTINGS_INDEX.find(entry => entry.path === 'automation.auto_send')?.risky).toBe(true)
  })
})
