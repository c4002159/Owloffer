import { NavLink, useLocation } from 'react-router-dom'
import { BriefcaseBusiness, ChevronDown, Github, LayoutDashboard, Radar, Settings } from 'lucide-react'
import { useEffect, useState } from 'react'
import { BRAND_NAME, GITHUB_URL } from '@/lib/owl'
import { CONFIG_CATEGORIES, categoryFromSection } from '@/lib/configCategories'
import { OwlLogo } from './OwlLogo'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: '工作台' },
  { to: '/jobs', icon: BriefcaseBusiness, label: '岗位池' },
  { to: '/monitor', icon: Radar, label: '监测执行' },
]

interface SidebarProps {
  pendingReplies?: number
}

export function Sidebar({ pendingReplies: pendingRepliesProp }: SidebarProps) {
  const [pendingReplies, setPendingReplies] = useState(pendingRepliesProp ?? 0)
  const location = useLocation()
  const onConfig = location.pathname.startsWith('/config')
  const [configOpen, setConfigOpen] = useState(onConfig)
  const activeCategory = categoryFromSection(new URLSearchParams(location.search).get('section'))

  useEffect(() => {
    if (onConfig) setConfigOpen(true)
  }, [onConfig])

  useEffect(() => {
    if (pendingRepliesProp !== undefined) {
      setPendingReplies(pendingRepliesProp)
      return
    }

    const fetchPendingReplies = async () => {
      try {
        const res = await fetch('/api/history/unresolved-replies/count')
        const data = await res.json()
        setPendingReplies(Number(data.count) || 0)
      } catch {
        setPendingReplies(0)
      }
    }

    fetchPendingReplies()
    const interval = setInterval(fetchPendingReplies, 30000)
    return () => clearInterval(interval)
  }, [pendingRepliesProp])

  const linkClass = (isActive: boolean) =>
    `relative flex items-center justify-center gap-3 rounded-xl px-1 py-3 text-[10px] transition-colors md:justify-between md:px-3 md:text-sm ${
      isActive ? 'bg-secondary text-primary font-black' : 'text-muted hover:text-foreground hover:bg-surface'
    }`

  return (
    <aside className="flex w-16 shrink-0 flex-col border-r border-card-border bg-card md:w-60">
      <div className="flex h-16 shrink-0 items-center justify-center border-b border-card-border md:justify-start md:px-5">
        <div className="flex items-center gap-3">
          <OwlLogo size={40} />
          <div className="hidden md:block">
            <div className="font-black text-sm tracking-tight text-foreground">{BRAND_NAME}</div>
            <div className="text-[11px] text-muted">v2.4.0 · 本地控制台</div>
          </div>
        </div>
      </div>

      <nav className="flex-1 space-y-1 overflow-y-auto px-1.5 py-4 md:px-3">
        {navItems.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            data-tour={item.to === '/' ? 'nav-workbench' : undefined}
            className={({ isActive }) => linkClass(isActive)}
          >
            <span className="flex flex-col items-center gap-1 md:flex-row md:gap-3">
              <item.icon className="w-4 h-4" />
              {item.label}
            </span>
            {item.to === '/monitor' && pendingReplies > 0 && (
              <span className="absolute right-1 top-2 h-2 w-2 rounded-full bg-danger md:static" aria-label="有待处理事项" />
            )}
          </NavLink>
        ))}

        <div>
          <div className="relative">
            <NavLink to="/config" data-tour="nav-config" className={({ isActive }) => linkClass(isActive)}>
              <span className="flex flex-col items-center gap-1 md:flex-row md:gap-3">
                <Settings className="w-4 h-4" />
                配置
              </span>
            </NavLink>
            <button
              type="button"
              aria-label={configOpen ? '收起配置分类' : '展开配置分类'}
              aria-expanded={configOpen}
              onClick={() => setConfigOpen(open => !open)}
              className="absolute right-2 top-1/2 hidden -translate-y-1/2 rounded-md p-1 text-muted hover:bg-secondary hover:text-primary md:block"
            >
              <ChevronDown className={`h-4 w-4 transition-transform ${configOpen ? 'rotate-180' : ''}`} />
            </button>
          </div>
          {configOpen && (
            <div role="group" aria-label="配置分类" className="mt-1 ml-3 hidden space-y-0.5 border-l border-card-border pl-2 md:block">
              {CONFIG_CATEGORIES.map(category => {
                const active = onConfig && activeCategory === category.key
                return (
                  <NavLink
                    key={category.key}
                    to={`/config?section=${category.key}`}
                    data-tour={category.key === 'system' ? 'ai-section' : undefined}
                    aria-current={active ? 'page' : undefined}
                    title={category.hint}
                    className={`block rounded-lg px-3 py-2 text-xs transition-colors ${active ? 'bg-secondary font-black text-primary' : 'text-muted hover:bg-surface hover:text-foreground'}`}
                  >
                    {category.label}
                  </NavLink>
                )
              })}
            </div>
          )}
        </div>
      </nav>

      <div className="space-y-3 border-t border-card-border px-2 py-4 md:px-4">
        <a
          href={GITHUB_URL}
          target="_blank"
          rel="noreferrer"
          aria-label={`${BRAND_NAME} GitHub`}
          className="relative flex items-center justify-center rounded-xl border border-card-border bg-surface px-3 py-3 text-xs font-black text-foreground transition-colors hover:border-primary/60 hover:text-primary md:rounded-2xl"
        >
          <Github className="h-4 w-4 shrink-0 md:absolute md:left-3" />
          <span className="mx-auto hidden items-center justify-center gap-2 md:flex">
            <span className="text-xl leading-none text-star">★</span>
            {BRAND_NAME}
          </span>
        </a>
        <p className="hidden text-center text-[11px] leading-5 text-muted md:block">❤️  欢迎点 Star 支持维护  ❤️</p>
      </div>
    </aside>
  )
}
