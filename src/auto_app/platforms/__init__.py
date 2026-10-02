"""Job platforms: base classes for company sources (docs/step1-collect.md §2.2).

A platform base knows how to search one platform's job boards (Workday, Ashby …) but is not a
source itself. Each company is a small plugin under `plugins/companies/` that subclasses one and
sets its `name`, `company` and `board` — `auto-app add-company` writes that file.
"""

from auto_app.platforms.ashby import AshbySource
from auto_app.platforms.greenhouse import GreenhouseSource
from auto_app.platforms.lever import LeverSource
from auto_app.platforms.workday import WorkdaySource

PLATFORMS = {cls.platform: cls for cls in (AshbySource, GreenhouseSource, LeverSource, WorkdaySource)}

__all__ = ["PLATFORMS", "AshbySource", "GreenhouseSource", "LeverSource", "WorkdaySource"]
