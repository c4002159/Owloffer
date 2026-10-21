import { useCallback, useEffect, useState } from 'react'
import type { Job } from '@/hooks/useDashboard'
import { hasInvalidSalaryRange, useDebouncedValue, type JobFilters } from '@/lib/jobFilters'

interface JobSearchResponse {
  items: Job[]
  total: number
  all_total: number
  city_options?: string[]
  limit: number
  offset: number
}

export type JobSortKey = 'salary' | 'education' | 'score' | 'recruit_score' | 'status' | 'hr_active' | 'created_at'
export type JobSortOrder = 'asc' | 'desc'

export function useJobSearch(
  filters: JobFilters,
  page: number,
  pageSize: number,
  sortBy: JobSortKey = 'created_at',
  sortOrder: JobSortOrder = 'desc',
  hideHandled = false,
  viewStatuses: string[] | null = null,
  queueView: string | null = null,
) {
  const debouncedQuery = useDebouncedValue(filters.query, 250)
  const [items, setItems] = useState<Job[]>([])
  const [total, setTotal] = useState(0)
  const [allTotal, setAllTotal] = useState(0)
  const [cityOptions, setCityOptions] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  const refresh = useCallback(() => setRevision(value => value + 1), [])

  const buildParams = (limit: number, offset: number) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
    if (debouncedQuery.trim()) params.set('q', debouncedQuery.trim())
    if (filters.minScore) params.set('min_score', filters.minScore)
    if (filters.minActivity) params.set('min_activity', filters.minActivity)
    if (filters.salaryMin) params.set('salary_min', filters.salaryMin)
    if (filters.salaryMax) params.set('salary_max', filters.salaryMax)
    ;(filters.status.length ? filters.status : (viewStatuses ?? [])).forEach(value => params.append('status', value))
    if (filters.createdWithin) params.set('created_within', filters.createdWithin)
    filters.sourcePlatform.forEach(value => params.append('source_platform', value))
    filters.city.forEach(value => params.append('city', value))
    filters.education.forEach(value => params.append('education', value))
    filters.recruitmentType.forEach(value => params.append('recruitment_type', value))
    if (hideHandled) params.set('hide_handled', '1')
    if (queueView) params.set('queue_view', queueView)
    params.set('sort_by', sortBy)
    params.set('sort_order', sortOrder)
    return params
  }

  useEffect(() => {
    if (hasInvalidSalaryRange(filters)) {
      setItems([])
      setTotal(0)
      setLoading(false)
      setError('')
      return
    }

    const controller = new AbortController()
    const params = buildParams(pageSize, page * pageSize)

    setLoading(true)
    fetch(`/api/jobs/search?${params.toString()}`, { cache: 'no-store', signal: controller.signal })
      .then(async response => {
        const data = await response.json()
        if (!response.ok) throw new Error(data.error || '岗位筛选失败')
        return data as JobSearchResponse
      })
      .then(data => {
        if (controller.signal.aborted) return
        setItems(data.items)
        setTotal(data.total)
        setAllTotal(data.all_total)
        setCityOptions(data.city_options || [])
        setError('')
      })
      .catch(cause => {
        if (cause instanceof DOMException && cause.name === 'AbortError') return
        setError(cause instanceof Error ? cause.message : '岗位筛选失败')
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })

    return () => controller.abort()
  }, [debouncedQuery, filters.minScore, filters.minActivity, filters.salaryMin, filters.salaryMax, filters.status, filters.createdWithin, filters.sourcePlatform, filters.city, filters.education, filters.recruitmentType, page, pageSize, sortBy, sortOrder, hideHandled, viewStatuses, queueView, revision])

  // Every job id matching the current filters (not only the current page), for “选择全部”.
  const fetchAllIds = async (): Promise<string[]> => {
    const ids: string[] = []
    // The search endpoint takes at most 100 jobs per request.
    for (let offset = 0; offset < 100000; offset += 100) {
      const response = await fetch(`/api/jobs/search?${buildParams(100, offset).toString()}`, { cache: 'no-store' })
      const data = await response.json()
      if (!response.ok) throw new Error(data.error || '岗位筛选失败')
      ids.push(...(data.items as Job[]).map(job => job.id))
      if (!data.items.length || ids.length >= data.total) break
    }
    return ids
  }

  return { items, total, allTotal, cityOptions, loading, error, refresh, fetchAllIds }
}
