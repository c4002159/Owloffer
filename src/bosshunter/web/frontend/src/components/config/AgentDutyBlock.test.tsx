import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { AgentDutyBlock } from './AgentDutyBlock'

const STATE = {
  enabled: false, interval_minutes: 60, prompt: '默认提示词', prompt_is_default: true, default_prompt: '默认提示词', cli_path: '',
  cli_found: true, cli: 'C:/claude.exe', runs: [{ at: '2026-10-26T12:00:00', status: 'login_required', summary: 'Claude 命令行还没有登录' }],
  next_run: '', running: false,
}

function mockApi(state: unknown = STATE, runAnswer: unknown = { status: 'ok' }) {
  const calls: { url: string; body?: string }[] = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, body: init?.body as string | undefined })
    return { ok: true, json: async () => (url === '/api/agent-duty/run' ? runAnswer : state) }
  }))
  return calls
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('AgentDutyBlock', () => {
  it('shows the switch, the command line that was found and how the last run went', async () => {
    mockApi()
    render(<AgentDutyBlock />)
    expect(await screen.findByText(/已找到 Claude 命令行/)).toBeTruthy()
    expect(screen.getByText(/未登录：Claude 命令行还没有登录/)).toBeTruthy()
  })

  it('turns the duty on and checks the command line on request', async () => {
    const calls = mockApi()
    render(<AgentDutyBlock />)
    fireEvent.click(await screen.findByRole('checkbox'))
    await waitFor(() => expect(calls.some(call => call.url === '/api/agent-duty' && call.body && JSON.parse(call.body).enabled === true)).toBe(true))
    fireEvent.click(screen.getByRole('button', { name: '检测命令行是否可用' }))
    await waitFor(() => expect(calls.some(call => call.url === '/api/agent-duty/run' && call.body && JSON.parse(call.body).check === true)).toBe(true))
    expect(await screen.findByText(/命令行可用，已登录/)).toBeTruthy()
  })

  it('says so when no command line was found, and shows nothing for an answer of another shape', async () => {
    mockApi({ ...STATE, cli_found: false, cli: '' })
    render(<AgentDutyBlock />)
    expect(await screen.findByText(/没有找到 Claude 命令行/)).toBeTruthy()
    cleanup()
    mockApi({ something: 'else' })
    const { container } = render(<AgentDutyBlock />)
    await waitFor(() => expect(container.textContent).toBe(''))
  })
})
