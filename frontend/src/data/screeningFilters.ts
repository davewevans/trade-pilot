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

  spreads: [
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
}
