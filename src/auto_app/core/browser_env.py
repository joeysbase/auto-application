"""The environment browsers are started with: yours, minus `DYLD_*`.

A `DYLD_LIBRARY_PATH` (e.g. `/opt/homebrew/lib` in ~/.zshrc) makes macOS load Homebrew's libpng / libjpeg / libtiff /
libgif in place of the ones Apple's ImageIO expects. Chrome for Testing (Playwright's Chromium) isn't a hardened app,
so it honours the variable and crashes at start (SIGBUS in ImageIO, "Mach rendezvous failed" from its helpers). Your
Google Chrome is hardened and ignores it — so the variable is dropped for every browser.
"""

import os


def browser_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if not k.startswith("DYLD_")}
