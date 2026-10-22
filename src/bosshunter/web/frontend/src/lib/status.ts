export const STATUS_LABELS: Record<string, string> = {
  pending: '待评分',
  scored: '已评分',
  filtered: '已过滤',
  ready: '待确认',
  approved: '已确认',
  skipped: '已跳过',
  sent: '已发送',
  replied: '已回复',
  resume_sent: '简历已发',
  needs_resume: '待手动发简历',
  follow_up_sent: '已跟进',
  rejected: '已拒绝',
  error: '发送失败',
}

export const ACTION_LABELS: Record<string, string> = {
  status_changed: '手动修改状态',
  scrape: '采集',
  scored: '评分',
  filtered: '过滤',
  ready: '待确认',
  approved: '确认',
  skipped: '跳过',
  sent: '发送',
  manual_sent: '手动标记已发送',
  replied: '回复',
  hr_reply_detected: 'HR 有新消息',
  reply_pending: '待确认回复',
  reply_dismissed: '已放弃回复',
  auto_replied: '自动回复',
  resume_failed: '简历生成失败',
  resume_failed_dismissed: '已放弃简历失败',
  resume_sent: '简历已发',
  needs_resume: '待手动发简历',
  follow_up_sent: '已跟进',
  rejected: '拒绝',
  error: '错误',
}

export function getStatusLabel(status: string | null | undefined): string {
  if (!status) return '未知'
  return STATUS_LABELS[status] ?? status
}

export function getActionLabel(action: string | null | undefined): string {
  if (!action) return '未知'
  return ACTION_LABELS[action] ?? action
}

/** 岗位池分成两个视图：还要处理的，和已经有结果的历史记录。 */
export type PoolView = 'pending' | 'waiting' | 'failed' | 'manual' | 'history'

export const POOL_VIEW_STATUSES: Record<PoolView, string[]> = {
  // 待评分、已评分、待确认
  pending: ['pending', 'scored', 'ready'],
  // 已确认（或招呼语已备好）、等待投递
  waiting: ['approved', 'ready'],
  // 发送失败（需要重试或人工投递）
  failed: ['error'],
  // 只能手动投递的岗位（服务端按公司去重后列出）加上程序投递失败、要手动处理的岗位
  manual: ['pending', 'scored', 'ready', 'approved', 'error'],
  // 已投递、已回复、简历已发、待手动发简历、已跟进、已拒绝、已过滤、已跳过
  history: ['sent', 'replied', 'resume_sent', 'needs_resume', 'follow_up_sent', 'rejected', 'filtered', 'skipped'],
}

// 同一个状态可能属于两个视图（ready 有无招呼语），服务端按 queue_view 区分。
export const POOL_VIEW_QUEUE: Record<PoolView, string | null> = { pending: 'confirm', waiting: 'waiting', failed: 'failed', manual: 'manual', history: null }

/** The tabs of the job pool (failed deliveries are part of the manual tab). */
export const POOL_TABS: PoolView[] = ['pending', 'waiting', 'manual', 'history']

export const POOL_VIEW_LABELS: Record<PoolView, string> = {
  pending: '待确认',
  waiting: '待投递',
  failed: '投递失败',
  manual: '手动投递',
  history: '历史投递记录',
}
