import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { DailyLoopProgress } from './DailyLoop'
import type { DailyLoopController, LoopRun } from '@/hooks/useDailyLoop'

const RUN: LoopRun = {
  started_at: '2026-10-24T07:30:02', ended_at: '2026-10-24T10:12:40', started_by: 'schedule', reason: 'limit', reason_label: '各平台今天都用完了额度',
  message: '各平台今天都用完了额度（BOSS 直聘「BOSS 单日页面访问总上限」），每日循环结束', needs_attention: true,
  suggestion: '额度明天恢复；也可以到设置里调高上限后现在重新启动', rounds: 3, totals: {}, open_incidents: 1,
}

function controller(overrides: Record<string, unknown> = {}) {
  const calls = { start: vi.fn(), overrideSchedule: vi.fn() }
  const loop = {
    state: { step: 'finished', message: '每日循环结束', round: 3, ended: RUN },
    running: false, busy: false, settings: {}, schedule: { enabled: true, time: '07:30', override: null }, history: [RUN, { ...RUN, ended_at: '2026-10-23T14:00:00', reason: 'end_time', reason_label: '已到结束时间', needs_attention: false }],
    start: calls.start, overrideSchedule: calls.overrideSchedule, ...overrides,
  } as unknown as DailyLoopController
  return { loop, calls }
}

afterEach(cleanup)

describe('DailyLoopLastRun', () => {
  it('says how the last run started and why it ended, and suggests what to do', () => {
    const { loop } = controller()
    render(<DailyLoopProgress loop={loop} />)
    const box = screen.getByLabelText('上次运行')
    expect(within(box).getByText(/07:30–10:12（定时启动）：各平台今天都用完了额度/)).toBeTruthy()
    expect(within(box).getByText('额度明天恢复；也可以到设置里调高上限后现在重新启动')).toBeTruthy()
    expect(within(box).getByText(/还有 1 条异常没处理/)).toBeTruthy()
  })

  it('restarts now, skips today or moves today\'s start without touching the daily setting', () => {
    const { loop, calls } = controller()
    render(<DailyLoopProgress loop={loop} />)
    fireEvent.click(screen.getByRole('button', { name: '现在重新启动' }))
    expect(calls.start).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: '今天不再自动启动' }))
    expect(calls.overrideSchedule).toHaveBeenCalledWith('skip')
    fireEvent.change(screen.getByRole('combobox', { name: '今天改到（时）' }), { target: { value: '13' } })
    fireEvent.click(screen.getByRole('button', { name: '改到这个时间启动' }))
    expect(calls.overrideSchedule).toHaveBeenLastCalledWith('time', '13:00')
  })

  it('shows a change made for today with a way back, and lists the earlier runs', () => {
    const { loop, calls } = controller({ schedule: { enabled: true, time: '07:30', override: { day: '2026-10-24', action: 'time', time: '13:00' } } })
    render(<DailyLoopProgress loop={loop} />)
    expect(screen.getByText(/今天已临时更改：改到 13:00 启动（明天照常）/)).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: '取消临时更改' }))
    expect(calls.overrideSchedule).toHaveBeenCalledWith('clear')
    expect(screen.getByText('最近 2 次运行')).toBeTruthy()
  })

  it('offers no change of the automatic start when the schedule is off, and shows nothing while the loop runs', () => {
    const { loop } = controller({ schedule: { enabled: false, time: '07:30', override: null } })
    const { rerender } = render(<DailyLoopProgress loop={loop} />)
    expect(screen.queryByRole('button', { name: '今天不再自动启动' })).toBeNull()
    expect(screen.getByRole('button', { name: '现在重新启动' })).toBeTruthy()
    rerender(<DailyLoopProgress loop={controller({ running: true }).loop} />)
    expect(screen.queryByLabelText('上次运行')).toBeNull()
  })
})
