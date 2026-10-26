import { useCallback, useEffect, useState } from 'react'
import { AgentDutyBlock } from './AgentDutyBlock'

interface AgentRequest {
  id: string
  time: string
  label: string
  status: 'pending' | 'approved' | 'denied'
  source: string
}

interface AgentEvent {
  id: number
  time: string
  kind: string
  level: string
  message: string
}

interface AgentAccessState {
  has_token: boolean
  token_created: string
  enabled: boolean
  mode: 'auto' | 'ask' | 'readonly'
  saved_mode: string
  objections: number
  degrade_after: number
  window_days: number
  requests: AgentRequest[]
  recent_events: AgentEvent[]
  connection: { python: string; base_url: string; project_dir: string }
}

const MODE_LABELS: Record<string, { name: string; hint: string }> = {
  auto: { name: '自动', hint: '智能体可以启动或停止每日循环、处理异常、批准并投递岗位。' },
  ask: { name: '询问', hint: '智能体想操作时先发一条请求，你点同意后才执行。' },
  readonly: { name: '只读', hint: '智能体只能查看状态、异常和消息，不能做任何操作。' },
}

const SCHEDULED_PROMPT = `你是本机求职程序 BossHunter 的值班员。通过 MCP 工具 bosshunter_status 查看状态，bosshunter_events 读取新消息（把上次返回的 latest 作为 since）。
每次值班：1）如果每日循环没在运行且在工作时间内，用 bosshunter_daily_loop_start 启动；2）看 open_incidents，能修的修（改代码、补测试、提交，不要 push），修好后用 bosshunter_resolve_incident 标记；3）有需要用户决定的事，用通知告诉用户。
不要绕过验证码、风控或登录；权限被拒绝或只读时，只汇报，不要重复尝试。`

function copy(text: string, done: (message: string) => void) {
  navigator.clipboard.writeText(text).then(() => done('已复制'), () => done('复制失败，请手动选择'))
}

export function AgentAccessPanel() {
  const [state, setState] = useState<AgentAccessState | null>(null)
  const [token, setToken] = useState('')
  const [notice, setNotice] = useState('')

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/agent-access')
      if (res.ok) setState(await res.json())
    } catch {
      // try again on the next poll
    }
  }, [])

  useEffect(() => {
    void load()
    const timer = window.setInterval(() => void load(), 10000)
    return () => window.clearInterval(timer)
  }, [load])

  const post = async (path: string, body: Record<string, unknown> = {}) => {
    const res = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    const data = await res.json().catch(() => ({}))
    if (!res.ok) throw new Error(data.error || '操作失败')
    return data
  }

  const run = async (action: () => Promise<void>) => {
    try {
      await action()
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '操作失败')
    }
  }

  if (!state) return <p className="text-xs text-muted">读取中…</p>
  const python = state.connection.python
  const tokenText = token || '<你的令牌>'
  const claudeCommand = `claude mcp add bosshunter -e BOSSHUNTER_TOKEN=${tokenText} -e BOSSHUNTER_URL=${state.connection.base_url} -- "${python}" -m bosshunter.main mcp`
  const codexConfig = `[mcp_servers.bosshunter]\ncommand = "${python.replace(/\\/g, '\\\\')}"\nargs = ["-m", "bosshunter.main", "mcp"]\nenv = { BOSSHUNTER_TOKEN = "${tokenText}", BOSSHUNTER_URL = "${state.connection.base_url}" }`
  const pending = state.requests.filter(item => item.status === 'pending')

  return (
    <div className="space-y-4 text-xs">
      <p className="rounded-xl border border-card-border bg-surface px-3 py-2 leading-5 text-muted">
        让 Claude 和 Codex 通过 MCP 查看进度、读取新消息，并在你允许的范围内操作这个程序（启动或停止每日循环、处理异常、批准并投递岗位）。
        你远程操作 Claude 或 Codex 时，它们在这台电脑上调用本程序，不需要把程序开放到外网。
      </p>

      <AgentDutyBlock />

      <div className="space-y-2 rounded-xl border border-card-border bg-card p-3">
        <div className="font-black text-foreground">1. 访问令牌</div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="rounded-lg bg-primary px-3 py-1.5 font-bold text-primary-foreground" onClick={() => void run(async () => { setToken((await post('/api/agent-access/token')).token); await load() })}>
            {state.has_token ? '重新生成令牌' : '生成令牌'}
          </button>
          {state.has_token && <button type="button" className="rounded-lg border border-card-border px-3 py-1.5 font-bold" onClick={() => void run(async () => { await post('/api/agent-access/token/revoke'); setToken(''); await load() })}>撤销令牌</button>}
          <span className="text-muted">{state.has_token ? `已生成（${state.token_created.replace('T', ' ')}）` : '还没有令牌，智能体连不上'}</span>
        </div>
        {token && (
          <div className="rounded-lg bg-warning-soft p-2">
            <div className="font-bold text-warning">令牌只显示这一次，请马上复制：</div>
            <code className="break-all">{token}</code>
            <button type="button" className="ml-2 font-bold text-primary" onClick={() => copy(token, setNotice)}>复制</button>
          </div>
        )}
      </div>

      <div className="space-y-2 rounded-xl border border-card-border bg-card p-3">
        <div className="font-black text-foreground">2. 智能体权限</div>
        <div className="flex flex-wrap items-center gap-2">
          {(['auto', 'ask', 'readonly'] as const).map(mode => (
            <button key={mode} type="button" onClick={() => void run(async () => { await post('/api/agent-access/mode', { mode }); await load() })}
              className={`rounded-full border px-3 py-1 font-bold ${state.mode === mode ? 'border-primary bg-secondary text-primary' : 'border-card-border bg-card text-muted'}`}>
              {MODE_LABELS[mode].name}
            </button>
          ))}
          <label className="ml-2 flex items-center gap-1 font-bold">
            <input type="checkbox" checked={state.enabled} className="accent-primary" onChange={event => void run(async () => { await post('/api/agent-access/mode', { enabled: event.target.checked }); await load() })} />
            允许智能体操作
          </label>
        </div>
        <p className="text-muted">当前：{MODE_LABELS[state.mode]?.name}。{MODE_LABELS[state.mode]?.hint}</p>
        <p className="text-muted">
          默认是「自动」。如果你在 {state.window_days} 天内 {state.degrade_after} 次收回权限（关掉开关或拒绝请求），会自动降为「询问」，再来 {state.degrade_after} 次降为「只读」。你随时可以手动改回。
          {state.objections > 0 ? ` 目前已记录 ${state.objections} 次。` : ''}
        </p>
      </div>

      {pending.length > 0 && (
        <div className="space-y-2 rounded-xl border border-warning/40 bg-warning-soft p-3">
          <div className="font-black text-warning">等你确认的请求 {pending.length} 条</div>
          {pending.map(item => (
            <div key={item.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-card p-2">
              <span className="font-bold">{item.time.slice(11, 16)} · {item.label}</span>
              <span className="flex gap-2">
                <button type="button" className="rounded-lg bg-primary px-3 py-1 font-bold text-primary-foreground" onClick={() => void run(async () => { await post(`/api/agent-access/requests/${item.id}/approve`); await load(); setNotice('已同意并执行') })}>同意</button>
                <button type="button" className="rounded-lg border border-card-border px-3 py-1 font-bold" onClick={() => void run(async () => { await post(`/api/agent-access/requests/${item.id}/deny`); await load() })}>拒绝</button>
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-2 rounded-xl border border-card-border bg-card p-3">
        <div className="font-black text-foreground">3. 连接 Claude</div>
        <p className="text-muted">在终端执行下面这条命令（令牌换成上面生成的；重新生成后要重新执行）：</p>
        <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded bg-surface p-2">{claudeCommand}</pre>
        <button type="button" className="font-bold text-primary" onClick={() => copy(claudeCommand, setNotice)}>复制命令</button>
      </div>

      <div className="space-y-2 rounded-xl border border-card-border bg-card p-3">
        <div className="font-black text-foreground">4. 连接 Codex</div>
        <p className="text-muted">把下面这段加到 Codex 的配置文件 <code>~/.codex/config.toml</code>：</p>
        <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded bg-surface p-2">{codexConfig}</pre>
        <button type="button" className="font-bold text-primary" onClick={() => copy(codexConfig, setNotice)}>复制配置</button>
      </div>

      <div className="space-y-2 rounded-xl border border-card-border bg-card p-3">
        <div className="font-black text-foreground">5. 备用：自己建定时任务</div>
        <p className="text-muted">用上面的「智能体值班」就不需要这一步。如果你更想在 Claude 的定时任务，或 Codex 的自动化里自己建，把下面这段作为任务内容，选你要的间隔（比如每小时）。它们通过上面的 MCP 工具读消息、盯异常。</p>
        <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-surface p-2">{SCHEDULED_PROMPT}</pre>
        <button type="button" className="font-bold text-primary" onClick={() => copy(SCHEDULED_PROMPT, setNotice)}>复制任务内容</button>
      </div>

      {state.recent_events.length > 0 && (
        <div className="space-y-1 rounded-xl border border-card-border bg-card p-3">
          <div className="font-black text-foreground">最近的消息（智能体读取的就是这些）</div>
          {state.recent_events.slice().reverse().map(item => (
            <div key={item.id} className={item.level === 'warning' ? 'text-danger' : 'text-muted'}>{item.time.slice(5, 16).replace('T', ' ')} · {item.message}</div>
          ))}
        </div>
      )}
      {notice && <div className="rounded-lg bg-secondary px-3 py-2 font-bold text-primary">{notice}</div>}
    </div>
  )
}
