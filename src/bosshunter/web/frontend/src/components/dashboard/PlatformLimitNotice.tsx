import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { PLATFORM_LABELS } from '@/lib/platforms'

export interface PlatformLimitDetail {
  reason: string
  since: string
  until: string
}

const CODE_LABELS: Record<string, string> = {
  selector_changed: '页面结构变化',
  rate_limit: '遇到验证或限流',
  login_required: '需要重新登录',
  blocked: '被平台拦截',
  network_error: '网络错误',
  browser_disconnected: '浏览器连接断开',
  failed: '采集失败',
}

// A mark that lasts all day stays on the screen this long (a cooldown goes away by itself when it ends).
const SHOW_MINUTES = 60

function clock(iso: string) {
  const match = /T(\d{2}:\d{2})/.exec(iso)
  return match ? match[1] : ''
}

function describe(reason: string) {
  const coded = /^采集异常已暂停（([a-z_]+)）：([\s\S]*)$/.exec(reason)
  if (coded) return { kind: '采集失败', text: `${CODE_LABELS[coded[1]] || coded[1]}：${coded[2]}` }
  if (reason.startsWith('采集遇到验证或限流')) return { kind: '采集被限流', text: reason.replace(/^采集遇到验证或限流，?/, '') }
  if (reason.includes('沟通上限') || reason.includes('沟通已达')) return { kind: '沟通已达上限', text: reason }
  return { kind: '已暂停', text: reason }
}

function dismissKey(platform: string, since: string) {
  return `platform-limit-dismissed:${platform}:${since}`
}

function isDismissed(platform: string, since: string) {
  try {
    return window.localStorage.getItem(dismissKey(platform, since)) === '1'
  } catch {
    return false
  }
}

export function PlatformLimitNotice({ details }: { details: Record<string, PlatformLimitDetail> }) {
  const [, setTick] = useState(0)
  const [dismissed, setDismissed] = useState<string[]>([])

  useEffect(() => {
    const timer = window.setInterval(() => setTick(value => value + 1), 60_000)
    return () => window.clearInterval(timer)
  }, [])

  const now = Date.now()
  const rows = Object.entries(details).filter(([platform, detail]) => {
    if (dismissed.includes(dismissKey(platform, detail.since)) || isDismissed(platform, detail.since)) return false
    if (detail.until || !detail.since) return true
    const since = Date.parse(detail.since)
    return Number.isNaN(since) || now - since < SHOW_MINUTES * 60_000
  })
  if (!rows.length) return null

  const dismiss = (platform: string, since: string) => {
    const key = dismissKey(platform, since)
    try {
      window.localStorage.setItem(key, '1')
    } catch {
      /* 只是不记住这次关闭 */
    }
    setDismissed(previous => [...previous, key])
  }

  return (
    <div role="alert" className="space-y-1.5 rounded-xl border border-warning bg-warning-soft px-4 py-3 text-sm text-warning-strong">
      {rows.map(([platform, detail]) => {
        const { kind, text } = describe(detail.reason)
        const at = clock(detail.since)
        const until = clock(detail.until)
        return (
          <div key={platform} className="flex items-start justify-between gap-3">
            <div>
              <span className="font-black">{PLATFORM_LABELS[platform] || platform}</span>
              <span className="font-bold">{at ? ` · ${at} ${kind}` : ` · ${kind}`}</span>
              <span>：{text}</span>
              <span className="ml-1 text-xs">
                {until ? `（${until} 前暂停，之后自动恢复；期间别的平台照常采集）` : '（今天内暂停；岗位仍在「待投递」，再次采集会先让你确认）'}
              </span>
            </div>
            <button
              type="button"
              onClick={() => dismiss(platform, detail.since)}
              aria-label="关闭这条通知"
              className="shrink-0 rounded p-1 hover:bg-card"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        )
      })}
    </div>
  )
}
