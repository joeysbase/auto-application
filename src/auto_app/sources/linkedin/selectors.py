# Verified on the live site: classic search page (/jobs/search/) 2026-09-28; newer search page
# (/jobs/search-results/, what the /jobs/ search bar opens) 2026-10-01. See docs/step1-collect.md §2.7.
SELECTOR_VERSION = "linkedin-2026-10-01"

# classic page first; the newer page's results list is the first lazy-column
LIST = (".scaffold-layout__list", ".jobs-search-results-list", "div.jobs-search-results", '[data-testid="lazy-column"]')
CARD_CONTAINERS = ("li[data-occludable-job-id]", ".job-card-container", "li")
CARD_ID_ATTRIBUTES = (
    "data-occludable-job-id",
    "data-job-id",
    r"componentkey~^job-card-component-ref-(\d+)$",  # newer page: cards are div[role=button], no links
)
JOB_LINK_PATTERNS = (r"/jobs/view/(\d+)", r"[?&]currentJobId=(\d+)")
PROMOTED_TEXTS = ("Promoted",)
# Newer search page: after the exact matches it lists "related" jobs that ignore the filters, below this note.
NOT_EXACT_TEXTS = ("may not be exact matches",)
LOGGED_IN = (".global-nav__me", "img.global-nav__me-photo", "#global-nav")
DETAIL_READY = (
    'h2:has-text("About the job")',  # 2026-09 layout: hashed class names, so match the section heading
    '[data-testid="expandable-text-box"]',
    ".jobs-description__content",
    "#job-details",
    ".job-details-jobs-unified-top-card__job-title",
    ".jobs-unified-top-card",
)
APPLY = (".jobs-apply-button", "button.jobs-apply-button", "a.jobs-apply-button")
CAPTURE_URL_PATTERNS = ("voyagerJobsDash", "/voyager/api/jobs")
