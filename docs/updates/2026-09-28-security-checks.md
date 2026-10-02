# Security-check detection, manual hand-off, Jobright disabled — 2026-09-28
**Step:** 1   **Status:** done

## What
- Detect bot-check pages (Cloudflare interstitials, `/challenge` URLs, Turnstile scripts, "security check" text) → stop the source + cooldown, instead of reporting "no cards".
- Opt-in **manual checks** (`--manual-checks`, or `sources.<site>.manual_checks: true`): bring the window forward, notify, wait (≤ 5 min, ≤ 5 per run) for the user to complete the check, then continue.
- Challenge detection runs before the HTTP-status check (challenges often return 403).
- Sources with no matching search now report `status=no_searches` instead of silently doing nothing.

## Why
Jobright served a Cloudflare check to the automated browser and the collector misreported it as "empty". The manual hand-off was tried; the check never loads in an automated browser, so **Jobright is disabled** (use by hand). Evasion (automation hiding, fingerprinting, simulated human input) was declined.

## Changes
`collect/guard.py` (`ChallengeDetected`, `detect_challenge`, `CHALLENGE_URL`), `sources/common/js.py` (`DETECT_CAPTCHA`), `collect/runner.py` (`_wait_for_manual_check`, no-search reporting), `core/config.py` (`manual_checks`, `max_manual_checks`, `manual_check_timeout_s`), CLI flag.

## Tests / verification
Fake-site tests: interstitial → blocked + cooldown; check completed → run continues; check not completed → timeout → blocked; source without a search → reported.
