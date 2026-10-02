# Jobright is disabled (bot check); see docs/step1-collect.md §2.6.
SELECTOR_VERSION = "jobright-2026-09-draft"

LIST = ("main",)
CARD_CONTAINERS = ("[class*='job-card']", "[class*='JobCard']", "article", "li")
CARD_ID_ATTRIBUTES: tuple[str, ...] = ()
JOB_LINK_PATTERNS = (r"/jobs/info/([A-Za-z0-9_-]+)",)
LOGGED_IN: tuple[str, ...] = ()  # none known yet; falls back to "no visible Sign in button"
DETAIL_READY = ("h1", "[class*='job-title']")
APPLY: tuple[str, ...] = ()  # generic "Apply" text search
CAPTURE_URL_PATTERNS = ("jobright.ai/swan/", "jobright.ai/api/")
