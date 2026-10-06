"""Site accounts for the browser agent (docs/step3-agent.md §6): the email and password **you** provide.

- The email: `accounts.email` in profile.yaml (default: your profile email) — one for every site.
- The password: in the **macOS Keychain** (service `auto-app`, one default entry, optionally one per site), set with
  `auto-app apply account set`; never in a file, never shown to the model (the agent's `fill_secret` tool types it).
- Each account the agent created or signed in to is recorded (the `accounts` table; `auto-app apply accounts`).
"""

import re
import subprocess
from urllib.parse import urlparse

from sqlalchemy import select

SERVICE = "auto-app"
DEFAULT = "default"


def account_site(url: str) -> str:
    """The site an account belongs to: the full host (Workday's accounts are per company: nvidia.wd5.myworkdayjobs.com)."""
    return (urlparse(url).hostname or "").lower()


def account_email(me) -> str | None:
    p = me.profile
    if p is None:
        return None
    acc = getattr(p, "accounts", None)
    return (acc.email if acc and acc.email else None) or p.email


EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def password_problems(password: str) -> list[str]:
    """What a sign-up would reject (Workday and most applicant-tracking sites ask for all of these)."""
    out = []
    if len(password) < 8:
        out.append("at least 8 characters")
    if not re.search(r"[a-z]", password):
        out.append("a lowercase letter")
    if not re.search(r"[A-Z]", password):
        out.append("an uppercase letter")
    if not re.search(r"\d", password):
        out.append("a number")
    if not re.search(r"[^A-Za-z0-9]", password):
        out.append("a symbol (e.g. ! @ # $)")
    return out


def find(store, site: str) -> dict | None:
    from auto_app.core.db import tables as T

    with store.engine.connect() as conn:
        row = conn.execute(select(T.accounts).where(T.accounts.c.site == site)).mappings().first()
    return dict(row) if row else None


def plan(email: str | None, site: str, known: dict | None, site_password: bool, default_password: bool) -> str:
    """What the agent does on a site that wants an account: sign in only with account info for that site (an account
    on record, or a password you set for it); otherwise sign up — with at least an email and a valid password."""
    missing = []
    if not email or not EMAIL.match(email):
        missing.append("an account email (accounts.email in profile/profile.yaml)")
    if not (site_password or default_password):
        missing.append("a password (auto-app apply account set)")
    if missing:
        return ("missing: " + " and ".join(missing) + " — don't sign in or sign up; call done(\"action_needed\", "
                f"\"{site} needs an account — set " + " and ".join(m.split(' (')[0] for m in missing) + "\")")
    if known and str(known.get("note") or "").startswith("not confirmed"):
        known = None  # an older record of a sign-in that never worked: not an account
    if known or site_password:
        when = f" (on record since {str(known['created_at'])[:10]})" if known else " (a password set for this site)"
        return (f"email {email} · password set · an account for {site}{when} → SIGN IN with fill_secret. If the "
                "site says there's no such account, create one with the same email and password.")
    return (f"email {email} · password set · no account for {site} on record → CREATE AN ACCOUNT (sign up) with "
            "fill_secret for the email and both password fields. If the site says the email is already registered, "
            "sign in with the same email and password instead; a wrong password → done(\"action_needed\").")


def _security(*args: str, run=subprocess.run):
    return run(["security", *args], capture_output=True, text=True)


def set_password(email: str, password: str, site: str | None = None, run=subprocess.run) -> None:
    r = _security("add-generic-password", "-U", "-s", SERVICE, "-a", f"{site or DEFAULT}:{email}", "-w", password,
                  "-l", f"auto-app account ({site or 'every site'})", run=run)
    if r.returncode != 0:
        raise RuntimeError(f"couldn't save it in the Keychain: {r.stderr.strip() or r.returncode}")


def has_password(email: str, site: str | None = None, run=subprocess.run) -> bool:
    """Is a password stored for exactly this site (or, with site None, the default one)?"""
    r = _security("find-generic-password", "-s", SERVICE, "-a", f"{site or DEFAULT}:{email}", run=run)
    return r.returncode == 0


def get_password(email: str, site: str | None = None, run=subprocess.run) -> str | None:
    """The site's own password if one was set, else the default; None if neither."""
    for who in ([site] if site else []) + [DEFAULT]:
        r = _security("find-generic-password", "-s", SERVICE, "-a", f"{who}:{email}", "-w", run=run)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.rstrip("\n")
    return None


def record(store, site: str, email: str, created: bool, application_id=None, note: str | None = None) -> None:
    from auto_app.core.db import tables as T
    from auto_app.core.models import utcnow

    t, now = T.accounts, utcnow()
    with store.engine.begin() as conn:
        row = conn.execute(select(t).where(t.c.site == site)).mappings().first()
        if row is None:
            conn.execute(t.insert().values(site=site, email=email, created_by_agent=created, created_at=now,
                                           last_used_at=now, application_id=application_id, note=note))
        else:
            conn.execute(t.update().where(t.c.site == site).values(
                last_used_at=now, note=note if note is not None else row["note"],
                created_by_agent=row["created_by_agent"] or created))


def rows(store) -> list[dict]:
    from auto_app.core.db import tables as T

    with store.engine.connect() as conn:
        return [dict(r) for r in conn.execute(select(T.accounts).order_by(T.accounts.c.last_used_at.desc())).mappings()]
