import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import type { HistoryItem, TaskSummary } from '@/hooks/useDashboard'

interface RecentActivityProps {
  data: HistoryItem[]
  tasks?: TaskSummary[]
}

const TASK_STATUS: Record<string, { label: string; className: string }> = {
  running: { label: '进行中', className: 'bg-primary text-primary-foreground' },
  stopping: { label: '停止中', className: 'bg-warning-soft text-warning-strong' },
  queued: { label: '等待中', className: 'bg-secondary text-primary' },
  completed: { label: '已完成', className: 'bg-success-soft text-success' },
  stopped: { label: '已停止', className: 'bg-card text-muted border border-card-border' },
  failed: { label: '失败', className: 'bg-danger-soft text-danger' },
}

// 全流程由几段任务组成；按任务最近一条日志判断走到了哪一段。
const FULL_FLOW_STAGES: Array<{ label: string; keys: string[] }> = [
  { label: '采集', keys: ['采集'] },
  { label: '评分', keys: ['评分', 'AI'] },
  { label: '确认', keys: ['确认', '等待前端'] },
  { label: '投递', keys: ['发送', '投递', '招呼语'] },
  { label: '监测', keys: ['监测'] },
]

function stageIndex(task: TaskSummary) {
  const text = task.last_log || ''
  let found = -1
  FULL_FLOW_STAGES.forEach((stage, index) => { if (stage.keys.some(key => text.includes(key))) found = index })
  return found
}

function TaskList({ tasks }: { tasks: TaskSummary[] }) {
  const order = (task: TaskSummary) => (task.status === 'running' || task.status === 'stopping' ? 0 : task.status === 'queued' ? 1 : 2)
  const shown = [...tasks].sort((a, b) => order(a) - order(b)).slice(0, 6)
  if (!shown.length) return null
  const ahead = shown.filter(task => task.status === 'queued')
  return (
    <div className="mb-3 space-y-1.5 border-b border-card-border pb-3">
      <div className="text-[11px] font-semibold text-muted">任务</div>
      {shown.map(task => {
        const state = TASK_STATUS[task.status] ?? { label: task.status, className: 'bg-card text-muted' }
        const position = task.status === 'queued' ? ahead.indexOf(task) + 1 : 0
        const current = task.mode === 'full' && (task.status === 'running' || task.status === 'stopping') ? stageIndex(task) : -1
        return (
          <div key={task.id} className="space-y-1">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
              <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${state.className}`}>{task.status === 'queued' ? `${state.label} ${position} 号` : state.label}</span>
              <span className="text-xs font-bold text-foreground">{task.label}</span>
              <span className="min-w-0 flex-1 truncate text-[11px] text-muted">{task.error || task.stop_reason || task.last_log}</span>
              <time className="shrink-0 text-[11px] text-muted" dateTime={task.updated_at}>{task.updated_at.slice(5, 16).replace('T', ' ')}</time>
            </div>
            {task.mode === 'full' && (task.status === 'running' || task.status === 'stopping') && (
              <div className="flex flex-wrap items-center gap-1 pl-1 text-[10px] font-bold">
                {FULL_FLOW_STAGES.map((stage, index) => (
                  <span key={stage.label} className={`rounded px-1.5 py-0.5 ${index < current ? 'bg-success-soft text-success' : index === current ? 'bg-primary text-primary-foreground' : 'bg-card text-muted'}`}>
                    {index < current ? '✓ ' : ''}{stage.label}{index === current ? ' · 进行中' : index > current ? ' · 等待' : ''}
                  </span>
                ))}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

const ACTION_LABELS: Record<string, string> = {
  scrape: '采集',
  scored: '评分',
  sent: '发送',
  manual_sent: '手动已发送',
  replied: '回复',
  hr_reply_detected: 'HR 消息',
  auto_replied: '自动回复',
  error: '错误',
  approved: '确认',
  filtered: '过滤',
  resume_sent: '简历',
}

export function RecentActivity({ data, tasks = [] }: RecentActivityProps) {
  const formatTime = (dateStr: string) => {
    if (!dateStr) return ''
    const d = new Date(dateStr)
    return `${(d.getMonth() + 1).toString().padStart(2, '0')}-${d.getDate().toString().padStart(2, '0')} ${d.getHours().toString().padStart(2, '0')}:${d.getMinutes().toString().padStart(2, '0')}`
  }

  if (!data.length) {
    return (
      <Card>
        <CardHeader>
          <CardTitle className="font-semibold text-foreground">最近活动</CardTitle>
        </CardHeader>
        <CardContent>
          <TaskList tasks={tasks} />
          <p className="text-sm text-muted">暂无活动记录</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-semibold text-foreground">最近活动</CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <TaskList tasks={tasks} />
        <div className="space-y-2">
          {data.slice(0, 3).map(item => (
            <div key={item.id} className="flex items-start gap-2">
              <div className="w-1.5 h-1.5 rounded-full bg-primary mt-2 shrink-0" />
              <div className="flex min-w-0 flex-1 flex-wrap items-center gap-x-3 gap-y-0.5">
                <div className="flex min-w-0 flex-1 items-center gap-2">
                  <Badge variant={item.action as any} className="text-[10px] px-1.5 py-0">
                    {ACTION_LABELS[item.action] || item.action}
                  </Badge>
                  <span className="text-xs font-bold text-foreground truncate">
                    {item.company} · {item.title}
                  </span>
                </div>
                <time className="shrink-0 text-[11px] text-muted" dateTime={item.created_at}>{formatTime(item.created_at)}</time>
              </div>
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  )
}
