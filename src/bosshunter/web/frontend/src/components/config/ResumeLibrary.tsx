import { useCallback, useEffect, useState, type ChangeEvent, type DragEvent } from 'react'
import { ChevronDown, ExternalLink, Loader2, Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { ResumeDualPreview } from '@/components/config/ResumeDualPreview'
import { resumeSizeLabel, shouldSyncResumePath, type ResumeInfo } from '@/lib/resumeDisplay'

interface DirectionResume {
  path: string
  filename: string
  size_label?: string
  uploaded_at?: string
  direction: string
  has_original_pdf?: boolean
  original_preview_url?: string
}

interface ResumeLibraryProps {
  currentResumePath: string
  updateConfig: (path: string, value: unknown, options?: { markDirty?: boolean }) => void
}

/**
 * All resume files in one list: the default resume (used when no direction matches) and the resumes tied to a job direction.
 * Every row looks the same; the default one is marked and has no direction.
 */
export function ResumeLibrary({ currentResumePath, updateConfig }: ResumeLibraryProps) {
  const [main, setMain] = useState<ResumeInfo | null>(null)
  const [extras, setExtras] = useState<DirectionResume[]>([])
  const [directions, setDirections] = useState<string[]>([])
  const [newDirection, setNewDirection] = useState('')
  const [uploading, setUploading] = useState(false)
  const [dragActive, setDragActive] = useState(false)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [error, setError] = useState('')

  const loadMain = useCallback(async () => {
    try {
      const res = await fetch('/api/resume')
      const data = await res.json().catch(() => null)
      if (res.ok && data && data.filename) {
        setMain(data)
        if (shouldSyncResumePath(currentResumePath, data.path)) updateConfig('profile.resume_path', data.path, { markDirty: false })
      } else if (res.ok) {
        setMain(null)
      } else {
        setError((data && data.error) || '读取简历失败')
      }
    } catch {
      setError('网络错误，无法读取简历')
    }
  }, [currentResumePath, updateConfig])

  const loadExtras = useCallback(async () => {
    try {
      const res = await fetch('/api/resumes', { cache: 'no-store' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '读取方向简历失败')
      setExtras(data.resumes || [])
      setDirections(data.directions || [])
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '读取方向简历失败')
    }
  }, [])

  useEffect(() => { void loadMain() }, [loadMain])
  useEffect(() => { void loadExtras() }, [loadExtras])

  useEffect(() => {
    // a file dropped next to the list must not make the browser open it
    const stop = (event: globalThis.DragEvent) => event.preventDefault()
    window.addEventListener('dragover', stop)
    window.addEventListener('drop', stop)
    return () => {
      window.removeEventListener('dragover', stop)
      window.removeEventListener('drop', stop)
    }
  }, [])

  const upload = async (file: File) => {
    setUploading(true)
    setError('')
    const direction = newDirection.trim()
    const asDefault = !main && !direction                      // the first file without a direction becomes the default resume
    try {
      const form = new FormData()
      form.append('file', file)
      if (!asDefault) form.append('direction', direction)
      const res = await fetch(asDefault ? '/api/resume/upload' : '/api/resumes/upload', { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok || !data.success) throw new Error(data.error || '简历上传失败')
      if (asDefault) {
        const { success: _success, ...info } = data
        setMain(info as ResumeInfo)
        updateConfig('profile.resume_path', data.path, { markDirty: false })
      } else {
        await loadExtras()
      }
      setNewDirection('')
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '简历上传失败')
    } finally {
      setUploading(false)
    }
  }

  const onPick = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (file) void upload(file)
  }

  const onDrop = (event: DragEvent<HTMLElement>) => {
    event.preventDefault()
    setDragActive(false)
    const file = event.dataTransfer.files?.[0]
    if (file) void upload(file)
  }

  const removeMain = async () => {
    setError('')
    try {
      const res = await fetch('/api/resume', { method: 'DELETE' })
      const data = await res.json().catch(() => null)
      if (!res.ok || !data?.success) throw new Error((data && data.error) || '删除简历失败')
      setMain(null)
      setPreviewOpen(false)
      updateConfig('profile.resume_path', '', { markDirty: false })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '删除简历失败')
    }
  }

  const saveDirection = async (resume: DirectionResume, direction: string) => {
    if (direction.trim() === resume.direction) return
    setError('')
    try {
      const res = await fetch('/api/resumes', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: resume.path, direction }),
      })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '保存方向失败')
      setExtras(prev => prev.map(item => (item.path === resume.path ? { ...item, direction: direction.trim() } : item)))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存方向失败')
    }
  }

  const removeExtra = async (resume: DirectionResume) => {
    setError('')
    try {
      const res = await fetch(`/api/resumes?path=${encodeURIComponent(resume.path)}`, { method: 'DELETE' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '删除失败')
      setExtras(prev => prev.filter(item => item.path !== resume.path))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '删除失败')
    }
  }

  const rowClass = 'flex flex-wrap items-center gap-3 rounded-md border border-card-border bg-surface p-3'
  return (
    <div
      data-tour="resume-upload"
      className={`space-y-3 rounded-2xl border bg-surface p-4 transition-colors ${dragActive ? 'border-primary' : 'border-primary/30'}`}
      onDragOver={event => { event.preventDefault(); setDragActive(true) }}
      onDragLeave={() => setDragActive(false)}
      onDrop={onDrop}
    >
      <div>
        <h3 className="text-sm font-black text-foreground">简历文件</h3>
        <p className="mt-1 text-xs leading-5 text-muted">
          可以放很多份。给文件填上「适用方向」后，岗位的方向和它一致（或岗位标题含该方向词）时，评分、招呼语和定制简历就用这一份；没有匹配的方向时用标为「默认」的那份。
        </p>
      </div>
      <datalist id="resume-direction-options">{directions.map(name => <option key={name} value={name} />)}</datalist>

      <div className="space-y-2">
        {main && (
          <div>
            <div className={rowClass}>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-bold text-foreground">{main.filename}</span>
                  <span className="shrink-0 rounded-full bg-secondary px-2 py-0.5 text-[10px] font-black text-primary">默认</span>
                </div>
                <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted">
                  <span>{resumeSizeLabel(main)}</span>
                  {main.uploaded_at && <span>上传于 {main.uploaded_at}</span>}
                </div>
              </div>
              <span className="w-56 text-xs text-muted">没有匹配方向时使用</span>
              {main.original_preview_url && (
                <a href={main.original_preview_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs font-bold text-primary hover:underline">
                  原 PDF<ExternalLink className="h-3 w-3" />
                </a>
              )}
              <button type="button" aria-expanded={previewOpen} onClick={() => setPreviewOpen(open => !open)} className="inline-flex items-center gap-1 text-xs font-bold text-primary hover:underline">
                预览<ChevronDown className={`h-3 w-3 transition-transform ${previewOpen ? 'rotate-180' : ''}`} />
              </button>
              <Button type="button" variant="ghost" size="icon" onClick={() => void removeMain()} aria-label="删除简历">
                <Trash2 className="h-4 w-4 text-danger" />
              </Button>
            </div>
            {previewOpen && <ResumeDualPreview key={`${main.path}:${main.cache_buster || ''}`} info={main} />}
          </div>
        )}
        {extras.map(resume => (
          <div key={resume.path} className={rowClass}>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-bold text-foreground">{resume.filename}</div>
              <div className="mt-1 flex flex-wrap gap-x-3 text-xs text-muted">
                {resume.size_label && <span>{resume.size_label}</span>}
                {resume.uploaded_at && <span>上传于 {resume.uploaded_at}</span>}
              </div>
            </div>
            <Input
              list="resume-direction-options"
              defaultValue={resume.direction}
              placeholder="适用方向，如：Agent / AI应用"
              aria-label={`${resume.filename} 的适用方向`}
              className="w-56"
              onBlur={event => void saveDirection(resume, event.target.value)}
            />
            {resume.original_preview_url && (
              <a href={resume.original_preview_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs font-bold text-primary hover:underline">
                原 PDF<ExternalLink className="h-3 w-3" />
              </a>
            )}
            <Button type="button" variant="ghost" size="icon" onClick={() => void removeExtra(resume)} aria-label="删除方向简历">
              <Trash2 className="h-4 w-4 text-danger" />
            </Button>
          </div>
        ))}
        {!main && !extras.length && <p className="rounded-md border border-dashed border-card-border px-3 py-6 text-center text-xs text-muted">还没有简历：点下面的「添加简历文件」，或把文件拖到这里（.md、.docx、.pdf）。</p>}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <Input
          list="resume-direction-options"
          value={newDirection}
          onChange={event => setNewDirection(event.target.value)}
          placeholder={main ? '新文件的适用方向（可先留空，之后再改）' : '适用方向（留空则作为默认简历）'}
          aria-label="新简历的适用方向"
          className="w-72"
        />
        <label className="inline-flex h-9 cursor-pointer items-center justify-center rounded-md border border-card-border bg-card px-4 text-sm font-medium text-foreground hover:bg-secondary">
          {uploading ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Plus className="mr-1 h-4 w-4" />}
          添加简历文件
          <input type="file" accept=".md,.docx,.pdf,application/pdf" onChange={onPick} className="hidden" disabled={uploading} />
        </label>
      </div>
      {error && <p className="rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger">{error}</p>}
    </div>
  )
}
