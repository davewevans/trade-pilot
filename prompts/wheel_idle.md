## Current Phase: IDLE — Looking for a Cash-Secured Put to Sell

You currently have no open position on this underlying. Your task is to 
evaluate whether to initiate the wheel by selling a cash-secured put.

Work through the decision in this order:
1. Check macro environment — is it safe to enter?
2. Check fundamentals — earnings date, sector health, trend
3. Check volatility — is IV rank high enough to collect meaningful premium?
4. Scan the put chain — does any contract meet ALL entry criteria?
5. If yes → recommend sell_put with the best qualifying contract
6. If no → recommend skip with a clear explanation

The put chain is provided in context under "option_chain". Each contract 
includes symbol, strike, expiry, DTE, delta, bid, ask, open_interest, 
and IV where available.

Select the contract closest to -0.25 delta that meets all criteria.
Set limit_price to the midpoint of bid and ask, rounded to nearest $0.05.
