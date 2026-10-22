import type { ReactNode } from 'react'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { TagsInput } from '@/components/ui/tags-input'

type Config = Record<string, any>
type Update = (path: string, value: unknown) => void

interface FieldsProps {
  config: Config
  updateConfig: Update
}

/** 标注一个设置多久生效：下次任务读取配置即生效，或需要重启本服务。 */
export function ApplyBadge({ kind }: { kind: 'task' | 'restart' }) {
  return kind === 'restart' ? (
    <span className="rounded border border-warning-border bg-warning-soft px-1.5 py-0.5 text-[10px] font-medium text-warning-strong">
      需重启服务
    </span>
  ) : (
    <span className="rounded border border-card-border bg-surface-muted px-1.5 py-0.5 text-[10px] text-muted">
      保存后下次任务生效
    </span>
  )
}

function Row({ label, hint, badge = 'task', children }: { label: string; hint?: string; badge?: 'task' | 'restart'; children: ReactNode }) {
  return (
    <div data-field>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <label className="block text-xs text-foreground">{label}</label>
        <ApplyBadge kind={badge} />
      </div>
      {children}
      {hint && <p className="mt-1 text-xs text-muted">{hint}</p>}
    </div>
  )
}

function SwitchRow({ label, hint, checked, onChange, badge = 'task', risky }: {
  label: string; hint?: string; checked: boolean; onChange: (v: boolean) => void; badge?: 'task' | 'restart'; risky?: boolean
}) {
  return (
    <div data-field className={`flex items-start justify-between gap-4 rounded-md border p-3 ${risky ? 'border-danger-border bg-danger-soft' : 'border-card-border'}`}>
      <div>
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-foreground">{label}</span>
          {risky && <span className="rounded bg-danger-solid px-1.5 py-0.5 text-[10px] text-danger-solid-foreground">高风险</span>}
          <ApplyBadge kind={badge} />
        </div>
        {hint && <p className={`mt-1 text-xs ${risky ? 'text-danger-strong' : 'text-muted'}`}>{hint}</p>}
      </div>
      <Switch aria-label={label} checked={checked} onChange={onChange} />
    </div>
  )
}

function NumberInput({ value, onChange, min, max, step }: {
  value: number; onChange: (n: number) => void; min?: number; max?: number; step?: number
}) {
  return (
    <Input
      type="number"
      value={value}
      min={min}
      max={max}
      step={step}
      onChange={e => {
        const next = Number(e.target.value)
        if (Number.isFinite(next)) onChange(next)
      }}
    />
  )
}

/** 岗位新鲜度与过滤：岗位最大天数、HR 活跃度、公司冷却、僵尸岗位天数。 */
export function FreshnessFields({ config, updateConfig }: FieldsProps) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Row label="岗位最大天数" hint="只保留最近这么多天内更新的岗位；0 表示不限制。">
        <NumberInput value={config.profile?.max_job_age_days ?? 0} onChange={v => updateConfig('profile.max_job_age_days', v)} min={0} max={365} />
      </Row>
      <Row label="HR 活跃度下限" hint="低于所选活跃程度的岗位在评分前被过滤。">
        <Select value={String(config.profile?.hr_active_min_level ?? 0)} onChange={e => updateConfig('profile.hr_active_min_level', Number(e.target.value))}>
          <option value="0">不限制</option>
          <option value="1">一个月内活跃</option>
          <option value="2">三天内活跃</option>
          <option value="3">当前在线</option>
        </Select>
      </Row>
      <Row label="公司冷却天数" hint="近这几天已投递过的公司，不再重复采集或投递。">
        <NumberInput value={config.automation?.company_cooldown_days ?? 1} onChange={v => updateConfig('automation.company_cooldown_days', v)} min={0} max={90} />
      </Row>
      <Row label="僵尸岗位天数" hint="岗位连续在招这么多天、HR 又不活跃，会被移出审查列表。">
        <NumberInput value={config.automation?.zombie_days ?? 7} onChange={v => updateConfig('automation.zombie_days', v)} min={1} max={60} />
      </Row>
      <div className="md:col-span-2">
        <SwitchRow
          label="读不到 HR 活跃度时放行"
          hint="关闭后，没有活跃度信息的岗位也会被活跃度下限过滤掉。"
          checked={config.profile?.hr_active_unknown_pass ?? true}
          onChange={v => updateConfig('profile.hr_active_unknown_pass', v)}
        />
      </div>
    </div>
  )
}

/** 评分里的两个单项门槛，以及进入待确认列表的最低分。 */
export function ScoringExtraFields({ config, updateConfig }: FieldsProps) {
  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Row label="最低匹配分" hint="匹配度低于它直接淘汰；0 表示不设。">
        <NumberInput value={config.scoring?.min_match_score ?? 0} onChange={v => updateConfig('scoring.min_match_score', v)} min={0} max={100} />
      </Row>
      <Row label="最低活跃分" hint="活跃度低于它直接淘汰；0 表示不设。">
        <NumberInput value={config.scoring?.min_activity_score ?? 0} onChange={v => updateConfig('scoring.min_activity_score', v)} min={0} max={100} />
      </Row>
      <Row label="进入待确认的最低分" hint="综合分达到它才会出现在待确认里。">
        <NumberInput value={config.automation?.review_min_score ?? 60} onChange={v => updateConfig('automation.review_min_score', v)} min={0} max={100} />
      </Row>
    </div>
  )
}

/** 自动化与文件：自动确认、关键词预算、定制简历输出目录。 */
export function AutomationFields({ config, updateConfig }: FieldsProps) {
  return (
    <div className="space-y-4">
      <SwitchRow
        risky
        label="自动确认投递（auto_send）"
        hint="开启后，通过评分的岗位会被自动确认，跳过你的人工审核，随后按发送窗口自动投递。建议保持关闭。"
        checked={config.automation?.auto_send ?? false}
        onChange={v => updateConfig('automation.auto_send', v)}
      />
      <SwitchRow
        label="关键词预算与自动升降档"
        hint="按关键词近期表现分配每词的新增名额，连续表现好的升档，探索词长期 0 通过会暂停。"
        checked={config.automation?.keyword_budget ?? true}
        onChange={v => updateConfig('automation.keyword_budget', v)}
      />
      <div className="grid gap-4 md:grid-cols-2">
        <Row label="猎聘选简历的最低匹配度" hint="岗位与某份附件简历的标签匹配度低于它，视为没有合适简历，降级为发招呼语（0–1）。">
          <NumberInput value={config.automation?.resume_match_min_score ?? 0.2} onChange={v => updateConfig('automation.resume_match_min_score', v)} min={0} max={1} step={0.05} />
        </Row>
        <Row label="平台简历重新读取间隔（小时）" hint="每隔这么久检查一次平台账号里的附件简历是否换过。">
          <NumberInput value={config.automation?.resume_refresh_hours ?? 12} onChange={v => updateConfig('automation.resume_refresh_hours', v)} min={1} max={168} />
        </Row>
      </div>
      <Row label="定制简历输出目录" hint="生成的定制简历文件保存到这里（相对项目目录或绝对路径）。">
        <Input value={config.profile?.resume_output_dir ?? './data/resumes'} onChange={e => updateConfig('profile.resume_output_dir', e.target.value)} />
      </Row>
    </div>
  )
}

/** AI 细项：token 上限、重试次数、招呼语审阅阈值。 */
export function AiAdvancedFields({ config, updateConfig }: FieldsProps) {
  const ai = config.ai ?? {}
  return (
    <div className="grid gap-4 md:grid-cols-3">
      <Row label="评分 token 上限" hint="单次评分回答的最大长度。">
        <NumberInput value={ai.scoring_max_tokens ?? 8192} onChange={v => updateConfig('ai.scoring_max_tokens', v)} min={512} max={32768} step={256} />
      </Row>
      <Row label="评分最多尝试次数" hint="回答无法解析时重试，最多 3 次。">
        <NumberInput value={ai.scoring_max_attempts ?? 2} onChange={v => updateConfig('ai.scoring_max_attempts', v)} min={1} max={3} />
      </Row>
      <Row label="招呼语审阅阈值" hint="审阅分低于它会重新生成。">
        <NumberInput value={ai.greeting_review_threshold ?? 7} onChange={v => updateConfig('ai.greeting_review_threshold', v)} min={0} max={10} step={0.5} />
      </Row>
      <Row label="招呼语 token 上限">
        <NumberInput value={ai.greeting_max_tokens ?? 8192} onChange={v => updateConfig('ai.greeting_max_tokens', v)} min={512} max={32768} step={256} />
      </Row>
      <Row label="招呼语审阅 token 上限">
        <NumberInput value={ai.greeting_review_max_tokens ?? 4096} onChange={v => updateConfig('ai.greeting_review_max_tokens', v)} min={512} max={32768} step={256} />
      </Row>
      <Row label="招呼语最多尝试次数">
        <NumberInput value={ai.greeting_max_attempts ?? 2} onChange={v => updateConfig('ai.greeting_max_attempts', v)} min={1} max={5} />
      </Row>
    </div>
  )
}

/** 安全：触发风控后的锁定时长。 */
export function SafetyExtraFields({ config, updateConfig }: FieldsProps) {
  return (
    <Row label="风险锁定时长（分钟）" hint="检测到风控信号后，平台锁定这么久才允许再次访问。">
      <NumberInput value={config.safety?.risk_lock_minutes ?? 10} onChange={v => updateConfig('safety.risk_lock_minutes', v)} min={1} max={1440} />
    </Row>
  )
}

function parsePorts(tags: string[]): number[] {
  const ports = tags.map(tag => Number(tag)).filter(port => Number.isInteger(port) && port >= 1 && port <= 65535)
  return [...new Set(ports)]
}

/** 浏览器连接：运行时、代理地址、Chrome 调试端口。这些在服务启动时读取，改后要重启。 */
export function BrowserFields({ config, updateConfig }: FieldsProps) {
  const browser = config.browser ?? {}
  const ports: number[] = Array.isArray(browser.chrome_ports) ? browser.chrome_ports : []
  return (
    <div className="space-y-4">
      <p className="rounded-md border border-warning-border bg-warning-soft px-3 py-2 text-xs text-warning-strong">
        这一组设置在服务启动时读取，修改并保存后需要在终端重启 <code>bosshunter web</code> 才会生效。不确定时请保持默认。
      </p>
      <div className="grid gap-4 md:grid-cols-3">
        <Row label="浏览器运行组件" badge="restart">
          <Input value={browser.runtime ?? 'builtin'} onChange={e => updateConfig('browser.runtime', e.target.value)} />
        </Row>
        <Row label="代理主机" badge="restart">
          <Input value={browser.proxy_host ?? '127.0.0.1'} onChange={e => updateConfig('browser.proxy_host', e.target.value)} />
        </Row>
        <Row label="代理端口" badge="restart">
          <NumberInput value={browser.proxy_port ?? 3456} onChange={v => updateConfig('browser.proxy_port', v)} min={1} max={65535} />
        </Row>
      </div>
      <Row label="Chrome 调试端口" badge="restart" hint="按顺序尝试连接；输入端口号后回车添加。">
        <TagsInput
          value={ports.map(String)}
          onChange={tags => updateConfig('browser.chrome_ports', parsePorts(tags))}
          placeholder="如：9222"
        />
      </Row>
      <div className="grid gap-3 md:grid-cols-3">
        <SwitchRow badge="restart" label="自动启动代理" checked={browser.auto_start_proxy ?? true} onChange={v => updateConfig('browser.auto_start_proxy', v)} />
        <SwitchRow badge="restart" label="端口保护" checked={browser.enable_port_guard ?? true} onChange={v => updateConfig('browser.enable_port_guard', v)} />
        <SwitchRow badge="restart" label="站点匹配规则" checked={browser.site_patterns ?? true} onChange={v => updateConfig('browser.site_patterns', v)} />
      </div>
    </div>
  )
}
