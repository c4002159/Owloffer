export interface ConfigChange {
  path: string
  label: string
  before: string
  after: string
  risky?: string
  restart?: boolean
}

/** 高风险设置：改动前要二次确认。值为风险说明。 */
const RISKY: Record<string, string> = {
  'automation.auto_send': '开启后岗位会自动确认并投递，跳过人工审核',
  'throttle.daily_limit': '每日发送上限直接影响账号风控',
  'throttle.send_windows': '发送时间窗口决定何时自动投递',
  'throttle.interval_min': '发送间隔过短容易触发风控',
  'throttle.interval_max': '发送间隔过短容易触发风控',
  'collection.daily_search_page_limit': 'BOSS 单日搜索页上限，过高容易触发风控',
  'safety.daily_platform_page_limit': '单日页面访问总上限，过高容易触发风控',
  'safety.risk_lock_minutes': '风险锁定时长过短会在风控后过早重试',
}

const LABELS: Record<string, string> = {
  'automation.auto_send': '自动确认投递',
  'throttle.daily_limit': '每日发送上限',
  'throttle.send_windows': '发送时间窗口',
  'throttle.collect_windows': '采集时间窗口',
  'throttle.interval_min': '发送间隔下限（秒）',
  'throttle.interval_max': '发送间隔上限（秒）',
  'throttle.day_off_probability': '随机休息概率',
  'collection.daily_search_page_limit': 'BOSS 单日搜索页上限',
  'collection.collection_delay_multiplier': '采集延迟倍率',
  'safety.daily_platform_page_limit': '单日页面访问总上限',
  'safety.risk_lock_minutes': '风险锁定时长（分钟）',
  'scoring.min_match_score': '最低匹配分',
  'scoring.min_activity_score': '最低活跃分',
  'automation.review_min_score': '待确认最低分',
  'profile.max_job_age_days': '岗位最大天数',
  'profile.hr_active_min_level': 'HR 活跃度下限',
  'automation.company_cooldown_days': '公司冷却天数',
  'automation.zombie_days': '僵尸岗位天数',
  'ai.model': 'AI 模型',
  'browser.proxy_port': '代理端口',
  'browser.proxy_host': '代理主机',
  'browser.chrome_ports': 'Chrome 调试端口',
}

const SECRET = /(api_key|token|password|secret)/i
const RESTART = /^browser\./

export function flattenConfig(value: unknown, prefix = '', out: Record<string, unknown> = {}): Record<string, unknown> {
  if (value && typeof value === 'object' && !Array.isArray(value)) {
    const entries = Object.entries(value as Record<string, unknown>)
    if (entries.length === 0 && prefix) out[prefix] = {}
    for (const [k, v] of entries) flattenConfig(v, prefix ? `${prefix}.${k}` : k, out)
  } else if (prefix) {
    out[prefix] = value
  }
  return out
}

function show(path: string, v: unknown): string {
  if (SECRET.test(path)) return v ? '••••（已设置）' : '（空）'
  if (v === undefined || v === null || v === '') return '（空）'
  if (typeof v === 'boolean') return v ? '开' : '关'
  if (Array.isArray(v)) return v.length ? v.join('、') : '（空）'
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}

export function diffConfig(before: Record<string, any> | null, after: Record<string, any> | null): ConfigChange[] {
  if (!before || !after) return []
  const a = flattenConfig(before)
  const b = flattenConfig(after)
  const keys = [...new Set([...Object.keys(a), ...Object.keys(b)])].sort()
  const changes: ConfigChange[] = []
  for (const path of keys) {
    if (JSON.stringify(a[path]) === JSON.stringify(b[path])) continue
    changes.push({
      path,
      label: LABELS[path] ?? path,
      before: show(path, a[path]),
      after: show(path, b[path]),
      risky: RISKY[path],
      restart: RESTART.test(path),
    })
  }
  // 高风险排前面
  return changes.sort((x, y) => Number(!!y.risky) - Number(!!x.risky))
}
