## How to Bump a Version in trade-pilot

---

### Step 1 — Decide the version number

Look at the current version in `version.py` and pick the next number:

- Bug fix, prompt tweak, UI change → bump patch (`1.0.0` → `1.0.1`)
- New feature, new strategy, new data source, new guardrail → bump minor (`1.0.0` → `1.1.0`)
- Strategy logic changed enough that old performance data isn't comparable → bump major (`1.0.0` → `2.0.0`)

---

### Step 2 — Update `CHANGELOG.md`

Open `CHANGELOG.md`. You should already have notes accumulated under `[Unreleased]`. If not, write them now.

Promote `[Unreleased]` to the new version:

```markdown
## [Unreleased]

## [1.0.1] — 2026-04-19
### Fixed
- Circuit breaker multiplier now actually gates new entries
- Covered call guardrail can see equity positions
```

Leave a fresh empty `[Unreleased]` section at the top for the next round.

---

### Step 3 — Update `version.py`

```python
VERSION = "1.0.1"
VERSION_DATE = "2026-04-19"
VERSION_NOTES = "One sentence summary of what changed."
```

---

### Step 4 — Commit

```bash
git add version.py CHANGELOG.md
git commit -m "chore: bump version to 1.0.1"
```

---

### Step 5 — Tag the release

```bash
git tag v1.0.1
git push origin main --tags
```

---

### That's it.

Deploy to Render as you normally would. The dashboard will automatically show the new version on next startup.
