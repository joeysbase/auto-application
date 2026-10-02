"""Find where an apply link really points, without clicking anything."""

import logging
from urllib.parse import parse_qs, urlparse

import httpx

log = logging.getLogger(__name__)

REDIRECT_PARAMS = ("url", "u", "target", "redirect", "redirect_url", "redirectUrl", "dest", "destination", "to")
MAX_HOPS = 5


def decode_redirect(href: str) -> str | None:
    """Target URL embedded in a redirect wrapper's query string, if any."""
    q = parse_qs(urlparse(href).query)
    for key in REDIRECT_PARAMS:
        for value in q.get(key, []):
            if value.startswith(("http://", "https://")):
                return value
    return None


async def resolve(href: str | None, *, is_site_host, client: httpx.AsyncClient | None = None) -> list[str]:
    """Hops from `href` to the first off-site URL. Empty if there's nothing to resolve.

    Direct employer links need no request. Site-hosted wrappers are decoded from the
    query string when possible, otherwise followed with plain HTTP (no browser cookies).
    """
    if not href or not is_site_host(href):
        return []
    decoded = decode_redirect(href)
    if decoded:
        return [href, decoded]
    hops = [href]
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=10, follow_redirects=False)
    try:
        url = href
        for _ in range(MAX_HOPS):
            resp = await client.get(url)
            location = resp.headers.get("location")
            if not resp.is_redirect or not location:
                break
            url = str(resp.url.join(location))
            hops.append(url)
            if not is_site_host(url):
                break
    except httpx.HTTPError as e:
        log.debug("apply link resolution stopped at %s: %s", hops[-1], e)
    finally:
        if own_client:
            await client.aclose()
    return hops if len(hops) > 1 else []
