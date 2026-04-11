# Synthesized Knowledge Base

This folder contains clean, processed knowledge documents generated 
by synthesizing raw notes using Claude.

## How to Synthesize

1. Open your Claude.ai project: "trade-pilot knowledge base"
2. Paste the contents of one or more raw note files
3. Use this prompt:

---

"I've been taking notes on [TOPIC] from various courses and blogs. 
Here are my raw notes:

[paste raw notes]

Please synthesize these into a clean, structured knowledge document 
that:
1. Removes redundancy and organizes by concept, not by source
2. Resolves contradictions — if sources disagree, note both views 
   and flag for my review
3. Highlights what is most actionable for a wheel strategy options 
   trader using the Alpaca API
4. Flags anything I should verify or research further with a 
   ⚠️ marker
5. Suggests whether this knowledge should update prompts/system.md, 
   a specific phase prompt file, or neither

Format as clean markdown I can save to knowledge/synthesized/[topic].md"

---

4. Review the output carefully
5. Save to this folder
6. Update knowledge/INDEX.md
7. If changes to prompts are suggested, evaluate and apply them
8. Run scripts/test_advisor.py to verify Claude reasoning improved

## Files in This Folder

| File | Status | Last Updated |
|------|--------|--------------|
| _none yet — synthesize your first batch of raw notes to begin_ | | |
