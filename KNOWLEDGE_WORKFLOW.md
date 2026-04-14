# Knowledge Workflow

## How Knowledge Gets Into the Bot

```
Study material (courses, blogs, ORATS docs)
        ↓
Claude project (trade-pilot knowledge base)
  → Paste content, discuss, identify gaps
  → Write Claude Code prompts for changes
        ↓
Code + prompt changes deployed via GitHub
  → prompts/system.md (strategy rules, always loaded)
  → prompts/*_idle.md, *_open.md (phase-specific)
  → strategies/*.py (pre-check logic, guardrails)
  → data/context_builder.py (what Claude sees)
        ↓
Bot picks up changes on next scheduled run
  (prompts loaded fresh every API call)
```

## Where Things Live

| What | Where | Used By |
|------|-------|---------|
| Strategy rules (always apply) | prompts/system.md | Every Claude API call |
| Phase-specific instructions | prompts/*_idle.md, *_open.md | Loaded per phase |
| Hard safety rules | strategies/guardrails.py | Python — Claude can't override |
| Pre-check filters | strategies/*_strategy.py | Python — runs before Claude |
| Live market data | data/context_builder.py | Injected every call |
| Historical backtest data | database/backtest_repository.py | Queried at decision time |
| What we learned and changed | knowledge/CHANGELOG.md | Human reference only |

## Prompt Caching

prompts/system.md uses `cache_control: ephemeral`. Anthropic caches
it for 5 minutes. When the bot checks 5 symbols at market open, the
system prompt is processed once — subsequent calls reuse the cache.
This means system.md can be as long as needed without significant
cost impact.

## The Process

1. **Study** — read courses, blogs, ORATS University, etc.
2. **Discuss** — paste into the Claude project, analyze against the
   codebase, identify actionable improvements
3. **Implement** — write Claude Code prompts for specific changes
4. **Log** — add an entry to knowledge/CHANGELOG.md recording what
   was learned and what changed
5. **Verify** — run scripts/test_advisor.py or review bot decisions
   in the dashboard

## When to Update Prompts vs Code

- **New rules Claude should follow** → update prompts/system.md or
  the relevant phase prompt
- **New data Claude should see** → update data/context_builder.py
  and data/orats_client.py (or other data sources)
- **Hard limits Claude can't override** → update strategies/guardrails.py
- **Pre-filters before Claude is called** → update the strategy's
  pre_check() method
- **Background knowledge for your reference** → log it in the
  CHANGELOG, no code change needed
