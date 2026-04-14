import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { Dashboard } from './pages/Dashboard'
import { AccountDetail } from './pages/AccountDetail'
import { DecisionLog } from './pages/DecisionLog'
import { ReasoningExplorer } from './pages/ReasoningExplorer'
import { Guardrails } from './pages/Guardrails'
import { Strategies } from './pages/Strategies'
import { HowItWorks } from './pages/HowItWorks'
import { HowBacktestingWorks } from './pages/HowBacktestingWorks'
import { OptionsBasics } from './pages/OptionsBasics'
import { TradeLifecycle } from './pages/TradeLifecycle'
import { SkipReasons } from './pages/SkipReasons'
import { CircuitBreakers } from './pages/CircuitBreakers'
import { Glossary } from './pages/Glossary'
import { MarketRegimes } from './pages/MarketRegimes'
import { FAQ } from './pages/FAQ'
import { About } from './pages/About'
import { DataSources } from './pages/DataSources'
import { Watchlist } from './pages/Watchlist'
import { Backtest } from './pages/Backtest'
import { BacktestIntel } from './pages/BacktestIntel'
import { Volatility } from './pages/Volatility'

export function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/backtest" element={<Backtest />} />
          <Route path="/backtest-intel" element={<BacktestIntel />} />
          <Route path="/volatility" element={<Volatility />} />
          <Route path="/account/:account" element={<AccountDetail />} />
          <Route path="/decisions" element={<DecisionLog />} />
          <Route path="/reasoning" element={<ReasoningExplorer />} />
          <Route path="/guardrails" element={<Guardrails />} />
          <Route path="/strategies" element={<Strategies />} />
          <Route path="/how-it-works" element={<HowItWorks />} />
          <Route path="/how-backtesting-works" element={<HowBacktestingWorks />} />
          <Route path="/options-basics" element={<OptionsBasics />} />
          <Route path="/trade-lifecycle" element={<TradeLifecycle />} />
          <Route path="/skip-reasons" element={<SkipReasons />} />
          <Route path="/circuit-breakers" element={<CircuitBreakers />} />
          <Route path="/market-regimes" element={<MarketRegimes />} />
          <Route path="/glossary" element={<Glossary />} />
          <Route path="/faq" element={<FAQ />} />
          <Route path="/about" element={<About />} />
          <Route path="/data-sources" element={<DataSources />} />
        </Routes>
      </AppShell>
    </BrowserRouter>
  )
}
