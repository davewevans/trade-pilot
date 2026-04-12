import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { Dashboard } from './pages/Dashboard'
import { AccountDetail } from './pages/AccountDetail'
import { DecisionLog } from './pages/DecisionLog'

export function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/account/:account" element={<AccountDetail />} />
          <Route path="/decisions" element={<DecisionLog />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}
