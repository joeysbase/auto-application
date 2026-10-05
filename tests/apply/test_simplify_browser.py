"""The automated-Simplify browser: finding and copying the installed extension, the login window (automation markers
hidden, so Google sign-in works), and detecting a profile already in use."""

import json

import pytest

from auto_app.apply.simplify_browser import EXTENSION_ID, installed_extension, prepare_extension


def fake_chrome(tmp_path, versions=("3.1.6_0", "3.1.10_0")):
    chrome = tmp_path / "Chrome"
    for prof, v in (("Default", versions[0]), ("Profile 1", versions[1])):
        d = chrome / prof / "Extensions" / EXTENSION_ID / v
        (d / "_metadata").mkdir(parents=True)
        (d / "manifest.json").write_text(json.dumps({"name": "Simplify Copilot", "version": v}))
    return chrome


def test_newest_extension_is_copied_and_refreshed(tmp_path):
    chrome = fake_chrome(tmp_path)
    assert installed_extension(chrome).name == "3.1.10_0"  # 3.1.10 > 3.1.6 (numeric, not text)
    dest = prepare_extension(tmp_path / "copy", chrome)
    assert (dest / "manifest.json").is_file() and not (dest / "_metadata").exists()
    assert (dest / ".source-version").read_text() == "3.1.10_0"
    newer = chrome / "Default" / "Extensions" / EXTENSION_ID / "3.2.0_0"
    newer.mkdir(parents=True)
    (newer / "manifest.json").write_text("{}")
    prepare_extension(tmp_path / "copy", chrome)
    assert (dest / ".source-version").read_text() == "3.2.0_0"  # Chrome updated it → recopied
    with pytest.raises(RuntimeError, match="isn't installed"):
        prepare_extension(tmp_path / "x", tmp_path / "no-chrome")


def test_login_window_hides_automation_markers(tmp_path, monkeypatch):
    import auto_app.apply.simplify_browser as sb

    chrome = fake_chrome(tmp_path)
    monkeypatch.setattr(sb, "prepare_extension", lambda dest: prepare_extension(dest, chrome))
    seen = {}

    class PW:
        class chromium:
            @staticmethod
            def launch_persistent_context(profile, **kw):
                seen.update(profile=profile, **kw)
                return "ctx"
    assert sb.launch(PW, tmp_path / "profiles", quiet=True) == "ctx"
    assert seen["profile"].endswith("simplify") and seen["headless"] is False
    assert "--disable-blink-features=AutomationControlled" in seen["args"]  # navigator.webdriver stays false
    assert seen["ignore_default_args"] == ["--enable-automation"]  # no "controlled by automated software" bar
    assert any(a.startswith("--load-extension=") for a in seen["args"])


def test_profile_in_use_reads_the_lock(tmp_path):
    import os

    from auto_app.apply.simplify_browser import profile_in_use

    assert profile_in_use(tmp_path) is None  # no lock
    os.symlink(f"host.local-{os.getpid()}", tmp_path / "SingletonLock")
    assert profile_in_use(tmp_path) == os.getpid()  # a live process holds it
    (tmp_path / "SingletonLock").unlink()
    os.symlink("host.local-999999", tmp_path / "SingletonLock")
    assert profile_in_use(tmp_path) is None  # stale: Chromium recovers by itself


def test_browsers_start_without_dyld_overrides(monkeypatch):
    # DYLD_LIBRARY_PATH=/opt/homebrew/lib made Chrome for Testing load Homebrew's libpng & co. and crash in ImageIO
    from auto_app.core.browser_env import browser_env

    monkeypatch.setenv("DYLD_LIBRARY_PATH", "/opt/homebrew/lib")
    monkeypatch.setenv("DYLD_FALLBACK_LIBRARY_PATH", "/x")
    env = browser_env()
    assert not any(k.startswith("DYLD_") for k in env) and env["PATH"] == __import__("os").environ["PATH"]
