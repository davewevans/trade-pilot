import { useCallback, useEffect, useState } from 'react'
import { api, type AccountConfig } from '../api/client'

interface UseAccountsResult {
  accounts: AccountConfig[]
  availableStrategies: Array<{ name: string; display_name: string }>
  loading: boolean
  error: string | null
  refetch: () => void
}

export function useAccounts(): UseAccountsResult {
  const [accounts, setAccounts] = useState<AccountConfig[]>([])
  const [availableStrategies, setAvailableStrategies] = useState<
    Array<{ name: string; display_name: string }>
  >([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refetch = useCallback(() => {
    api
      .accountConfig()
      .then((r) => {
        setAccounts(r.accounts)
        setAvailableStrategies(r.available_strategies)
        setError(null)
      })
      .catch((e: unknown) => {
        setError(e instanceof Error ? e.message : 'Failed to load accounts')
      })
      .finally(() => {
        setLoading(false)
      })
  }, [])

  useEffect(() => {
    refetch()
    const id = setInterval(refetch, 60_000)
    return () => clearInterval(id)
  }, [refetch])

  return { accounts, availableStrategies, loading, error, refetch }
}
