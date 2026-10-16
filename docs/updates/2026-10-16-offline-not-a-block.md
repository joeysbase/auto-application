# Collect: no network isn't a block — 2026-10-16

Found twice under `serve` (2026-10-08, 2026-10-15): the Mac lost its connection, LinkedIn's session check failed with
`net::ERR_INTERNET_DISCONNECTED`, and that counted as a block, so LinkedIn sat in a 48 h cooldown and every round
collected nothing until `source health --reset`.

**What counts as offline:** a navigation error matching `guard.OFFLINE_ERRORS`: `ERR_INTERNET_DISCONNECTED`,
`ERR_NAME_NOT_RESOLVED`, `ERR_NETWORK_CHANGED`, `ERR_ADDRESS_UNREACHABLE`, `ERR_NETWORK_IO_SUSPENDED`. These are this
machine's network, not the site's answer. Timeouts, refused or reset connections, and HTTP statuses are unchanged
(they can be the site).

**What happens:** `CollectionRunner._navigate` raises `NetworkDown` (a `HardBlock`, so the source stops at once,
including mid-run: no "3 consecutive failed cards" escalation). `record_block` gives it status `offline`, records
`last_run_at` only (no cooldown, health stays as it was), and sends no notification; `_on_block` saves no
screenshots. The search run's stop reason is `error`. In `serve`, the round lists
`linkedin: offline — no network: …` under problems, and the next round tries again.

**Tests** (375 with the web frontend's): `test_no_network_stops_this_run_without_a_cooldown`, `test_network_lost_mid_run_is_not_a_block`
(fake site: `offline_paths`, aborted as `internetdisconnected`), `test_no_network_is_a_problem_but_no_stop`.
