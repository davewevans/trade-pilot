import { useEffect, useState, type ReactNode } from 'react'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'

function SessionExpiredOverlay() {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
    >
      <div
        className="rounded-lg p-8 max-w-sm w-full text-center space-y-4"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <div className="text-2xl font-semibold" style={{ color: 'var(--text-primary)' }}>
          Session expired
        </div>
        <p className="text-sm" style={{ color: 'var(--text-secondary)' }}>
          You've been logged out. Click below to return to the login page.
        </p>
        <button
          onClick={() => window.location.reload()}
          className="w-full py-2 rounded font-medium text-sm"
          style={{ backgroundColor: 'var(--accent)', color: '#fff' }}
        >
          Log in again
        </button>
      </div>
    </div>
  )
}

export function AppShell({ children }: { children: ReactNode }) {
  const [sessionExpired, setSessionExpired] = useState(false)

  useEffect(() => {
    const handler = () => setSessionExpired(true)
    window.addEventListener('auth:expired', handler)
    return () => window.removeEventListener('auth:expired', handler)
  }, [])

  return (
    <div className="h-full flex flex-col">
      {sessionExpired && <SessionExpiredOverlay />}
      <TopBar />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
    </div>
  )
}
