import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { useKeywordAdvice, type KeywordAdvice } from '@/hooks/useKeywordAdvice'
import { useKeywordTrials } from '@/hooks/useKeywordTrials'

const KIND_LABEL = { pause: '暂停 7 天', add: '新增关键词' } as const

type Advisor = ReturnType<typeof useKeywordAdvice>

/** The reasons and the suggestions with a tick box each; "一键应用" applies the ticked ones, "撤销" takes the last application back. */
function AdviceBody({ advice, advisor, selected, setSelected }: { advice: KeywordAdvice; advisor: Advisor; selected: string[]; setSelected: (ids: string[]) => void }) {
  const toggle = (id: string) => setSelected(selected.includes(id) ? selected.filter(item => item !== id) : [...selected, id])
  return (
    <div className="space-y-3 text-sm">
      <ul className="space-y-1 text-muted">
        {advice.reasons.map(reason => <li key={reason.code}>· {reason.message}</li>)}
      </ul>
      {advice.suggestions.length > 0 && (
        <ul className="space-y-1.5">
          {advice.suggestions.map(item => (
            <li key={item.id} className="flex items-start gap-2 rounded-xl border border-card-border bg-surface px-3 py-2">
              <input type="checkbox" className="mt-1" checked={selected.includes(item.id)} onChange={() => toggle(item.id)} aria-label={`${KIND_LABEL[item.kind]}：${item.keyword}`} />
              <span className="min-w-0">
                <span className="font-black text-foreground">{item.keyword}</span>
                <span className="ml-2 rounded-full bg-secondary px-2 py-0.5 text-xs font-bold text-primary">{KIND_LABEL[item.kind]}</span>
                <span className="mt-0.5 block text-xs text-muted">{item.reason}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
      {advisor.error && <p className="text-xs font-bold text-danger">{advisor.error}</p>}
    </div>
  )
}

function useSelection(advice: KeywordAdvice | null) {
  const ids = (advice?.suggestions ?? []).map(item => item.id).join('|')
  const [selected, setSelected] = useState<string[]>([])
  useEffect(() => setSelected(ids ? ids.split('|') : []), [ids])
  return [selected, setSelected] as const
}

const RESULT_LABEL = { kept: '保留', dropped: '已移除', removed: '你移除了' } as const

/** The role names the program is trying as keywords by itself, and how the earlier trials ended. */
function KeywordTrialsBlock() {
  const { trials, busy, runNow } = useKeywordTrials()
  if (!trials || !Array.isArray(trials.active) || !Array.isArray(trials.history)) return null          // an answer of another shape (an old server) shows nothing
  return (
    <div className="space-y-1.5 border-t border-card-border pt-3 text-xs" aria-label="岗位名称试用">
      <div className="flex items-center gap-2">
        <span className="text-sm font-black text-foreground">岗位名称试用</span>
        <span className="text-muted">{trials.slots > 0 ? `同时试 ${trials.slots} 个：采到的岗位里常见、但关键词没搜过的名称，效果好的留下，不好的自动移除` : '已关闭（设置里把试用名额改成大于 0 即可开启）'}</span>
        {trials.slots > 0 && <Button size="sm" variant="secondary" className="ml-auto" disabled={busy} onClick={() => void runNow()}>{busy ? '处理中…' : '现在评估一次'}</Button>}
      </div>
      {trials.active.length > 0 && <p className="text-muted">试用中：{trials.active.map(item => item.keyword).join('、')}</p>}
      {trials.history.length > 0 && (
        <ul className="space-y-0.5 text-muted">
          {trials.history.slice(-5).reverse().map(item => <li key={`${item.keyword}-${item.at}`}>· {item.keyword}：{RESULT_LABEL[item.result] ?? item.result}（{item.reason}）</li>)}
        </ul>
      )}
    </div>
  )
}

/** In the settings page: the same advice without a pop-up, plus undo and the pop-up switch. */
export function KeywordAdviceCard() {
  const advisor = useKeywordAdvice()
  const { advice } = advisor
  const [selected, setSelected] = useSelection(advice)
  if (!advice || !Array.isArray(advice.suggestions)) return null          // an answer without the lists (an old server) shows nothing
  const quiet = !advice.level3 && !advice.last_applied && advice.suggestions.length === 0
  return (
    <div className={`space-y-3 rounded-2xl border p-4 ${quiet ? 'border-card-border bg-surface' : 'border-warning-border bg-warning-soft'}`} aria-label="关键词优化建议">
      <div className={`text-sm font-black ${quiet ? 'text-foreground' : 'text-warning-strong'}`}>关键词优化建议</div>
      {quiet && <p className="text-xs text-muted">暂时没有建议。点「让模型扩展方向」，模型会根据你的简历和已采到的岗位，提出现有关键词没有覆盖的新方向和新说法，你确认后才会加入搜索。</p>}
      {(advice.level3 || advice.suggestions.length > 0) && <AdviceBody advice={advice} advisor={advisor} selected={selected} setSelected={setSelected} />}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" disabled={advisor.busy} onClick={() => void advisor.expand()}>{advisor.busy ? '处理中…' : '让模型扩展方向'}</Button>
        {advice.suggestions.length > 0 && (
          <Button size="sm" disabled={advisor.busy || !selected.length} onClick={() => void advisor.apply(selected)}>一键应用所选（{selected.length}）</Button>
        )}
        {advice.last_applied && <Button size="sm" variant="secondary" disabled={advisor.busy} onClick={() => void advisor.undo()}>撤销上次调整（{advice.last_applied.changes.length} 项）</Button>}
        <label className="ml-auto flex items-center gap-1.5 text-xs text-muted">
          <input type="checkbox" checked={advice.popup_enabled} onChange={event => void advisor.setPopup(event.target.checked)} />
          需要调整时在工作台弹窗提醒
        </label>
      </div>
      <KeywordTrialsBlock />
    </div>
  )
}

/** On every page: the pop-up that appears when the keywords need the user's decision (see keyword_advisor.py for when). */
export function KeywordAdvicePopup() {
  const advisor = useKeywordAdvice()
  const { advice } = advisor
  const [selected, setSelected] = useSelection(advice)
  if (!advice?.popup) return null
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 p-4" role="dialog" aria-modal="true" aria-label="关键词需要调整">
      <div className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-2xl border border-card-border bg-card p-6 shadow-xl">
        <h2 className="text-lg font-black text-foreground">关键词需要调整</h2>
        <p className="mb-3 mt-1 text-sm text-muted">最近的采集结果显示，现在的关键词需要调整（原因见下）。有建议时勾选后可一键应用，之后也能撤销。</p>
        <AdviceBody advice={advice} advisor={advisor} selected={selected} setSelected={setSelected} />
        <div className="mt-4 flex flex-wrap gap-2">
          {advice.expand_wanted && <Button size="sm" disabled={advisor.busy} onClick={() => void advisor.expand()}>{advisor.busy ? '模型思考中…' : '让模型扩展方向'}</Button>}
          {advice.suggestions.length > 0 && <Button size="sm" disabled={advisor.busy || !selected.length} onClick={() => void advisor.apply(selected)}>一键应用所选（{selected.length}）</Button>}
          <Button size="sm" variant="secondary" onClick={() => { window.location.href = `/config?section=search&focus=${encodeURIComponent('关键词设置')}` }}>去设置里改</Button>
          <Button size="sm" variant="secondary" disabled={advisor.busy} onClick={() => void advisor.snooze(24)}>24 小时后再提醒</Button>
        </div>
        <button type="button" className="mt-3 text-xs text-muted hover:text-foreground" onClick={() => void advisor.setPopup(false)}>以后不弹窗，只在设置页和消息中心提示</button>
      </div>
    </div>
  )
}
