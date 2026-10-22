import { useEffect } from 'react'
import { AlertTriangle, RotateCcw } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { describeChanges, diffConfig, formatValue, groupByCategory } from '@/lib/configDiff'

interface SaveDialogProps {
  baseline: Record<string, any> | null
  config: Record<string, any>
  saving: boolean
  onRevert: (path: string) => void
  onConfirm: () => void
  onCancel: () => void
}

/** Shown before saving: what changed (old → new), what needs care, what needs a restart. */
export function SaveDialog({ baseline, config, saving, onRevert, onConfirm, onCancel }: SaveDialogProps) {
  const changes = describeChanges(diffConfig(baseline ?? {}, config))
  const groups = groupByCategory(changes)
  const risky = changes.filter(change => change.risky)
  const restart = changes.filter(change => change.restart)

  useEffect(() => {
    const escape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [onCancel])

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="确认保存">
      <div className="flex max-h-[86vh] w-full max-w-2xl flex-col rounded-2xl border border-card-border bg-card shadow-2xl">
        <div className="border-b border-card-border px-5 py-4">
          <h2 className="text-lg font-black text-foreground">确认保存</h2>
          <p className="mt-1 text-xs text-muted">{changes.length ? `共改了 ${changes.length} 项，保存前请看一眼。` : '没有检测到具体的改动，仍可保存。'}</p>
        </div>
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
          {risky.length > 0 && (
            <div className="flex gap-2 rounded-xl border border-warning-border bg-warning-soft px-3 py-2 text-xs leading-5 text-warning-strong">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <span>这些项会影响投递节奏或自动化，请确认没改错：{risky.map(change => change.label).join('、')}。</span>
            </div>
          )}
          {restart.length > 0 && (
            <div className="rounded-xl border border-card-border bg-surface px-3 py-2 text-xs leading-5 text-muted">
              需要重启程序后才生效：{restart.map(change => change.label).join('、')}。
            </div>
          )}
          {groups.map(group => (
            <div key={group.category}>
              <div className="mb-1 text-xs font-black text-foreground">{group.label}</div>
              <ul className="divide-y divide-card-border rounded-xl border border-card-border">
                {group.items.map(change => (
                  <li key={change.path} className="flex items-start justify-between gap-3 px-3 py-2 text-xs">
                    <div className="min-w-0">
                      <div className="font-bold text-foreground">
                        {change.label}
                        {change.risky && <span className="ml-1.5 rounded-full bg-warning-soft px-1.5 py-0.5 text-[10px] font-bold text-warning">谨慎</span>}
                        {change.restart && <span className="ml-1.5 rounded-full bg-secondary px-1.5 py-0.5 text-[10px] font-bold text-primary">重启生效</span>}
                      </div>
                      <div className="mt-1 break-words leading-5 text-muted">
                        <span className="line-through opacity-70">{formatValue(change.path, change.before)}</span>
                        <span className="mx-1.5 text-primary">→</span>
                        <span className="font-bold text-foreground">{formatValue(change.path, change.after)}</span>
                      </div>
                    </div>
                    <button type="button" onClick={() => onRevert(change.path)} title="撤销这一项" aria-label={`撤销 ${change.label} 的修改`} className="shrink-0 rounded-md p-1.5 text-muted hover:bg-secondary hover:text-primary">
                      <RotateCcw className="h-3.5 w-3.5" />
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
        <div className="flex justify-end gap-2 border-t border-card-border px-5 py-3">
          <Button variant="secondary" size="sm" onClick={onCancel}>取消</Button>
          <Button size="sm" disabled={saving} onClick={onConfirm}>{saving ? '保存中...' : '确认保存'}</Button>
        </div>
      </div>
    </div>
  )
}
