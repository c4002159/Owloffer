import { useCallback, useEffect, useRef, useState } from 'react'
import { Download, RefreshCw, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { SubCard } from './fields'

interface Backup {
  name: string
  size: number
  created: string
}

interface LogFile {
  name: string
  size: number
  modified: string
}

const size = (bytes: number) => (bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`)
const when = (iso: string) => iso.replace('T', ' ')

async function call(path: string, init?: RequestInit) {
  const res = await fetch(path, init)
  const data = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(data.error || '操作失败')
  return data
}

/** Backups of the data and a viewer for the program's logs. */
export function DataAndLogs() {
  const [backups, setBackups] = useState<Backup[]>([])
  const [auto, setAuto] = useState<{ enabled: boolean; keep: number } | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [logs, setLogs] = useState<LogFile[]>([])
  const [logName, setLogName] = useState('')
  const [level, setLevel] = useState('')
  const [query, setQuery] = useState('')
  const [lineCount, setLineCount] = useState('300')
  const [lines, setLines] = useState<string[]>([])
  const [matched, setMatched] = useState(0)
  const [follow, setFollow] = useState(false)
  const viewRef = useRef<HTMLPreElement>(null)

  const loadBackups = useCallback(async () => {
    try {
      const data = await call('/api/system/backups')
      setBackups(data.backups || [])
      setAuto({ enabled: Boolean(data.enabled), keep: Number(data.keep) || 14 })
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '读取备份失败')
    }
  }, [])

  const loadLogs = useCallback(async () => {
    try {
      const data = await call('/api/system/logs')
      const files: LogFile[] = data.logs || []
      setLogs(files)
      setLogName(current => (current && files.some(file => file.name === current) ? current : files[0]?.name || ''))
    } catch {
      setLogs([])
    }
  }, [])

  const loadTail = useCallback(async () => {
    if (!logName) {
      setLines([])
      return
    }
    try {
      const params = new URLSearchParams({ name: logName, lines: lineCount || '300', level, q: query })
      const data = await call(`/api/system/logs/tail?${params.toString()}`)
      setLines(data.lines || [])
      setMatched(Number(data.matched) || 0)
    } catch (cause) {
      setLines([cause instanceof Error ? cause.message : '读取日志失败'])
    }
  }, [logName, lineCount, level, query])

  useEffect(() => { void loadBackups(); void loadLogs() }, [loadBackups, loadLogs])
  useEffect(() => {
    const timer = window.setTimeout(() => void loadTail(), 250)
    return () => window.clearTimeout(timer)
  }, [loadTail])
  useEffect(() => {
    if (!follow) return
    const timer = window.setInterval(() => void loadTail(), 3000)
    return () => window.clearInterval(timer)
  }, [follow, loadTail])
  useEffect(() => {
    if (follow && viewRef.current) viewRef.current.scrollTop = viewRef.current.scrollHeight
  }, [lines, follow])

  const run = async (action: () => Promise<void>) => {
    setBusy(true)
    setNotice('')
    try {
      await action()
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : '操作失败')
    } finally {
      setBusy(false)
    }
  }

  const backupNow = () => run(async () => {
    const item = await call('/api/system/backups', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' })
    setNotice(`已备份：${item.name}（${size(item.size)}）`)
    await loadBackups()
  })

  const restore = (backup: Backup) => {
    if (!window.confirm(`用备份「${backup.name}」恢复？\n\n数据库、设置和简历文件会被替换成这个备份里的内容。恢复前会先自动再备份一次现在的状态，需要时还能回到现在。`)) return
    void run(async () => {
      const data = await call('/api/system/backups/restore', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: backup.name, confirmed: true }) })
      setNotice(`已恢复：${(data.restored || []).join('、')}。恢复前的状态保存在 ${data.safety_backup}。请刷新页面。`)
      await loadBackups()
    })
  }

  const remove = (backup: Backup) => {
    if (!window.confirm(`删除备份「${backup.name}」？`)) return
    void run(async () => {
      await call(`/api/system/backups?name=${encodeURIComponent(backup.name)}`, { method: 'DELETE' })
      await loadBackups()
    })
  }

  const setAutoBackup = (enabled: boolean, keep: number) => run(async () => {
    const config = await call('/api/config')
    await call('/api/config', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...config, backup: { ...(config.backup || {}), enabled, keep } }) })
    await loadBackups()
  })

  return (
    <div className="space-y-4">
      <SubCard title="数据备份" hint="数据库、设置和简历文件打成一个压缩包，存在 data/backups。API Key 和智能体令牌不在备份里。">
        <div className="flex flex-wrap items-center gap-3 text-xs">
          <Button size="sm" disabled={busy} onClick={() => void backupNow()}>立即备份</Button>
          {auto && (
            <>
              <label className="flex items-center gap-1.5 font-bold">
                <input type="checkbox" className="accent-primary" checked={auto.enabled} onChange={event => void setAutoBackup(event.target.checked, auto.keep)} />
                每天自动备份
              </label>
              <label className="flex items-center gap-1.5 font-bold">
                保留最近
                <Input aria-label="保留份数" type="number" min={1} max={100} value={auto.keep} onChange={event => setAuto({ ...auto, keep: Number(event.target.value) })} onBlur={() => void setAutoBackup(auto.enabled, Math.max(1, auto.keep))} className="h-8 w-20" />
                份
              </label>
            </>
          )}
        </div>
        {notice && <p className="rounded-lg bg-secondary px-3 py-2 text-xs font-bold text-primary">{notice}</p>}
        {backups.length ? (
          <ul className="divide-y divide-card-border rounded-xl border border-card-border">
            {backups.map(backup => (
              <li key={backup.name} className="flex flex-wrap items-center justify-between gap-2 px-3 py-2 text-xs">
                <span className="min-w-0"><span className="font-bold text-foreground">{when(backup.created)}</span><span className="ml-2 text-muted">{size(backup.size)}{backup.name.includes('pre-restore') ? ' · 恢复前自动备份' : ''}</span></span>
                <span className="flex items-center gap-2">
                  <a href={`/api/system/backups/download?name=${encodeURIComponent(backup.name)}`} className="inline-flex items-center gap-1 font-bold text-primary hover:underline"><Download className="h-3 w-3" />下载</a>
                  <button type="button" disabled={busy} onClick={() => restore(backup)} className="font-bold text-primary hover:underline disabled:opacity-50">恢复</button>
                  <button type="button" disabled={busy} onClick={() => remove(backup)} aria-label={`删除备份 ${backup.name}`} className="rounded-md p-1 text-muted hover:bg-danger-soft hover:text-danger"><Trash2 className="h-3.5 w-3.5" /></button>
                </span>
              </li>
            ))}
          </ul>
        ) : <p className="text-xs text-muted">还没有备份。</p>}
      </SubCard>

      <SubCard title="运行日志" hint="程序自己的日志。出问题时先看带 ERROR 的行；要找人帮忙，可以导出诊断包（日志末尾加去掉个人信息和密钥的设置）。">
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <Select aria-label="日志文件" value={logName} onChange={event => setLogName(event.target.value)} className="h-8 w-56">
            {logs.length === 0 && <option value="">没有日志文件</option>}
            {logs.map(file => <option key={file.name} value={file.name}>{file.name}（{size(file.size)}，{when(file.modified).slice(5, 16)}）</option>)}
          </Select>
          <Select aria-label="日志级别" value={level} onChange={event => setLevel(event.target.value)} className="h-8 w-28">
            <option value="">全部</option>
            <option value="error">只看错误</option>
            <option value="warning">只看警告</option>
          </Select>
          <Input aria-label="日志搜索" value={query} onChange={event => setQuery(event.target.value)} placeholder="搜索关键字" className="h-8 w-44" />
          <Select aria-label="显示行数" value={lineCount} onChange={event => setLineCount(event.target.value)} className="h-8 w-24">
            {['100', '300', '1000'].map(value => <option key={value} value={value}>{value} 行</option>)}
          </Select>
          <label className="flex items-center gap-1.5 font-bold"><input type="checkbox" className="accent-primary" checked={follow} onChange={event => setFollow(event.target.checked)} />自动刷新</label>
          <Button size="sm" variant="secondary" onClick={() => void loadTail()}><RefreshCw className="mr-1 h-3.5 w-3.5" />刷新</Button>
          {logName && <a href={`/api/system/logs/download?name=${encodeURIComponent(logName)}`} className="inline-flex items-center gap-1 font-bold text-primary hover:underline"><Download className="h-3 w-3" />下载</a>}
          <a href="/api/system/diagnostics" className="inline-flex items-center gap-1 font-bold text-primary hover:underline"><Download className="h-3 w-3" />导出诊断包</a>
        </div>
        <pre ref={viewRef} aria-label="日志内容" className="max-h-96 overflow-auto whitespace-pre-wrap break-all rounded-lg border border-card-border bg-surface p-3 text-[11px] leading-5 text-muted">
          {lines.length ? lines.join('\n') : '没有符合条件的日志行。'}
        </pre>
        <p className="text-[11px] text-muted">共 {matched} 行符合条件，显示最后 {lines.length} 行。</p>
      </SubCard>
    </div>
  )
}
