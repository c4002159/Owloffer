import { describe, expect, it } from 'vitest'
import { diffConfig } from './configDiff'

describe('diffConfig', () => {
  it('lists changed leaf values and flags risky/restart ones first', () => {
    const before = { automation: { auto_send: false }, browser: { proxy_port: 3456 }, profile: { max_job_age_days: 0 } }
    const after = { automation: { auto_send: true }, browser: { proxy_port: 3457 }, profile: { max_job_age_days: 0 } }
    const out = diffConfig(before, after)
    expect(out.map(c => c.path)).toEqual(['automation.auto_send', 'browser.proxy_port'])
    expect(out[0].risky).toBeTruthy()
    expect(out[0].before).toBe('关')
    expect(out[0].after).toBe('开')
    expect(out[1].restart).toBe(true)
  })

  it('masks secrets', () => {
    const out = diffConfig({ ai: { api_key: 'old' } }, { ai: { api_key: 'new-secret' } })
    expect(out[0].after).not.toContain('new-secret')
    expect(out[0].before).not.toContain('old')
  })

  it('returns nothing when equal', () => {
    expect(diffConfig({ a: { b: [1, 2] } }, { a: { b: [1, 2] } })).toEqual([])
  })
})
