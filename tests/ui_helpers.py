"""Build `auto-app ui` (AutoApp) the way the old single-section apps were built in tests."""

from auto_app.ui import AutoApp


def ui_app(browser, open_url=None, start="recommended", audit_mode=False, dark=lambda: False, ui=None, inbox=None):
    """Jobs (or, with audit_mode, Audit) section on `browser`."""
    return AutoApp(jobs=browser, audit=browser, inbox=inbox, start="audit" if audit_mode else "jobs", jobs_tab=start,
                   ui=ui, dark=dark, open_url=open_url or (lambda u: None))


def inbox_app(apps, dark=lambda: False, open_url=None, cv=None):
    return AutoApp(jobs=None, audit=None, inbox=apps, cv=cv, start="inbox", dark=dark, open_url=open_url or (lambda u: None))
