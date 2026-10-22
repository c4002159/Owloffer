import { describe, expect, it } from 'vitest'
import { describeChanges, diffConfig, formatValue, groupByCategory } from './configDiff'

describe('config diff', () => {
  const before = { throttle: { daily_limit: 30, send_windows: ['09:00-16:00'] }, automation: { auto_send: false }, ai: { api_key: '', has_api_key: false }, browser: { proxy_port: 3456 } }
  const after = { throttle: { daily_limit: 60, send_windows: ['09:00-16:00', '19:00-21:00'] }, automation: { auto_send: true }, ai: { api_key: 'sk-secret', has_api_key: true }, browser: { proxy_port: 3456 } }

  it('lists what changed, whole arrays as one change, and ignores derived fields', () => {
    const paths = diffConfig(before, after).map(change => change.path)
    expect(paths.sort()).toEqual(['ai.api_key', 'automation.auto_send', 'throttle.daily_limit', 'throttle.send_windows'])
    expect(diffConfig(before, before)).toEqual([])
  })

  it('names changes, flags the risky ones and groups them by category', () => {
    const described = describeChanges(diffConfig(before, { ...after, browser: { proxy_port: 4000 } }))
    expect(described.find(change => change.path === 'throttle.daily_limit')).toMatchObject({ label: '每日发送上限', risky: true })
    expect(described.find(change => change.path === 'browser.proxy_port')).toMatchObject({ restart: true, label: '浏览器代理端口' })
    const groups = groupByCategory(described)
    expect(groups.map(group => group.label)).toEqual(['投递与自动化', '系统'])
  })

  it('shows values readably and never shows a secret', () => {
    expect(formatValue('automation.auto_send', true)).toBe('开')
    expect(formatValue('throttle.send_windows', ['09:00-16:00', '19:00-21:00'])).toBe('09:00-16:00、19:00-21:00')
    expect(formatValue('profile.fixed_greeting', '')).toBe('（空）')
    expect(formatValue('ai.api_key', 'sk-secret')).not.toContain('sk-secret')
  })
})
