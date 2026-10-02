# Login via plain Chrome + mock-keychain fix — 2026-09-28
**Step:** 1   **Status:** done

## What
`auto-app login <source>` opens a **plain (non-automated) Chrome** on the source's profile so Google/SSO sign-in works, launched with `--use-mock-keychain`; the user quits it with **Cmd+Q**; the session is then verified headlessly and the landed URL is printed.

## Why
1. Google refuses sign-in in automation-flagged browsers ("This browser may not be secure").
2. The first plain-Chrome version lost the LinkedIn login: Playwright's Chrome uses a mock keychain, so cookies encrypted with the real Keychain key couldn't be decrypted and were discarded. Killing Chrome from the terminal also risked unflushed cookies.

## Changes
`collect/browser.py` (`open_plain_chrome`, `chrome_executable`, `close_chrome`), `cli.py` (`login` waits for Chrome to quit; prints the checked URL).

## Tests / verification
Reproduced with a local cookie-setting page: without the flag the collector saw no cookies; with it, the cookie survived. Regression unit test asserts the flag. LinkedIn login then verified live.
