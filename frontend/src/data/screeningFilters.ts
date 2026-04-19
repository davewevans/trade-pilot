export interface StrategyFilters {
  strategy: string
  filters: { label: string; value: string }[]
}

export const SCREENING_FILTERS: Record<string, StrategyFilters[]> = {
  wheel: [
    {
      strategy: 'Cash-Secured Put Entry',
      filters: [
        { label: 'Delta', value: '-0.20 to -0.30' },
        { label: 'DTE', value: '21–35 days' },
        { label: 'IV Rank (1y)', value: '≥ 30' },
        { label: 'Open Interest', value: '≥ 200' },
        { label: 'Bid-Ask Spread', value: '≤ $0.15' },
        { label: 'Earnings', value: '> 21 days away' },
        { label: 'Max Position Size', value: '≤ 10% buying power' },
        { label: 'SMA Filter', value: 'Stock above 50-day SMA' },
        { label: 'Max Concurrent', value: '5 wheels' },
      ],
    },
    {
      strategy: 'Covered Call Entry',
      filters: [
        { label: 'Delta', value: '0.20 to 0.35' },
        { label: 'DTE', value: '21–35 days' },
        { label: 'IV Rank (1y)', value: '≥ 20' },
        { label: 'Open Interest', value: '≥ 200' },
        { label: 'Strike', value: 'Above upper Bollinger Band' },
        { label: 'Strike', value: 'Above cost basis (hard rule)' },
        { label: 'Earnings', value: '> 21 days away' },
      ],
    },
  ],

  iron_condor: [
    {
      strategy: 'Iron Condor Entry',
      filters: [
        { label: 'Combined Credit', value: '≥ $1.25' },
        { label: 'Put Short Delta', value: '-0.15 to -0.25' },
        { label: 'Call Short Delta', value: '0.15 to 0.25' },
        { label: 'DTE', value: '20–50 days' },
        { label: 'Short Strikes', value: 'Outside 1× implied move' },
        { label: 'Earnings', value: '> 21 days away' },
        { label: 'VIX Range', value: '20–35' },
        { label: 'Regime', value: 'NEUTRAL only' },
        { label: 'IV Environment', value: 'HIGH only (IVR ≥ 50)' },
      ],
    },
  ],

  adaptive_spreads: [
    {
      strategy: 'Bull Put Spread Entry',
      filters: [
        { label: 'Short Put Delta', value: '-0.20 to -0.30' },
        { label: 'Net Credit', value: '≥ $0.75' },
        { label: 'DTE', value: '21–35 days' },
        { label: 'Credit-to-Width', value: '≥ 0.15' },
        { label: 'Open Interest', value: '≥ 100 (both legs)' },
        { label: 'Bid-Ask Spread', value: '< 20%' },
        { label: 'Earnings', value: '> 21 days away' },
        { label: 'SMA Filter', value: 'Stock above 50-day SMA' },
        { label: 'Regime', value: 'BULL or NEUTRAL' },
        { label: 'IV Environment', value: 'MODERATE or HIGH' },
      ],
    },
    {
      strategy: 'Bear Call Spread Entry',
      filters: [
        { label: 'Short Call Delta', value: '0.20 to 0.30' },
        { label: 'Net Credit', value: '≥ $0.75' },
        { label: 'DTE', value: '21–35 days' },
        { label: 'Open Interest', value: '≥ 100 (both legs)' },
        { label: 'Bid-Ask Spread', value: '< 20%' },
        { label: 'Earnings', value: '> 21 days away' },
        { label: 'Ex-Dividend', value: 'None within DTE window' },
        { label: 'SMA Filter', value: 'Stock below 50-day SMA' },
        { label: 'Regime', value: 'BEAR or NEUTRAL' },
        { label: 'IV Environment', value: 'MODERATE or HIGH' },
      ],
    },
    {
      strategy: 'Long Call Vertical Entry',
      filters: [
        { label: 'Long Call Delta', value: '0.45 to 0.60' },
        { label: 'Net Debit', value: '< $2.00' },
        { label: 'DTE', value: '30–60 days' },
        { label: 'Earnings', value: '> DTE + 5 days' },
        { label: 'Entry Signal', value: 'CAHOLD support bounce' },
        { label: 'SMA Filter', value: 'Stock above 50-day SMA' },
        { label: 'Regime', value: 'BULL only' },
        { label: 'IV Environment', value: 'LOW only (IVR < 30)' },
      ],
    },
  ],

  iron_butterfly: [
    {
      strategy: 'Iron Butterfly Entry',
      filters: [
        { label: 'Center Strike', value: 'ATM (both shorts at same strike)' },
        { label: 'Wing Width', value: '$5 above and below center' },
        { label: 'Total Credit', value: '≥ $2.00' },
        { label: 'Credit-to-Width Ratio', value: '≥ 30%' },
        { label: 'DTE', value: '20–45 days' },
        { label: 'Open Interest', value: '≥ 200 (both wings)' },
        { label: 'Bid-Ask Spread', value: '< 15%' },
        { label: 'Earnings', value: '> 30 days away' },
        { label: 'IV Forecast', value: 'OVERVALUED or FAIR (never UNDERVALUED)' },
        { label: 'Term Structure', value: 'Not in backwardation' },
        { label: 'Regime', value: 'NEUTRAL only' },
        { label: 'IV Environment', value: 'HIGH (IVR ≥ 50)' },
      ],
    },
  ],

  calendar_spread: [
    {
      strategy: 'Calendar Spread Entry',
      filters: [
        { label: 'Strike Selection', value: 'ATM (50 delta, same strike both legs)' },
        { label: 'Short Leg DTE', value: '20–35 days' },
        { label: 'Long Leg DTE', value: '50–90 days (≥ 30 days after short leg)' },
        { label: 'Net Debit', value: '≤ $2.50' },
        { label: 'Earnings', value: 'Must NOT fall between the two expirations' },
        { label: 'Term Structure', value: 'Contango required (short-term IV < long-term IV)' },
        { label: 'IV Forecast', value: 'FAIR or UNDERVALUED preferred (we buy the long leg)' },
        { label: 'Regime', value: 'NEUTRAL only' },
        { label: 'IV Environment', value: 'LOW or MODERATE (IVR < 50)' },
      ],
    },
  ],

  conservative_wheel: [
    {
      strategy: 'Conservative Wheel — Cash-Secured Put Entry',
      filters: [
        { label: 'Delta', value: '-0.20 to -0.30' },
        { label: 'DTE', value: '21–35 days' },
        { label: 'IV Rank (1y)', value: '≥ 30' },
        { label: 'Open Interest', value: '≥ 200' },
        { label: 'Earnings', value: '> 21 days away' },
        { label: 'Max Position Size', value: '≤ 5% buying power (vs. 10% standard)' },
        { label: 'Max Concurrent', value: '10 wheels (vs. 5 standard)' },
      ],
    },
    {
      strategy: 'Conservative Wheel — Covered Call Entry',
      filters: [
        { label: 'Delta', value: '0.20 to 0.35' },
        { label: 'DTE', value: '7–14 days (vs. 21–35 standard — faster share turnover)' },
        { label: 'Strike', value: 'Above cost basis (hard rule — no Bollinger Band check)' },
        { label: 'Open Interest', value: '≥ 200' },
        { label: 'Earnings', value: '> 21 days away' },
      ],
    },
  ],
}
