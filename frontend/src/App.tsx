import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { Dashboard } from './pages/Dashboard'
import { AccountDetail } from './pages/AccountDetail'
import { DecisionLog } from './pages/DecisionLog'
import { ReasoningExplorer } from './pages/ReasoningExplorer'

export function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/account/:account" element={<AccountDetail />} />
          <Route path="/decisions" element={<DecisionLog />} />
          <Route path="/reasoning" element={<ReasoningExplorer />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}
