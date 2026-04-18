import { useEffect, useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { useAccounts } from '../../hooks/useAccounts'
import { useFeatureFlags } from '../../hooks/useFeatureFlags'

function usePendingRecsCount(): number {
  const [count, setCount] = useState(0)
  useEffect(() => {
    fetch('/api/research/recommendations', { credentials: 'same-origin' })
      .then((r) => r.ok ? r.json() : null)
      .then((d) => {
        if (!d) return
        let pending = 0
        for (const wl of Object.values(d.watchlists as Record<string, { add: unknown[]; remove: unknown[] }>)) {
          pending += (wl.add?.length ?? 0) + (wl.remove?.length ?? 0)
        }
        setCount(pending)
      })
      .catch(() => {})
  }, [])
  return count
}

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `block px-3 py-2 rounded text-sm transition-all ${
    isActive ? 'font-semibold' : 'hover:bg-[var(--bg-card)]'
  }`

const linkStyle = (isActive: boolean): React.CSSProperties => ({
  color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
  backgroundColor: isActive
    ? 'color-mix(in srgb, var(--accent) 14%, var(--bg-card))'
    : 'transparent',
  borderLeft: `3px solid ${isActive ? 'var(--accent)' : 'transparent'}`,
  boxShadow: isActive
    ? 'inset 0 0 0 1px color-mix(in srgb, var(--accent) 25%, transparent)'
    : 'none',
})

const LEARN_ROUTES = [
  '/options-basics',
  '/trade-lifecycle',
  '/research-guide',
  '/how-it-works',
  '/playbook',
  '/how-backtesting-works',
  '/strategies',
  '/guardrails',
  '/circuit-breakers',
  '/market-regimes',
  '/skip-reasons',
  '/glossary',
  '/data-sources',
  '/faq',
]

function SidebarSection({
  label,
  children,
  defaultOpen = true,
  storageKey,
  forceOpen = false,
  first = false,
}: {
  label: string
  children: React.ReactNode
  defaultOpen?: boolean
  storageKey: string
  forceOpen?: boolean
  first?: boolean
}) {
  const [open, setOpen] = useState(() => {
    const saved = localStorage.getItem(`sidebar-${storageKey}`)
    return saved !== null ? saved === 'true' : defaultOpen
  })

  useEffect(() => {
    if (forceOpen && !open) {
      setOpen(true)
      localStorage.setItem(`sidebar-${storageKey}`, 'true')
    }
  }, [forceOpen])

  const toggle = () => {
    setOpen((prev) => {
      localStorage.setItem(`sidebar-${storageKey}`, String(!prev))
      return !prev
    })
  }

  return (
    <div className={first ? '' : 'mt-6'}>
      <button
        onClick={toggle}
        className="flex items-center justify-between w-full text-[10px] uppercase tracking-wider mb-2 px-3 cursor-pointer hover:opacity-80"
        style={{ color: 'var(--text-muted)', background: 'none', border: 'none' }}
      >
        <span>{label}</span>
        <span
          className="text-[8px] transition-transform duration-200"
          style={{
            transform: open ? 'rotate(0deg)' : 'rotate(-90deg)',
            color: 'var(--text-muted)',
            display: 'inline-block',
          }}
        >
          ▼
        </span>
      </button>
      {open && children}
    </div>
  )
}

export function Sidebar() {
  const location = useLocation()
  const isLearnRoute = LEARN_ROUTES.some((p) => location.pathname.startsWith(p))
  const { accounts } = useAccounts()
  const pendingRecs = usePendingRecsCount()
  const featureFlags = useFeatureFlags()

  return (
    <aside
      className="w-56 shrink-0 border-r p-3 overflow-y-auto"
      style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
    >
      <SidebarSection label="Overview" storageKey="overview" defaultOpen={true} first={true}>
        <NavLink to="/" end className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Dashboard
        </NavLink>
        <NavLink to="/decisions" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Decision Log
        </NavLink>
        <NavLink to="/reasoning" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Reasoning
        </NavLink>
        <NavLink to="/evaluations" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Evaluations
        </NavLink>
        <NavLink to="/watchlist" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Watchlist
        </NavLink>
        {featureFlags.strategy_health_enabled && (
          <NavLink
            to="/strategy-health"
            className={linkClass}
            style={({ isActive }) => linkStyle(isActive)}
          >
            Strategy Health
          </NavLink>
        )}
      </SidebarSection>

      <SidebarSection label="Research" storageKey="research" defaultOpen={true}>
        <NavLink to="/research" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Research Dashboard
        </NavLink>
        <NavLink to="/recommendations" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          <span style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
            Recommendations
            {pendingRecs > 0 && (
              <span
                style={{
                  fontSize: 10,
                  padding: '1px 6px',
                  borderRadius: 10,
                  backgroundColor: 'var(--accent)',
                  color: '#fff',
                  fontWeight: 700,
                  marginLeft: 4,
                }}
              >
                {pendingRecs}
              </span>
            )}
          </span>
        </NavLink>
        <NavLink to="/backtest" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Backtester
        </NavLink>
        <NavLink to="/backtest-intel" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Backtest Intel
        </NavLink>
        <NavLink to="/volatility" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Volatility
        </NavLink>
      </SidebarSection>

      <SidebarSection label="Accounts" storageKey="accounts" defaultOpen={true}>
        {accounts.map((a) => (
          <NavLink
            key={a.account_id}
            to={`/account/${a.account_id}`}
            className={linkClass}
            style={({ isActive }) => ({
              ...linkStyle(isActive),
              opacity: a.status === 'inactive' ? 0.5 : 1,
            })}
          >
            {a.label}
          </NavLink>
        ))}
      </SidebarSection>

      <SidebarSection
        label="Learn"
        storageKey="learn"
        defaultOpen={false}
        forceOpen={isLearnRoute}
      >
        <NavLink to="/options-basics" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Options Basics
        </NavLink>
        <NavLink to="/trade-lifecycle" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Life of a Trade
        </NavLink>
        <NavLink to="/research-guide" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Research Guide
        </NavLink>
        <NavLink to="/how-it-works" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          How Claude Decides
        </NavLink>
        <NavLink to="/playbook" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Claude's Playbook
        </NavLink>
        <NavLink to="/strategies" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Strategies
        </NavLink>
        <NavLink to="/how-backtesting-works" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          How Backtesting Works
        </NavLink>
        <NavLink to="/guardrails" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Guardrails
        </NavLink>
        <NavLink to="/circuit-breakers" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Circuit Breakers
        </NavLink>
        <NavLink to="/market-regimes" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Market Regimes
        </NavLink>
        <NavLink to="/skip-reasons" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Skip Reasons
        </NavLink>
        <NavLink to="/glossary" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Glossary
        </NavLink>
        <NavLink to="/data-sources" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          Data Sources
        </NavLink>
        <NavLink to="/faq" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
          FAQ
        </NavLink>
      </SidebarSection>

      <hr className="my-4 border-0 h-px" style={{ backgroundColor: 'var(--border)' }} />

      <NavLink to="/about" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
        About
      </NavLink>
    </aside>
  )
}
