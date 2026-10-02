# Unverified on the live site; see docs/step1-collect.md §2.6.
SELECTOR_VERSION = "handshake-2026-09-draft"

LIST = ("[data-hook='search-results']", "main")
CARD_CONTAINERS = ("[data-hook*='card']", "[class*='card']", "li")
CARD_ID_ATTRIBUTES: tuple[str, ...] = ()
JOB_LINK_PATTERNS = (r"/job-search/(\d+)", r"/stu/jobs/(\d+)", r"/jobs/(\d+)")
LOGGED_IN: tuple[str, ...] = ()
DETAIL_READY = ("h1", "[data-hook='job-details-page']")
APPLY: tuple[str, ...] = ()
CAPTURE_URL_PATTERNS = ("graphql", "/api/")
