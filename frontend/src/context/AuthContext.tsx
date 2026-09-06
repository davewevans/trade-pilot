import { createContext, useContext } from 'react'

export interface AuthState {
  /** False when the server is in public read-only mode. */
  authRequired: boolean
  /** True when this browser holds a valid session cookie. */
  authenticated: boolean
  /** Re-fetch /api/config (call after a successful login). */
  refresh: () => void
}

/**
 * Defaults are the private-dashboard values, so any component rendered
 * outside the provider (or before /api/config resolves) behaves exactly
 * as it did before public mode existed.
 */
export const AuthContext = createContext<AuthState>({
  authRequired: true,
  authenticated: false,
  refresh: () => {},
})

export function useAuth(): AuthState {
  return useContext(AuthContext)
}

/**
 * True when the viewer may use mutating controls. Hide admin UI behind this
 * rather than disabling it — an anonymous visitor should not see the controls
 * at all.
 */
export function useCanMutate(): boolean {
  return useAuth().authenticated
}
