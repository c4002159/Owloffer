import { useCallback, useEffect, useState, type ChangeEvent } from 'react'
import { ExternalLink, Loader2, Plus, Trash2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

interface DirectionResume {
  path: string
  filename: string
  size_label?: string
  uploaded_at?: string
  direction: string
  has_original_pdf?: boolean
  original_preview_url?: string
}

/** Extra resumes tied to a job direction; a job uses the one whose direction matches, else the default resume. */
export function ExtraResumesSection() {
  const [resumes, setResumes] = useState<DirectionResume[]>([])
  const [directions, setDirections] = useState<string[]>([])
  const [newDirection, setNewDirection] = useState('')
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    try {
      const res = await fetch('/api/resumes', { cache: 'no-store' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '读取方向简历失败')
      setResumes(data.resumes || [])
      setDirections(data.directions || [])
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '读取方向简历失败')
    }
  }, [])

  useEffect(() => { void load() }, [load])

  const upload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    setUploading(true)
    setError('')
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('direction', newDirection.trim())
      const res = await fetch('/api/resumes/upload', { method: 'POST', body: form })
      const data = await res.json()
      if (!res.ok || !data.success) throw new Error(data.error || '简历上传失败')
      setNewDirection('')
      await load()
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '简历上传失败')
    } finally {
      setUploading(false)
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
      setResumes(prev => prev.map(item => (item.path === resume.path ? { ...item, direction: direction.trim() } : item)))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '保存方向失败')
    }
  }

  const remove = async (resume: DirectionResume) => {
    setError('')
    try {
      const res = await fetch(`/api/resumes?path=${encodeURIComponent(resume.path)}`, { method: 'DELETE' })
      const data = await res.json()
      if (!res.ok) throw new Error(data.error || '删除失败')
      setResumes(prev => prev.filter(item => item.path !== resume.path))
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '删除失败')
    }
  }

  return (
    <div className="mt-4">
      <label className="mb-1 block text-xs text-foreground">方向简历（可添加多份）</label>
      <p className="mb-2 text-xs text-muted">
        给不同岗位方向各放一份简历。岗位的方向和某份简历的方向一致（或岗位标题含该方向词）时，评分、招呼语和定制简历都用这一份；没有匹配时用上面的默认简历。
      </p>
      <datalist id="resume-direction-options">{directions.map(name => <option key={name} value={name} />)}</datalist>
      <div className="space-y-2">
        {resumes.map(resume => (
          <div key={resume.path} className="flex flex-wrap items-center gap-3 rounded-md border border-card-border bg-surface p-3">
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
            <Button type="button" variant="ghost" size="icon" onClick={() => void remove(resume)} aria-label="删除方向简历">
              <Trash2 className="h-4 w-4 text-danger" />
            </Button>
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <Input
          list="resume-direction-options"
          value={newDirection}
          onChange={event => setNewDirection(event.target.value)}
          placeholder="新简历的适用方向（可先留空，之后再改）"
          aria-label="新简历的适用方向"
          className="w-72"
        />
        <label className="inline-flex h-9 cursor-pointer items-center justify-center rounded-md border border-card-border bg-card px-4 text-sm font-medium text-foreground hover:bg-secondary">
          {uploading ? <Loader2 className="mr-1 h-4 w-4 animate-spin" /> : <Plus className="mr-1 h-4 w-4" />}
          添加方向简历
          <input type="file" accept=".md,.docx,.pdf,application/pdf" onChange={event => void upload(event)} className="hidden" disabled={uploading} />
        </label>
      </div>
      {error && <p className="mt-2 rounded-lg bg-danger-soft px-3 py-2 text-xs text-danger">{error}</p>}
    </div>
  )
}
