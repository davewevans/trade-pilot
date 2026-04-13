import { NavLink } from 'react-router-dom'
import { ACCOUNTS } from '../../api/client'

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

export function Sidebar() {
  return (
    <aside
      className="w-56 shrink-0 border-r p-3"
      style={{ backgroundColor: 'var(--bg-secondary)', borderColor: 'var(--border)' }}
    >
      <div
        className="text-[10px] uppercase tracking-wider mb-2 px-3"
        style={{ color: 'var(--text-muted)' }}
      >
        Overview
      </div>
      <NavLink to="/" end className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
        Dashboard
      </NavLink>
      <NavLink to="/decisions" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
        Decision Log
      </NavLink>
      <NavLink to="/reasoning" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
        Reasoning
      </NavLink>
      <NavLink to="/watchlist" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
        Watchlist
      </NavLink>

      <div
        className="text-[10px] uppercase tracking-wider mt-6 mb-2 px-3"
        style={{ color: 'var(--text-muted)' }}
      >
        Accounts
      </div>
      {ACCOUNTS.map((a) => (
        <NavLink
          key={a.account}
          to={`/account/${a.account}`}
          className={linkClass}
          style={({ isActive }) => linkStyle(isActive)}
        >
          {a.label}
        </NavLink>
      ))}

      <div
        className="text-[10px] uppercase tracking-wider mt-6 mb-2 px-3"
        style={{ color: 'var(--text-muted)' }}
      >
        Learn
      </div>
      <NavLink to="/options-basics" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
        Options Basics
      </NavLink>
      <NavLink to="/trade-lifecycle" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
        Life of a Trade
      </NavLink>
      <NavLink to="/how-it-works" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
        How Claude Decides
      </NavLink>
      <NavLink to="/strategies" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
        Strategies
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

      <hr className="my-4 border-0 h-px" style={{ backgroundColor: 'var(--border)' }} />

      <NavLink to="/about" className={linkClass} style={({ isActive }) => linkStyle(isActive)}>
        About
      </NavLink>
    </aside>
  )
}
