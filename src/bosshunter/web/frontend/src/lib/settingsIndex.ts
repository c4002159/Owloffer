import type { ConfigCategoryKey } from '@/lib/configCategories'

export interface SettingEntry {
  /** Config path (a prefix also matches deeper keys); '' for entries that are only searchable. */
  path: string
  label: string
  category: ConfigCategoryKey
  /** Other words people may type for it. */
  aliases?: string[]
  /** Needs a restart of the program to apply. */
  restart?: boolean
  /** A change here deserves a warning in the save preview. */
  risky?: boolean
}

/** Every setting the page shows: used by the search box and to name changes in the save preview. */
export const SETTINGS_INDEX: SettingEntry[] = [
  // 求职档案
  { path: 'profile.resume_path', label: '默认简历', category: 'profile', aliases: ['简历', '简历文件', 'resume'] },
  { path: 'profile.resume_direction', label: '默认简历的适用方向', category: 'profile', aliases: ['方向简历', '适用方向'] },
  { path: 'profile.resumes', label: '方向简历', category: 'profile', aliases: ['简历文件', '适用方向'] },
  { path: 'profile.education', label: '最高学历', category: 'profile', aliases: ['学历'] },
  { path: 'profile.recruitment_type', label: '求职招聘类型', category: 'profile', aliases: ['校招', '社招', '招聘类型'] },
  { path: 'profile.ai_greeting_enabled', label: 'AI 生成招呼语', category: 'profile', aliases: ['打招呼', '招呼语'] },
  { path: 'profile.fixed_greeting', label: '固定招呼语', category: 'profile', aliases: ['打招呼', '招呼语'] },
  { path: 'profile.greeting_preference', label: '招呼语偏好', category: 'profile', aliases: ['打招呼', '语气'] },
  { path: 'profile.salary_min', label: '期望薪资下限（K）', category: 'profile', aliases: ['薪资', '工资'] },
  { path: 'profile.salary_max', label: '期望薪资上限（K）', category: 'profile', aliases: ['薪资', '工资'] },
  { path: 'profile.salary_ceil_ratio', label: '薪资上限放宽倍数', category: 'profile', aliases: ['薪资', '工资'] },
  { path: 'profile.filter_unparsed_salary', label: '过滤面议/无法解析薪资', category: 'profile', aliases: ['面议', '薪资'] },
  { path: 'profile.deal_breakers', label: '排除关键词', category: 'profile', aliases: ['外包', '996', '黑名单'] },
  { path: 'profile.jd_deal_breakers', label: 'JD 排除关键词', category: 'profile', aliases: ['出差', '黑名单'] },
  { path: 'profile.blocked_companies', label: '屏蔽公司', category: 'profile', aliases: ['黑名单', '公司'] },
  { path: 'profile.allow_internship', label: '接受实习/管培岗位', category: 'profile', aliases: ['实习', '管培'] },
  { path: 'profile.max_job_age_days', label: '岗位最大天数', category: 'profile', aliases: ['新鲜度', '过期', '多久'] },
  { path: 'profile.hr_active_min_level', label: '招聘者活跃度下限', category: 'profile', aliases: ['活跃', 'HR'] },
  { path: 'profile.hr_active_unknown_pass', label: '活跃度未知的岗位也放行', category: 'profile', aliases: ['活跃', 'HR'] },
  { path: 'profile.resume_output_dir', label: '定制简历输出目录', category: 'profile', aliases: ['目录', '路径'] },
  { path: 'automation.company_cooldown_days', label: '同公司冷却天数', category: 'profile', aliases: ['冷却', '重复投递', '同一家公司'] },
  { path: 'automation.zombie_days', label: '僵尸岗位天数', category: 'profile', aliases: ['没回复', '僵尸'] },
  // 搜索与关键词
  { path: 'search.intern_keywords', label: '实习关键词', category: 'search', aliases: ['实习'] },
  { path: 'search_common', label: '全平台通用搜索设置', category: 'search', aliases: ['通用', '所有平台', '应用到所有', '城市', '页数'] },
  { path: 'keywords', label: '搜索关键词', category: 'search', aliases: ['关键词', '岗位', 'AI 推荐'] },
  { path: 'platforms', label: '启用平台 / 各平台搜索设置', category: 'search', aliases: ['BOSS', '智联', '前程无忧', '猎聘', '校招', '平台'] },
  { path: 'collection.default_order', label: '默认执行顺序', category: 'search', aliases: ['平台顺序', '先后'] },
  { path: 'collection.auto_score_default', label: '采集后自动评分', category: 'search', aliases: ['评分'] },
  // 评分与过滤
  { path: 'scoring.threshold', label: '通过阈值', category: 'scoring', aliases: ['评分', '分数', '及格'] },
  { path: 'scoring.max_candidates', label: '每轮最大候选数', category: 'scoring', aliases: ['评分', '数量'] },
  { path: 'scoring.min_match_score', label: '最低匹配分', category: 'scoring', aliases: ['评分', '分数'] },
  { path: 'scoring.min_activity_score', label: '最低活跃分', category: 'scoring', aliases: ['评分', '分数', '活跃'] },
  { path: 'automation.review_min_score', label: '进入审查的最低分', category: 'scoring', aliases: ['评分', '分数', '待确认'] },
  // 投递与自动化
  { path: 'automation.auto_send', label: '自动投递（无需逐个确认）', category: 'delivery', aliases: ['自动发送', '确认'], risky: true },
  { path: 'automation.review_cap', label: '待审查/待发送岗位上限', category: 'delivery', aliases: ['上限', '审查'] },
  { path: 'automation.keyword_budget', label: '关键词预算', category: 'delivery', aliases: ['关键词', '名额'] },
  { path: 'automation.stream_scoring', label: '边采边评（采集时同时评分）', category: 'delivery', aliases: ['并发', '评分', '同时', '速度'] },
  { path: 'automation.saturation_stop', label: '饱和检测（没有新岗位的词不再往下读）', category: 'delivery', aliases: ['饱和', '翻页', '没有新岗位', '采集慢'] },
  { path: 'automation.saturation_chunk_pages', label: '饱和检测：每段读几页', category: 'delivery', aliases: ['饱和', '翻页'] },
  { path: 'automation.saturation_min_seen', label: '饱和检测：一段至少看到多少个岗位才判断', category: 'delivery', aliases: ['饱和'] },
  { path: 'automation.daily_loop_collect_minutes', label: '每日循环：单次采集最长（分钟）', category: 'delivery', aliases: ['90分钟', '采集时间', '超时', '每日循环'] },
  { path: 'automation.daily_loop_monitor_minutes', label: '每日循环：每隔多久检查一次 BOSS 的 HR 回复（分钟，0 = 不检查）', category: 'delivery', aliases: ['回复', '监测', 'HR', '消息', '提醒'] },
  { path: 'automation.daily_loop_deliver_minutes', label: '每日循环：单次投递最长（分钟）', category: 'delivery', aliases: ['240分钟', '投递时间', '超时', '每日循环'] },
  { path: 'automation.page_frontier', label: '页数记忆（头部 + 深层窗口）', category: 'delivery', aliases: ['翻页', '页数', '深层'] },
  { path: 'throttle.daily_limit', label: '每日发送上限', category: 'delivery', aliases: ['投递上限', '每天', '额度'], risky: true },
  { path: 'throttle.interval_min', label: '发送间隔下限（秒）', category: 'delivery', aliases: ['间隔', '频率'], risky: true },
  { path: 'throttle.interval_max', label: '发送间隔上限（秒）', category: 'delivery', aliases: ['间隔', '频率'], risky: true },
  { path: 'throttle.browse_before_greet', label: '发送前模拟浏览', category: 'delivery', aliases: ['浏览', '节奏'] },
  { path: 'throttle.browse_duration_min', label: '模拟浏览时长下限（秒）', category: 'delivery', aliases: ['浏览', '节奏'] },
  { path: 'throttle.browse_duration_max', label: '模拟浏览时长上限（秒）', category: 'delivery', aliases: ['浏览', '节奏'] },
  { path: 'throttle.send_windows', label: '发送时间窗口', category: 'delivery', aliases: ['发送窗口', '时间段', '几点', '时间'], risky: true },
  { path: 'throttle.day_off_probability', label: '随机休息概率', category: 'delivery', aliases: ['休息', '节奏'] },
  // 监测与跟进
  { path: 'monitor.interval', label: '检查间隔（分钟）', category: 'monitor', aliases: ['监测', '回复', 'HR'] },
  { path: 'monitor.initial_cooldown_minutes', label: '全流程首次监测冷却（分钟）', category: 'monitor', aliases: ['监测'] },
  { path: 'monitor.chat_url', label: '聊天页 URL', category: 'monitor', aliases: ['监测', '地址'] },
  { path: 'monitor.max_conversations_per_cycle', label: '每轮最多处理对话数', category: 'monitor', aliases: ['监测'] },
  { path: 'monitor.max_consecutive_page_failures', label: '监测连续页面失败停止阈值', category: 'monitor', aliases: ['监测', '失败'] },
  { path: 'monitor.max_resume_sends_per_cycle', label: '每轮最多发简历数', category: 'monitor', aliases: ['监测', '简历'] },
  { path: 'monitor.auto_reply_hr_questions', label: '自动回复 HR 常见问题', category: 'monitor', aliases: ['自动回复', 'HR'], risky: true },
  { path: 'follow_up.enabled', label: '启用自动跟进', category: 'monitor', aliases: ['跟进', '催'] },
  { path: 'follow_up.interval_hours', label: '跟进间隔（小时）', category: 'monitor', aliases: ['跟进'] },
  { path: 'follow_up.skip_weekends', label: '跳过周末节假日', category: 'monitor', aliases: ['跟进', '周末'] },
  // 安全与反监测
  { path: 'collection.daily_search_page_limit', label: 'BOSS 单日搜索页上限', category: 'safety', aliases: ['风控', '页数', '封号'], risky: true },
  { path: 'collection.daily_detail_page_limit', label: 'BOSS 单日详情页尝试上限', category: 'safety', aliases: ['风控', '详情', '封号'], risky: true },
  { path: 'collection.max_consecutive_page_failures', label: 'BOSS 连续页面失败停止阈值', category: 'safety', aliases: ['风控', '失败'] },
  { path: 'collection.risk_pause_min_minutes', label: 'BOSS 风险暂停下限（分钟）', category: 'safety', aliases: ['风控', '暂停'] },
  { path: 'collection.risk_pause_max_minutes', label: 'BOSS 风险暂停上限（分钟）', category: 'safety', aliases: ['风控', '暂停'] },
  { path: 'collection.collection_delay_multiplier', label: 'BOSS 操作间隔倍率', category: 'safety', aliases: ['风控', '慢', '延迟'] },
  { path: 'collection.delivery_cooldown_min_minutes', label: 'BOSS 采集后投递冷却下限（分钟）', category: 'safety', aliases: ['冷却'] },
  { path: 'collection.delivery_cooldown_max_minutes', label: 'BOSS 采集后投递冷却上限（分钟）', category: 'safety', aliases: ['冷却'] },
  { path: 'safety.daily_platform_page_limit', label: 'BOSS 单日页面访问总上限', category: 'safety', aliases: ['风控', '页面'], risky: true },
  { path: 'collection.platform_limits.51job.daily_search_page_limit', label: '前程无忧 单日搜索页上限', category: 'safety', aliases: ['风控', '页数', '51job'], risky: true },
  { path: 'collection.platform_limits.liepin.daily_search_page_limit', label: '猎聘 单日搜索页上限', category: 'safety', aliases: ['风控', '页数'], risky: true },
  { path: 'collection.platform_limits.zhilian.daily_search_page_limit', label: '智联招聘 单日搜索页上限', category: 'safety', aliases: ['风控', '页数'], risky: true },
  { path: 'collection.platform_limits.zhilian_campus.daily_search_page_limit', label: '智联校招 单日搜索页上限', category: 'safety', aliases: ['风控', '页数', '校园'], risky: true },
  { path: 'collection.platform_pacing.51job', label: '前程无忧 采集节奏倍率', category: 'safety', aliases: ['风控', '慢', '间隔', '节奏', '51job'] },
  { path: 'collection.platform_pacing.liepin', label: '猎聘 采集节奏倍率', category: 'safety', aliases: ['风控', '慢', '间隔', '节奏'] },
  { path: 'collection.platform_pacing.zhilian', label: '智联招聘 采集节奏倍率', category: 'safety', aliases: ['风控', '慢', '间隔', '节奏', '限流'] },
  { path: 'collection.platform_pacing.zhilian_campus', label: '智联校招 采集节奏倍率', category: 'safety', aliases: ['风控', '慢', '间隔', '节奏', '校园'] },
  { path: 'safety.risk_lock_minutes', label: '风险锁定时长（分钟）', category: 'safety', aliases: ['风控', '锁定'] },
  // 智能体
  { path: '', label: '智能体令牌 / 权限 / 连接 Claude 和 Codex', category: 'agent', aliases: ['MCP', 'Claude', 'Codex', '令牌', 'token', '远程'] },
  // 系统
  { path: 'ai.service', label: 'AI 提供商', category: 'system', aliases: ['AI', '模型服务', 'Claude', 'DeepSeek', '豆包'] },
  { path: 'ai.provider', label: 'AI 协议', category: 'system', aliases: ['AI'] },
  { path: 'ai.model', label: 'AI 模型名称', category: 'system', aliases: ['AI', '模型'] },
  { path: 'ai.base_url', label: 'AI Base URL', category: 'system', aliases: ['AI', '地址'] },
  { path: 'ai.api_key', label: 'AI API Key', category: 'system', aliases: ['AI', '密钥', 'key', '令牌'] },
  { path: 'ai.thinking', label: 'AI Thinking 模式', category: 'system', aliases: ['AI', '思考'] },
  { path: 'ai.thinking_budget', label: 'AI Thinking 预算 Token', category: 'system', aliases: ['AI', '思考'] },
  { path: 'ai.timeout_seconds', label: 'AI 请求超时（秒）', category: 'system', aliases: ['AI', '超时'] },
  { path: 'ai.scoring_concurrency', label: 'AI 评分并发数', category: 'system', aliases: ['AI', '评分', '并发', '速度'] },
  { path: 'ai.scoring_second_review', label: 'AI 评分二次复核', category: 'system', aliases: ['AI', '评分'] },
  { path: 'ai.greeting_style_suggestions', label: '招呼语质量提醒', category: 'system', aliases: ['AI', '招呼语'] },
  { path: 'ai.scoring_max_tokens', label: '评分最大 token', category: 'system', aliases: ['AI', '评分'] },
  { path: 'ai.scoring_max_attempts', label: '评分最多重试次数', category: 'system', aliases: ['AI', '评分', '重试'] },
  { path: 'ai.greeting_max_tokens', label: '招呼语最大 token', category: 'system', aliases: ['AI', '招呼语'] },
  { path: 'ai.greeting_review_max_tokens', label: '招呼语审阅最大 token', category: 'system', aliases: ['AI', '招呼语'] },
  { path: 'ai.greeting_max_attempts', label: '招呼语最多重试次数', category: 'system', aliases: ['AI', '招呼语', '重试'] },
  { path: 'ai.greeting_review_threshold', label: '招呼语审阅阈值', category: 'system', aliases: ['AI', '招呼语'] },
  { path: 'browser.proxy_host', label: '浏览器代理主机', category: 'system', aliases: ['代理', 'Chrome'], restart: true },
  { path: 'browser.proxy_port', label: '浏览器代理端口', category: 'system', aliases: ['代理', 'Chrome'], restart: true },
  { path: 'browser.chrome_ports', label: 'Chrome 调试端口', category: 'system', aliases: ['9222', '代理', 'Chrome'], restart: true },
  { path: 'browser.site_patterns', label: '站点匹配规则', category: 'system', aliases: ['浏览器', '网站'], restart: true },
  { path: 'browser.auto_start_proxy', label: '自动启动浏览器代理', category: 'system', aliases: ['浏览器', '代理'], restart: true },
  { path: 'browser.enable_port_guard', label: '启用端口保护', category: 'system', aliases: ['浏览器', '端口'], restart: true },
  { path: '', label: '系统时区', category: 'system', aliases: ['时间', '上海', '北京时间'] },
  { path: 'dedup.history_file', label: '历史记录文件路径', category: 'system', aliases: ['去重', '记录'] },
  { path: '', label: '数据备份', category: 'system', aliases: ['备份', '恢复', '导出'] },
  { path: '', label: '运行日志', category: 'system', aliases: ['日志', 'log', '报错', '诊断'] },
]

/** The entry that names a changed config path (the longest matching prefix wins). */
export function entryForPath(path: string): SettingEntry | undefined {
  let found: SettingEntry | undefined
  for (const entry of SETTINGS_INDEX) {
    if (!entry.path) continue
    if (path === entry.path || path.startsWith(`${entry.path}.`)) {
      if (!found || entry.path.length > found.path.length) found = entry
    }
  }
  return found
}

/** Entries matching what the user typed: label, aliases and category words, best matches first. */
export function searchSettings(query: string, limit = 8): SettingEntry[] {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean)
  if (!words.length) return []
  const scored: Array<{ entry: SettingEntry; score: number }> = []
  for (const entry of SETTINGS_INDEX) {
    const label = entry.label.toLowerCase()
    const aliases = (entry.aliases ?? []).map(item => item.toLowerCase())
    let score = 0
    let hit = true
    for (const word of words) {
      if (label === word) score += 10
      else if (label.startsWith(word)) score += 6
      else if (label.includes(word)) score += 4
      else if (aliases.some(alias => alias === word)) score += 3
      else if (aliases.some(alias => alias.includes(word))) score += 2
      else if (entry.path.toLowerCase().includes(word)) score += 1
      else hit = false
    }
    if (hit) scored.push({ entry, score })
  }
  return scored.sort((a, b) => b.score - a.score).slice(0, limit).map(item => item.entry)
}
