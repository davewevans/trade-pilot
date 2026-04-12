import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { Dashboard } from './pages/Dashboard'
import { AccountDetail } from './pages/AccountDetail'
import { DecisionLog } from './pages/DecisionLog'
import { ReasoningExplorer } from './pages/ReasoningExplorer'
import { Guardrails } from './pages/Guardrails'
import { Strategies } from './pages/Strategies'

export function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/account/:account" element={<AccountDetail />} />
          <Route path="/decisions" element={<DecisionLog />} />
          <Route path="/reasoning" element={<ReasoningExplorer />} />
          <Route path="/guardrails" element={<Guardrails />} />
          <Route path="/strategies" element={<Strategies />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}
