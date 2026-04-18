Bump the trade-pilot version following the process in VERSIONING.md.

## Arguments

The user may pass a bump type: `patch`, `minor`, or `major`. If no argument is given,
read the [Unreleased] section of CHANGELOG.md and decide the appropriate bump type
using these rules:
- Bug fix, prompt tweak, UI change → `patch`
- New feature, new strategy, new data source, new guardrail → `minor`
- Strategy logic changed enough that old performance data isn't comparable → `major`

Argument passed (may be empty): $ARGUMENTS

## Steps

1. Read `version.py` to get the current VERSION string.

2. Read `CHANGELOG.md`. Extract everything under `## [Unreleased]` down to (but not
   including) the next `## [` heading. This is the content that will become the new
   release section.

3. Compute the new version number:
   - If the user passed `patch`, `minor`, or `major`, use that bump type.
   - Otherwise, infer the bump type from the unreleased content using the rules above.
   - Increment the appropriate part of the current version (MAJOR.MINOR.PATCH).
   - Reset lower parts to 0 on minor or major bumps.

4. Get today's date (it is provided in the system context as `currentDate`, format
   YYYY-MM-DD).

5. Update `CHANGELOG.md`:
   - Replace `## [Unreleased]` with:
     ```
     ## [Unreleased]

     ## [X.Y.Z] - YYYY-MM-DD
     ```
     followed by the content that was previously under [Unreleased].
   - Preserve everything else in the file exactly.

6. Update `version.py`:
   - Set `VERSION = "X.Y.Z"`
   - Set `VERSION_DATE = "YYYY-MM-DD"`
   - Set `VERSION_NOTES` to a single concise sentence summarising the release.
     Derive this from the unreleased changelog entries — one sentence, no bullet
     points, no markdown.

7. Commit:
   ```
   git add version.py CHANGELOG.md
   git commit -m "chore: bump version to X.Y.Z"
   ```

8. Report what was done: old version → new version, bump type used, and the
   one-sentence VERSION_NOTES you wrote.

Do not tag or push. Do not modify any other files.
