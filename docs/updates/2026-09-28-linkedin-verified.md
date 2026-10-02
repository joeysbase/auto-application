# LinkedIn verified live — 2026-09-28
**Step:** 1   **Status:** done

## What
Real LinkedIn search collected: 25 cards per page read correctly; job pages saved with full "About the job" descriptions, header info (company, title, location, posted time, applicants, workplace/employment type, salary) and apply kind.

Detail-ready selector updated for the 2026-09 layout (hashed class names): `h2:has-text("About the job")`, `[data-testid="expandable-text-box"]`. Before, every page waited 15 s for a stale selector and showed `ready=N`.

## Findings
- LinkedIn job data is in HTML; the only captured JSON (`JobSeekerPreferences`) is unrelated.
- Easy Apply jobs have no external apply URL.
- ~400 KB per job with screenshots and page text on.

## Changes
`sources/linkedin/selectors.py` (`DETAIL_READY`, `SELECTOR_VERSION = linkedin-2026-09-28`).

## Tests / verification
New selectors matched all saved LinkedIn pages.
