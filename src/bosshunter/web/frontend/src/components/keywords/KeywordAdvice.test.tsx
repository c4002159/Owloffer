import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { KeywordAdviceCard, KeywordAdvicePopup } from './KeywordAdvice'

const ADVICE = {
  level3: true,
  reasons: [{ code: 'core_low_pass', message: '核心关键词最近两天通过率只有 5%（40 个岗位），低于 10%' }],
  suggestions: [
    { id: 'pause:旧词', kind: 'pause', keyword: '旧词', reason: '近 14 天 20 个岗位只有 0% 通过' },
    { id: 'add:Agent开发工程师', kind: 'add', keyword: 'Agent开发工程师', reason: '4 个通过评分的岗位都叫这个名字' },
  ],
  popup: true,
  popup_enabled: true,
  last_applied: null,
}

function mockApi(advice: unknown) {
  const calls: { url: string; body?: string }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body as string | undefined })
    return { ok: true, json: async () => (url === '/api/keywords/advice' ? advice : { success: true }) }
  }))
  return calls
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('KeywordAdvicePopup', () => {
  it('shows nothing when the advice does not ask for a pop-up', async () => {
    mockApi({ ...ADVICE, popup: false })
    render(<KeywordAdvicePopup />)
    await waitFor(() => expect(fetch).toHaveBeenCalled())
    expect(screen.queryByRole('dialog')).toBeNull()
  })

  it('lists the reasons and suggestions and applies the ticked ones in one click', async () => {
    const calls = mockApi(ADVICE)
    render(<KeywordAdvicePopup />)
    expect(await screen.findByRole('dialog', { name: '关键词需要调整' })).toBeTruthy()
    expect(screen.getByText(/通过率只有 5%/)).toBeTruthy()
    fireEvent.click(screen.getByRole('checkbox', { name: '新增关键词：Agent开发工程师' }))      // untick one
    fireEvent.click(screen.getByRole('button', { name: '一键应用所选（1）' }))
    await waitFor(() => expect(calls.some(call => call.url === '/api/keywords/advice/apply')).toBe(true))
    expect(JSON.parse(calls.find(call => call.url === '/api/keywords/advice/apply')!.body!)).toEqual({ ids: ['pause:旧词'] })
  })

  it('can be snoozed or switched off', async () => {
    const calls = mockApi(ADVICE)
    render(<KeywordAdvicePopup />)
    fireEvent.click(await screen.findByRole('button', { name: '24 小时后再提醒' }))
    await waitFor(() => expect(calls.some(call => call.url === '/api/keywords/advice/snooze')).toBe(true))
    fireEvent.click(screen.getByText('以后不弹窗，只在设置页和消息中心提示'))
    await waitFor(() => expect(JSON.parse(calls.find(call => call.url === '/api/keywords/advice/popup')!.body!)).toEqual({ enabled: false }))
  })
})

describe('KeywordAdviceCard', () => {
  it('asks the model to widen the search even when there is no advice, and shows what comes back', async () => {
    const quiet = { ...ADVICE, level3: false, reasons: [], suggestions: [], popup: false, last_applied: null }
    const calls = mockApi(quiet)
    render(<KeywordAdviceCard />)
    expect(await screen.findByText(/暂时没有建议/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '让模型扩展方向' }))
    await waitFor(() => expect(calls.some(call => call.url === '/api/keywords/expand')).toBe(true))
  })

  it('lists the words the model or the mining proposed with where they come from', async () => {
    mockApi({ ...ADVICE, popup: false, suggestions: [{ id: 'add:AIGC', kind: 'add', keyword: 'AIGC', reason: '模型建议（AIGC 应用）：同类工作的另一种叫法' }] })
    render(<KeywordAdviceCard />)
    expect(await screen.findByText('AIGC')).toBeTruthy()
    expect(screen.getByText(/模型建议（AIGC 应用）/)).toBeTruthy()
  })
})

describe('closing the pop-up for now', () => {
  it('hides the pop-up in this browser for a while without telling the server anything', async () => {
    window.sessionStorage.clear()
    const calls = mockApi(ADVICE)
    render(<KeywordAdvicePopup />)
    fireEvent.click(await screen.findByRole('button', { name: '暂时关闭（2 小时）' }))
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(calls.every(call => call.url === '/api/keywords/advice')).toBe(true)          // only the reading of the advice, no setting changed
    cleanup()
    render(<KeywordAdvicePopup />)                                                       // still closed after the page is opened again
    await waitFor(() => expect(calls.length).toBeGreaterThan(1))
    expect(screen.queryByRole('dialog')).toBeNull()
    window.sessionStorage.clear()
  })
})
