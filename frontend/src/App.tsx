import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { AppShell } from './components/layout/AppShell'
import { Dashboard } from './pages/Dashboard'
import { Overview } from './pages/Overview'
import { AccountDetail } from './pages/AccountDetail'
import { DecisionLog } from './pages/DecisionLog'
import { ReasoningExplorer } from './pages/ReasoningExplorer'
import { Guardrails } from './pages/Guardrails'
import { Strategies } from './pages/Strategies'
import { HowItWorks } from './pages/HowItWorks'
import { ClaudesPlaybook } from './pages/ClaudesPlaybook'
import { HowBacktestingWorks } from './pages/HowBacktestingWorks'
import { HowEvaluationsWork } from './pages/HowEvaluationsWork'
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
import { Research } from './pages/Research'
import { Recommendations } from './pages/Recommendations'
import { ResearchGuide } from './pages/ResearchGuide'
import { Evaluations } from './pages/Evaluations'
import { EvaluationDetail } from './pages/EvaluationDetail'
import { StrategyHealth } from './pages/StrategyHealth'
import { Reliability } from './pages/Reliability'
import { Measurement } from './pages/Measurement'

export function App() {
  return (
    <BrowserRouter>
      <AppShell>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/overview" element={<Overview />} />
          <Route path="/watchlist" element={<Watchlist />} />
          <Route path="/research" element={<Research />} />
          <Route path="/recommendations" element={<Recommendations />} />
          <Route path="/research-guide" element={<ResearchGuide />} />
          <Route path="/backtest" element={<Backtest />} />
          <Route path="/backtest-intel" element={<BacktestIntel />} />
          <Route path="/volatility" element={<Volatility />} />
          <Route path="/account/:account" element={<AccountDetail />} />
          <Route path="/evaluations" element={<Evaluations />} />
          <Route path="/evaluations/:month" element={<EvaluationDetail />} />
          <Route path="/strategy-health" element={<StrategyHealth />} />
          <Route path="/decisions" element={<DecisionLog />} />
          <Route path="/reasoning" element={<ReasoningExplorer />} />
          <Route path="/guardrails" element={<Guardrails />} />
          <Route path="/strategies" element={<Strategies />} />
          <Route path="/how-it-works" element={<HowItWorks />} />
          <Route path="/playbook" element={<ClaudesPlaybook />} />
          <Route path="/reliability" element={<Reliability />} />
          <Route path="/measurement" element={<Measurement />} />
          <Route path="/how-backtesting-works" element={<HowBacktestingWorks />} />
          <Route path="/how-evaluations-work" element={<HowEvaluationsWork />} />
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
