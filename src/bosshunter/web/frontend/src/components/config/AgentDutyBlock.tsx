import { useCallback, useEffect, useState } from 'react'

interface DutyRun {
  at: string
  status: 'ok' | 'login_required' | 'failed' | 'no_cli' | 'busy' | string
  summary: string
  seconds?: number
}

interface DutyState {
  enabled: boolean
  interval_minutes: number
  prompt: string
  prompt_is_default: boolean
  default_prompt: string
  cli_path: string
  cli_found: boolean
  cli: string
  runs: DutyRun[]
  next_run: string
  running: boolean
}

const INTERVALS = [15, 30, 60, 120, 240]
const STATUS_LABEL: Record<string, string> = { ok: '正常', login_required: '未登录', failed: '失败', no_cli: '没找到命令行', busy: '上一次还没结束' }

/** The program calls the user's Claude on a schedule while it runs (see agent_duty.py): a switch instead of a scheduled task made by hand. */
export function AgentDutyBlock() {
  const [state, setState] = useState<DutyState | null>(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const [prompt, setPrompt] = useState('')
  const [editing, setEditing] = useState(false)

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/agent-duty')
      if (res.ok) {
        const data: DutyState = await res.json()
        if (data && typeof data === 'object' && Array.isArray(data.runs)) setState(data)
      }
    } catch {
      // asked again on the next poll
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 15000)
    return () => window.clearInterval(timer)
  }, [load])

  const save = async (changes: Record<string, unknown>) => {
    const res = await fetch('/api/agent-duty', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes) })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) setNotice(data.error || '保存失败')
    await load()
  }

  const call = async (body: Record<string, unknown>) => {
    setBusy(true)
    setNotice('')
    try {
      const res = await fetch('/api/agent-duty/run', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      const data = await res.json().catch(() => ({}))
      if (body.check) setNotice(data.status === 'ok' ? 'Claude 命令行可用，已登录' : data.summary || '检测失败')
      else setNotice('已开始一次值班，几分钟后在下面看报告')
    } finally {
      setBusy(false)
      await load()
    }
  }

  if (!state) return null
  return (
    <div className="space-y-2 rounded-xl border border-primary/40 bg-card p-3" aria-label="智能体值班">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-black text-foreground">智能体值班（推荐）</span>
        <label className="flex items-center gap-1.5 font-bold">
          <input type="checkbox" className="accent-primary" checked={state.enabled} onChange={event => void save({ enabled: event.target.checked })} />
          开启
        </label>
        <label className="flex items-center gap-1.5 text-muted">
          每
          <select className="rounded border border-card-border bg-surface px-1 py-0.5" value={state.interval_minutes} onChange={event => void save({ interval_minutes: Number(event.target.value) })}>
            {INTERVALS.map(value => <option key={value} value={value}>{value >= 60 ? `${value / 60} 小时` : `${value} 分钟`}</option>)}
            {!INTERVALS.includes(state.interval_minutes) && <option value={state.interval_minutes}>{state.interval_minutes} 分钟</option>}
          </select>
          值班一次
        </label>
      </div>
      <p className="leading-5 text-muted">
        程序在后台运行、这个开关开着时，会自动叫起你电脑上的 Claude 命令行（<code>claude -p</code>），让它读状态和消息、循环停了就启动、处理能处理的异常，再把报告写进消息中心。
        不需要自己建定时任务。它只能用本程序的工具（不能改代码、不能用终端和网页），能做什么仍由上面的「智能体权限」决定。
        每次值班都会用掉你自己的 Claude 额度或 API 用量，所以默认关闭、一小时一次。
      </p>
      <p className={state.cli_found ? 'text-muted' : 'font-bold text-danger'}>
        {state.cli_found ? `已找到 Claude 命令行：${state.cli}` : '没有找到 Claude 命令行。先安装 Claude Code，或在下面填它的路径。'}
        {' '}它需要登录过一次：在终端运行 <code>claude</code> 再输入 <code>/login</code>（或设置 ANTHROPIC_API_KEY）。
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <button type="button" className="rounded-lg border border-card-border px-3 py-1.5 font-bold" disabled={busy} onClick={() => void call({ check: true })}>检测命令行是否可用</button>
        <button type="button" className="rounded-lg bg-primary px-3 py-1.5 font-bold text-primary-foreground" disabled={busy || state.running} onClick={() => void call({})}>{state.running ? '值班中…' : '现在值班一次'}</button>
        {state.enabled && state.next_run && <span className="text-muted">下次：{state.next_run.slice(5, 16).replace('T', ' ')}</span>}
      </div>
      {notice && <div className="rounded-lg bg-secondary px-3 py-2 font-bold text-primary">{notice}</div>}
      {state.runs.length > 0 && (
        <ul className="space-y-1">
          {state.runs.slice(0, 4).map(run => (
            <li key={run.at} className={run.status === 'ok' ? 'text-muted' : 'text-danger'}>
              {run.at.slice(5, 16).replace('T', ' ')} · {STATUS_LABEL[run.status] ?? run.status}：{run.summary.slice(0, 200)}
            </li>
          ))}
        </ul>
      )}
      <details onToggle={event => { if ((event.target as HTMLDetailsElement).open && !editing) { setPrompt(state.prompt); setEditing(true) } }}>
        <summary className="cursor-pointer font-bold text-primary">高级：命令行路径、值班提示词</summary>
        <div className="mt-2 space-y-2">
          <label className="block text-muted">Claude 命令行路径（留空自动查找）
            <input className="mt-1 w-full rounded border border-card-border bg-surface px-2 py-1" defaultValue={state.cli_path} onBlur={event => void save({ cli_path: event.target.value })} />
          </label>
          <label className="block text-muted">值班提示词（它每次值班要做的事）
            <textarea className="mt-1 h-40 w-full rounded border border-card-border bg-surface px-2 py-1" value={prompt} onChange={event => setPrompt(event.target.value)} />
          </label>
          <div className="flex gap-2">
            <button type="button" className="rounded-lg bg-primary px-3 py-1.5 font-bold text-primary-foreground" onClick={() => void save({ prompt })}>保存提示词</button>
            <button type="button" className="rounded-lg border border-card-border px-3 py-1.5 font-bold" onClick={() => { setPrompt(state.default_prompt); void save({ prompt: state.default_prompt }) }}>恢复默认</button>
          </div>
        </div>
      </details>
    </div>
  )
}
