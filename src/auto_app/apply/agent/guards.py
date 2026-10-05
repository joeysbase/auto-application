"""The rules the agent's tools enforce, whatever the model or a page says (docs/step3-agent.md §5).

- `click_refusal`: no final submit outside submit mode; no Next / Continue in start mode (Next sends the step to the
  site). An "Apply" link or button that leads *to* the form is fine — only a submit inside a filled form is final.
- `allowed_navigation`: the main frame stays on the job's site and the applicant-tracking / sign-in sites it uses.
- sensitive fields (work authorization, sponsorship, demographics, consent …) take only the answer `answer_for` gave.
"""

import re
from urllib.parse import urlparse

NEXT = re.compile(r"^\s*(next|continue|next step|proceed|save (and|&) (continue|next)|continue to next step)\b", re.I)
FINAL = re.compile(r"^\s*(submit( (my |your |the )?application)?|send( (my )?application)?|complete( (my )?application)?|"
                   r"finish( application)?|review and submit|submit and finish)\s*$", re.I)
APPLY = re.compile(r"^\s*apply( now| for this (job|position|role))?\s*$", re.I)  # final only as a filled form's button

# applicant-tracking systems and sign-in pages an application may move through (registrable domains)
ATS = ("greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com", "myworkdaysite.com", "workday.com",
       "smartrecruiters.com", "icims.com", "jobvite.com", "workable.com", "bamboohr.com", "rippling.com",
       "rippling-ats.com", "phenompeople.com", "successfactors.com", "sapsf.com", "taleo.net", "oraclecloud.com",
       "ultipro.com", "ukg.com", "paylocity.com", "adp.com", "dayforcehcm.com", "breezy.hr", "recruitee.com",
       "teamtailor.com", "personio.de", "personio.com", "eightfold.ai", "avature.net", "jazzhr.com",
       "applytojob.com", "hire.trakstar.com", "comeet.co", "pinpointhq.com")


def site(host: str) -> str:
    """The registrable part of a host name (careers.adobe.com → adobe.com; good enough for job sites)."""
    parts = (host or "").lower().split(".")
    if len(parts) >= 3 and parts[-2] in ("co", "com", "ac", "gov", "org") and len(parts[-1]) == 2:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def allowed_sites(apply_url: str) -> set[str]:
    return {site(urlparse(apply_url).hostname or ""), *ATS}


def allowed_navigation(url: str, allowed: set[str]) -> bool:
    u = urlparse(url)
    if u.scheme in ("about", "data", "blob", "javascript", ""):
        return True
    return site(u.hostname or "") in allowed


def click_refusal(mode: str, name: str, role: str, in_filled_form: bool) -> str | None:
    """Why this click isn't allowed in this mode, or None."""
    final = bool(FINAL.match(name or "")) or (bool(APPLY.match(name or "")) and in_filled_form and role == "button")
    if final and mode != "submit":
        return ("refused: that submits the application — only when the user presses s. "
                + ("Stop here and call done(\"filled\")." if mode == "fill" else
                   "Starting a job never submits: call done when step 1 is filled."))
    if mode == "start" and NEXT.match(name or ""):
        return ("refused: Next sends this step to the site, which a dry run must not do. Fill this step, then call done "
                "(more_steps=true); the next steps are filled when the user fills or submits.")
    return None
