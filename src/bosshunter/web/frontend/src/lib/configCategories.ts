/** The groups of the settings page; the sidebar lists them under "配置". */
export const CONFIG_CATEGORIES = [
  { key: 'profile', label: '求职档案', hint: '简历文件、个人信息、求职偏好、过滤规则', sections: ['profile'] },
  { key: 'search', label: '搜索与关键词', hint: '各平台的关键词、城市、页数、筛选', sections: ['keywords', 'search'] },
  { key: 'scoring', label: '评分与过滤', hint: '评分阈值与分数门槛', sections: ['scoring'] },
  { key: 'delivery', label: '投递与自动化', hint: '自动投递、发送额度、发送窗口', sections: ['delivery'] },
  { key: 'monitor', label: '监测与跟进', hint: 'HR 回复检查、自动跟进', sections: ['monitor', 'follow_up'] },
  { key: 'safety', label: '安全与反监测', hint: '页面上限、风险暂停、采集节奏', sections: ['collection'] },
  { key: 'agent', label: '智能体连接', hint: 'Claude / Codex 的令牌、权限、连接方式', sections: ['agent_access'] },
  { key: 'system', label: '系统', hint: 'AI 服务、浏览器、时区、去重', sections: ['ai', 'browser', 'dedup'] },
] as const

export type ConfigCategoryKey = (typeof CONFIG_CATEGORIES)[number]['key']

/** Old ``?section=`` values (and section keys) mapped to their category. */
export const SECTION_TO_CATEGORY: Record<string, ConfigCategoryKey> = {
  profile: 'profile', search: 'search', keywords: 'search', scoring: 'scoring', delivery: 'delivery', throttle: 'delivery', monitor: 'monitor', follow_up: 'monitor',
  collection: 'safety', safety: 'safety', agent_access: 'agent', agent: 'agent', ai: 'system', browser: 'system', dedup: 'system', system: 'system',
}

export const CONFIG_CATEGORY_EVENT = 'bosshunter-config-category'

export function categoryFromSection(value: string | null | undefined): ConfigCategoryKey {
  return SECTION_TO_CATEGORY[value || ''] ?? 'profile'
}
