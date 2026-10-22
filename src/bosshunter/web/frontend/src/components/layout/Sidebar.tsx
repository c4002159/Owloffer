import { NavLink } from 'react-router-dom'
import { BriefcaseBusiness, LayoutDashboard, Radar, Settings } from 'lucide-react'
import { useEffect, useState } from 'react'

const navItems = [
  { to: '/', icon: LayoutDashboard, label: '工作台' },
  { to: '/jobs', icon: BriefcaseBusiness, label: '岗位池' },
  { to: '/monitor', icon: Radar, label: '监测执行' },
  { to: '/config', icon: Settings, label: '配置' },
]

interface SidebarProps {
  pendingReplies?: number
}

export function Sidebar({ pendingReplies: pendingRepliesProp }: SidebarProps) {
  const [pendingReplies, setPendingReplies] = useState(pendingRepliesProp ?? 0)

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

  return (
    <aside className="flex w-16 shrink-0 flex-col border-r border-card-border bg-card md:w-60">
      <div className="flex h-16 shrink-0 items-center justify-center border-b border-card-border md:justify-start md:px-5">
        <div className="flex items-center gap-3">
          <img src="/offerowl-128.png" alt="OfferOwl" width={40} height={40} className="h-10 w-10 shrink-0 rounded-full" />
          <div className="hidden md:block">
            <div className="font-semibold text-base tracking-tight text-foreground">OfferOwl</div>
            <div className="text-[11px] text-muted">求职助手 · 本地</div>
          </div>
        </div>
      </div>

      <nav className="flex-1 space-y-1 px-1.5 py-4 md:px-3">
        {navItems.map(item => (
          <NavLink
            key={item.to}
            to={item.to}
            data-tour={item.to === '/config' ? 'nav-config' : item.to === '/' ? 'nav-workbench' : undefined}
            className={({ isActive }) =>
              `relative flex items-center justify-center gap-3 rounded-lg px-1 py-3 text-[10px] transition-colors md:justify-between md:px-3 md:text-sm ${
                isActive
                  ? 'bg-secondary text-primary font-semibold'
                  : 'text-muted hover:text-foreground hover:bg-surface'
              }`
            }
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
      </nav>

    </aside>
  )
}
