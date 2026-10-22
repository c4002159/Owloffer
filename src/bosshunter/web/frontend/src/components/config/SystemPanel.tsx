import { useCallback, useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { Download, RefreshCw, AlertTriangle } from 'lucide-react'

interface SystemInfo {
  version: string
  timezone: { name: string; offset: string; expected: string; mismatch: boolean; warning: string | null; local_now: string }
  paths: { base_dir: string; config: string; database: string }
  database: { exists: boolean; size_bytes: number }
}

interface TaskLog {
  id: string
  mode?: string
  label?: string
  status?: string
  logs?: string[]
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** 系统页：时区提醒、数据备份与导出、最近任务日志。 */
export function SystemPanel() {
  const [info, setInfo] = useState<SystemInfo | null>(null)
  const [tasks, setTasks] = useState<TaskLog[] | null>(null)
  const [loadError, setLoadError] = useState(false)

  const loadLogs = useCallback(() => {
    fetch('/api/system/logs').then(r => r.json()).then(d => setTasks(d.tasks ?? [])).catch(() => setTasks([]))
  }, [])

  useEffect(() => {
    fetch('/api/system/info').then(r => { if (!r.ok) throw new Error(); return r.json() }).then(setInfo).catch(() => setLoadError(true))
    loadLogs()
  }, [loadLogs])

  if (loadError) return <p className="text-xs text-danger">系统信息读取失败，请确认服务已更新到最新版本。</p>
  if (!info) return <p className="text-xs text-muted">正在读取…</p>

  return (
    <div className="space-y-5" data-testid="system-panel">
      {info.timezone.warning && (
        <div className="flex gap-2 rounded-md border border-warning-border bg-warning-soft p-3 text-xs text-warning-strong">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{info.timezone.warning}</span>
        </div>
      )}
      <div data-field className="grid gap-2 text-xs md:grid-cols-2">
        <div><span className="text-muted">本机时区：</span>{info.timezone.name || '未知'}（{info.timezone.offset}）</div>
        <div><span className="text-muted">当前本机时间：</span>{info.timezone.local_now}</div>
        <div className="md:col-span-2"><span className="text-muted">数据库：</span>{info.paths.database}（{info.database.exists ? formatSize(info.database.size_bytes) : '尚未创建'}）</div>
        <div className="md:col-span-2"><span className="text-muted">配置文件：</span>{info.paths.config}</div>
      </div>

      <div data-field>
        <div className="mb-2 text-xs font-medium text-foreground">数据备份与导出</div>
        <div className="flex flex-wrap gap-2">
          <a href="/api/system/backup" download>
            <Button variant="secondary" size="sm" disabled={!info.database.exists}><Download className="mr-1 h-3 w-3" />下载数据库备份</Button>
          </a>
          <a href="/api/config/download" download>
            <Button variant="secondary" size="sm"><Download className="mr-1 h-3 w-3" />导出配置（不含密钥）</Button>
          </a>
        </div>
        <p className="mt-1 text-xs text-muted">备份是岗位、评分、投递记录的完整副本，建议大改配置或升级前先备份。</p>
      </div>

      <div data-field>
        <div className="mb-2 flex items-center justify-between">
          <span className="text-xs font-medium text-foreground">最近任务日志</span>
          <Button variant="ghost" size="sm" onClick={loadLogs}><RefreshCw className="mr-1 h-3 w-3" />刷新</Button>
        </div>
        <p className="mb-2 text-xs text-muted">仅包含本次服务启动以来的任务；重启服务后会清空。</p>
        <div className="max-h-72 space-y-2 overflow-y-auto">
          {(tasks ?? []).length === 0 && <p className="text-xs text-muted">暂无任务日志。</p>}
          {(tasks ?? []).map(task => (
            <details key={task.id} className="rounded-md border border-card-border bg-surface p-2 text-xs">
              <summary className="cursor-pointer text-foreground">{task.label ?? task.mode ?? "任务"} · {task.status ?? ''} · {(task.logs ?? []).length} 条</summary>
              <pre className="mt-2 whitespace-pre-wrap break-all text-muted">{(task.logs ?? []).join('\n') || '（无日志）'}</pre>
            </details>
          ))}
        </div>
      </div>
    </div>
  )
}
