import { useCallback, useEffect, useMemo, useState, type ReactNode, type FormEvent } from 'react'
import { MacroBlockBanner } from '../MacroBlockBanner'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'
import { AuthContext, type AuthState } from '../../context/AuthContext'
import { api } from '../../api/client'

function SessionExpiredOverlay({
  onAuthenticated,
  publicMode,
}: {
  onAuthenticated: () => void
  publicMode: boolean
}) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const res = await fetch('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ password }),
      })
      if (res.ok) {
        onAuthenticated()
      } else {
        setError('Incorrect password')
        setPassword('')
      }
    } catch {
      setError('Could not reach the server')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      style={{ backgroundColor: 'rgba(0,0,0,0.6)', backdropFilter: 'blur(4px)' }}
    >
      <div
        className="rounded-lg p-8 max-w-sm w-full space-y-4"
        style={{ backgroundColor: 'var(--bg-card)', border: '1px solid var(--border)' }}
      >
        <div className="text-xl font-semibold text-center" style={{ color: 'var(--text-primary)' }}>
          {publicMode ? 'Log in' : 'Session expired'}
        </div>
        <p className="text-sm text-center" style={{ color: 'var(--text-secondary)' }}>
          {publicMode
            ? 'Enter your password to access admin controls.'
            : 'Enter your password to continue.'}
        </p>
        <form onSubmit={submit} className="space-y-3">
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="Password"
            autoFocus
            className="w-full rounded px-3 py-2 text-sm"
            style={{
              backgroundColor: 'var(--bg-secondary)',
              border: `1px solid ${error ? 'var(--red)' : 'var(--border)'}`,
              color: 'var(--text-primary)',
              outline: 'none',
            }}
          />
          {error && (
            <p className="text-xs" style={{ color: 'var(--red)' }}>{error}</p>
          )}
          <button
            type="submit"
            disabled={loading || !password}
            className="w-full py-2 rounded font-medium text-sm"
            style={{
              backgroundColor: loading || !password ? 'var(--text-muted)' : 'var(--accent)',
              color: '#fff',
              cursor: loading || !password ? 'default' : 'pointer',
            }}
          >
            {loading ? 'Logging in…' : 'Log in'}
          </button>
        </form>
      </div>
    </div>
  )
}

export function AppShell({ children }: { children: ReactNode }) {
  const [sessionExpired, setSessionExpired] = useState(false)
  // Defaults match the private dashboard, so a failed /api/config fetch
  // degrades to exactly the pre-public-mode behaviour.
  const [authRequired, setAuthRequired] = useState(true)
  const [authenticated, setAuthenticated] = useState(false)

  const refresh = useCallback(() => {
    api
      .appConfig()
      .then((cfg) => {
        setAuthRequired(cfg.auth_required)
        setAuthenticated(cfg.authenticated)
      })
      .catch(() => {
        // Leave the safe defaults in place.
      })
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  useEffect(() => {
    const handler = () => setSessionExpired(true)
    window.addEventListener('auth:expired', handler)
    return () => window.removeEventListener('auth:expired', handler)
  }, [])

  const authState: AuthState = useMemo(
    () => ({ authRequired, authenticated, refresh }),
    [authRequired, authenticated, refresh],
  )

  const publicMode = !authRequired && !authenticated

  return (
    <AuthContext.Provider value={authState}>
    <div className="h-full flex flex-col">
      {sessionExpired && (
        <SessionExpiredOverlay
          publicMode={publicMode}
          onAuthenticated={() => {
            setSessionExpired(false)
            refresh()
          }}
        />
      )}
      <TopBar />
      <MacroBlockBanner />
      <div className="flex flex-1 overflow-hidden">
        <Sidebar />
        <main className="flex-1 overflow-auto p-6">{children}</main>
      </div>
    </div>
    </AuthContext.Provider>
  )
}
