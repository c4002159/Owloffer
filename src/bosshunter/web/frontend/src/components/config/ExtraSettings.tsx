import { Select } from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { TagsInput } from '@/components/ui/tags-input'
import { NumberSetting, SettingField, SubCard, ToggleSetting } from './fields'

type Config = Record<string, any>
type Update = (path: string, value: unknown) => void

/** Freshness and company rules that used to exist only in config.yaml. */
export function FreshnessSettings({ config, updateConfig }: { config: Config; updateConfig: Update }) {
  return (
    <SubCard title="新鲜度与公司规则" hint="过滤太旧的岗位、不活跃的招聘者，以及刚投过的公司。">
      <div className="grid gap-4 md:grid-cols-2">
        <NumberSetting label="岗位最大天数（0 = 不限）" value={config.profile?.max_job_age_days ?? 0} onChange={v => updateConfig('profile.max_job_age_days', v)} min={0} max={365}
          hint="首次入库超过这么多天的岗位会被过滤" />
        <SettingField label="招聘者活跃度下限" hint="数值越高要求越活跃">
          <Select aria-label="招聘者活跃度下限" value={String(config.profile?.hr_active_min_level ?? 0)} onChange={e => updateConfig('profile.hr_active_min_level', Number(e.target.value))}>
            <option value="0">不限</option>
            <option value="1">一个月内活跃</option>
            <option value="2">三天内活跃</option>
            <option value="3">在线或今天活跃</option>
          </Select>
        </SettingField>
        <NumberSetting label="同公司冷却天数" value={config.automation?.company_cooldown_days} onChange={v => updateConfig('automation.company_cooldown_days', v)} min={0} max={365}
          hint="投过的公司这么多天内不再出现新岗位" />
        <NumberSetting label="僵尸岗位天数" value={config.automation?.zombie_days} onChange={v => updateConfig('automation.zombie_days', v)} min={0} max={365}
          hint="发出去这么多天没回复的岗位视为僵尸" />
      </div>
      <ToggleSetting label="活跃度未知的岗位也放行" checked={config.profile?.hr_active_unknown_pass ?? true} onChange={v => updateConfig('profile.hr_active_unknown_pass', v)}
        hint="平台没给出活跃度时，是否按通过处理" />
      <SettingField label="定制简历输出目录" hint="留空使用默认位置">
        <Input aria-label="定制简历输出目录" value={config.profile?.resume_output_dir || ''} onChange={e => updateConfig('profile.resume_output_dir', e.target.value)} placeholder="默认：项目 data 目录" />
      </SettingField>
    </SubCard>
  )
}

export function ScoringExtras({ config, updateConfig }: { config: Config; updateConfig: Update }) {
  return (
    <SubCard title="分数门槛" hint="门槛之下的岗位不进入待确认。">
      <div className="grid gap-4 md:grid-cols-3">
        <NumberSetting label="最低匹配分" value={config.scoring?.min_match_score} onChange={v => updateConfig('scoring.min_match_score', v)} min={0} max={100} />
        <NumberSetting label="最低活跃分" value={config.scoring?.min_activity_score} onChange={v => updateConfig('scoring.min_activity_score', v)} min={0} max={100} />
        <NumberSetting label="进入审查的最低分" value={config.automation?.review_min_score} onChange={v => updateConfig('automation.review_min_score', v)} min={0} max={100} />
      </div>
    </SubCard>
  )
}

/** Automation switches: what the program may do on its own. */
export function AutomationSettings({ config, updateConfig }: { config: Config; updateConfig: Update }) {
  return (
    <div className="space-y-4">
      <ToggleSetting danger label="自动投递（无需逐个确认）" checked={config.automation?.auto_send ?? false} onChange={v => updateConfig('automation.auto_send', v)}
        hint="开启后，通过评分的岗位会直接进入投递。每日循环不受这个开关影响，它有自己的启动和上限设置。" />
      <div className="grid gap-4 md:grid-cols-2">
        <NumberSetting label="待审查/待发送岗位上限" value={config.automation?.review_cap} onChange={v => updateConfig('automation.review_cap', v)} min={1} max={1000}
          hint="达到后采集和评分会暂停，直到你处理一部分" />
      </div>
      <ToggleSetting label="关键词预算（按关键词分配采集名额）" checked={config.automation?.keyword_budget ?? true} onChange={v => updateConfig('automation.keyword_budget', v)}
        hint="每个关键词按层级分配采集名额，并记住当天哪些词已采完。" />
      <ToggleSetting label="页数记忆（头部 + 深层窗口）" checked={config.automation?.page_frontier ?? true} onChange={v => updateConfig('automation.page_frontier', v)}
        hint="每个关键词先读头部新岗位，再从上次停下的页往后读一个窗口，不重复筛同样的页。" />
      <p className="text-xs leading-5 text-muted">每日循环的开始/结束时间、各平台目标数量和投递上限，在「工作台 → 运行全流程」的齿轮里设置。</p>
    </div>
  )
}

export function SafetyExtras({ config, updateConfig }: { config: Config; updateConfig: Update }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      <NumberSetting label="风险锁定时长（分钟）" value={config.safety?.risk_lock_minutes} onChange={v => updateConfig('safety.risk_lock_minutes', v)} min={1} max={1440}
        hint="平台出现验证或限流后，这么久内不再操作该平台" />
    </div>
  )
}

export function AiExtras({ config, updateConfig }: { config: Config; updateConfig: Update }) {
  return (
    <SubCard title="AI 调用细项" hint="一般不用改；回答被截断或经常失败时再调。">
      <div className="grid gap-4 md:grid-cols-3">
        <NumberSetting label="评分最大 token" value={config.ai?.scoring_max_tokens} onChange={v => updateConfig('ai.scoring_max_tokens', v)} min={100} max={32000} />
        <NumberSetting label="评分最多重试次数" value={config.ai?.scoring_max_attempts} onChange={v => updateConfig('ai.scoring_max_attempts', v)} min={1} max={10} />
        <NumberSetting label="招呼语最大 token" value={config.ai?.greeting_max_tokens} onChange={v => updateConfig('ai.greeting_max_tokens', v)} min={100} max={32000} />
        <NumberSetting label="招呼语审阅最大 token" value={config.ai?.greeting_review_max_tokens} onChange={v => updateConfig('ai.greeting_review_max_tokens', v)} min={100} max={32000} />
        <NumberSetting label="招呼语最多重试次数" value={config.ai?.greeting_max_attempts} onChange={v => updateConfig('ai.greeting_max_attempts', v)} min={1} max={10} />
        <NumberSetting label="招呼语审阅阈值" value={config.ai?.greeting_review_threshold} onChange={v => updateConfig('ai.greeting_review_threshold', v)} min={0} max={100}
          hint="分数不低于它的岗位才需要你审阅招呼语" />
      </div>
    </SubCard>
  )
}

export function BrowserSettings({ config, updateConfig }: { config: Config; updateConfig: Update }) {
  const chromePorts = Array.isArray(config.browser?.chrome_ports) ? config.browser.chrome_ports.map(String) : []
  const sitePatterns = Array.isArray(config.browser?.site_patterns) ? config.browser.site_patterns : []
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <SettingField label="代理主机" restart>
          <Input aria-label="代理主机" value={config.browser?.proxy_host || ''} onChange={e => updateConfig('browser.proxy_host', e.target.value)} placeholder="127.0.0.1" />
        </SettingField>
        <NumberSetting label="代理端口" restart value={config.browser?.proxy_port} onChange={v => updateConfig('browser.proxy_port', v)} min={1} max={65535} />
      </div>
      <SettingField label="Chrome 调试端口" restart hint="专用 Chrome 的远程调试端口，默认 9222">
        <TagsInput value={chromePorts} onChange={value => updateConfig('browser.chrome_ports', value.map(item => Number(item)).filter(port => Number.isInteger(port) && port > 0 && port < 65536))} placeholder="输入端口后回车" />
      </SettingField>
      <SettingField label="站点匹配规则" restart hint="只在这些站点的标签页上操作">
        <TagsInput value={sitePatterns} onChange={value => updateConfig('browser.site_patterns', value)} placeholder="如：zhipin.com" />
      </SettingField>
      <ToggleSetting restart label="自动启动浏览器代理" checked={config.browser?.auto_start_proxy ?? true} onChange={v => updateConfig('browser.auto_start_proxy', v)} />
      <ToggleSetting restart label="启用端口保护" checked={config.browser?.enable_port_guard ?? true} onChange={v => updateConfig('browser.enable_port_guard', v)}
        hint="拒绝连到不是专用 Chrome 的端口，避免误操作日常浏览器。" />
    </div>
  )
}

/** Warns when this computer's clock is not on Shanghai time: the collection and sending windows follow it. */
export function TimezoneNotice() {
  let zone = ''
  try {
    zone = Intl.DateTimeFormat().resolvedOptions().timeZone
  } catch {
    zone = ''
  }
  const shanghai = zone === 'Asia/Shanghai' || zone === 'Asia/Chongqing' || zone === 'Asia/Harbin'
  const now = new Date().toLocaleTimeString('zh-CN', { hour12: false })
  return (
    <div className={`rounded-xl border px-3 py-2 text-xs leading-5 ${shanghai || !zone ? 'border-card-border bg-surface text-muted' : 'border-warning-border bg-warning-soft text-warning-strong'}`}>
      当前系统时区：{zone || '未知'}（现在 {now}）。
      {shanghai || !zone ? ' 采集窗口、发送窗口、每日循环都按这个时间计算。' : ' 它不是上海时间，采集窗口、发送窗口和每日循环的时间会错位，请把系统时区改成「中国标准时间」。'}
    </div>
  )
}
