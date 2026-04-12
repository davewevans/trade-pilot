import { NavLink } from 'react-router-dom'
import { ACCOUNTS } from '../../api/client'

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `block px-3 py-2 rounded text-sm transition-colors ${
    isActive ? 'font-semibold' : ''
  }`

const linkStyle = (isActive: boolean): React.CSSProperties => ({
  color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
  backgroundColor: isActive ? 'var(--bg-card)' : 'transparent',
  borderLeft: `2px solid ${isActive ? 'var(--accent)' : 'transparent'}`,
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
    </aside>
  )
}
