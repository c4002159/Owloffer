import type { ReactNode } from 'react'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'

/** A labelled setting row. ``restart`` marks settings that only apply after the program is restarted. */
export function SettingField({ label, hint, restart, children }: { label: string; hint?: string; restart?: boolean; children: ReactNode }) {
  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between gap-2">
        <label className="block text-xs text-foreground">
          {label}
          {restart && <span className="ml-1.5 rounded-full bg-warning-soft px-1.5 py-0.5 text-[10px] font-bold text-warning">重启后生效</span>}
        </label>
        {hint && <span className="text-[11px] leading-4 text-muted">{hint}</span>}
      </div>
      {children}
    </div>
  )
}

export function NumberSetting({
  label, value, onChange, min, max, step, hint, restart,
}: { label: string; value: number | undefined; onChange: (value: number) => void; min?: number; max?: number; step?: number; hint?: string; restart?: boolean }) {
  return (
    <SettingField label={label} hint={hint} restart={restart}>
      <Input type="number" aria-label={label} value={value ?? ''} onChange={event => onChange(Number(event.target.value))} min={min} max={max} step={step} />
    </SettingField>
  )
}

export function ToggleSetting({
  label, hint, checked, onChange, restart, danger,
}: { label: string; hint?: string; checked: boolean; onChange: (value: boolean) => void; restart?: boolean; danger?: boolean }) {
  return (
    <div className={`flex items-center justify-between gap-3 rounded-xl border px-3 py-2 ${danger && checked ? 'border-warning-border bg-warning-soft' : 'border-card-border bg-surface'}`}>
      <div>
        <label className="text-xs text-foreground">
          {label}
          {restart && <span className="ml-1.5 rounded-full bg-warning-soft px-1.5 py-0.5 text-[10px] font-bold text-warning">重启后生效</span>}
        </label>
        {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
      </div>
      <Switch aria-label={label} checked={checked} onChange={onChange} />
    </div>
  )
}

export function SubCard({ title, children, hint }: { title: string; children: ReactNode; hint?: string }) {
  return (
    <div className="space-y-3 rounded-xl border border-card-border bg-card p-3">
      <div>
        <p className="text-xs font-black text-foreground">{title}</p>
        {hint && <p className="mt-0.5 text-[11px] leading-4 text-muted">{hint}</p>}
      </div>
      {children}
    </div>
  )
}
