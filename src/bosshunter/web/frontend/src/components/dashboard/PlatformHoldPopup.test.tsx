import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { PlatformHoldPopup } from './PlatformHoldPopup'

function todayText(offsetDays = 0) {
  const date = new Date(Date.now() + offsetDays * 86400000)
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`
}

function mockLoop(held: Record<string, unknown>) {
  const calls: { url: string; method?: string }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method })
    return { ok: true, json: async () => ({ held }) }
  }))
  return calls
}

afterEach(() => {
  cleanup()
  window.sessionStorage.clear()
  vi.unstubAllGlobals()
})

describe('PlatformHoldPopup', () => {
  it('asks about a platform held by a captcha and lets the user bring it back', async () => {
    const calls = mockLoop({ boss: { code: 'captcha', message: 'BOSS 采集检测到验证码', since: '', day: todayText(), skipped_day: '' } })
    render(<PlatformHoldPopup />)
    expect(await screen.findByText(/遇到了验证码/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '已验证，加入循环' }))
    await waitFor(() => expect(calls.some(call => call.url === '/api/daily-loop/platform/boss/release' && call.method === 'POST')).toBe(true))
  })

  it('asks again on a new day, and stays quiet for a platform skipped today', async () => {
    mockLoop({ boss: { code: 'captcha', message: '', since: '', day: todayText(-1), skipped_day: '' } })
    render(<PlatformHoldPopup />)
    expect(await screen.findByText(/新的一天/)).toBeTruthy()
    cleanup()
    mockLoop({ boss: { code: 'captcha', message: '', since: '', day: todayText(-1), skipped_day: todayText() } })
    render(<PlatformHoldPopup />)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('"later" hides it in this tab without telling the server', async () => {
    const calls = mockLoop({ boss: { code: 'captcha', message: '', since: '', day: todayText(), skipped_day: '' } })
    render(<PlatformHoldPopup />)
    fireEvent.click(await screen.findByRole('button', { name: '稍后再问（30 分钟）' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(calls.every(call => call.method === undefined)).toBe(true)
  })

  it('shows nothing when no platform is held', async () => {
    mockLoop({})
    render(<PlatformHoldPopup />)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })
})
