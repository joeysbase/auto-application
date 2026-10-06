"""Which model runner judges each filter (Claude API, your Claude Code login, or DeepSeek) — shared by `filter run`
and `serve`'s filter batch. A setup problem (a missing key or CLI) is a SetupError: for `serve`, the filter is broken."""

import os
import shutil


class SetupError(Exception):
    pass


def runner_kind(cfg) -> str:
    kind = cfg.select.runner
    if kind == "auto":
        kind = "api" if os.environ.get("ANTHROPIC_API_KEY") else "claude-cli"
    return kind


def make_runner(cfg, filters, say=lambda text: None):
    """runner(filter, prompt, **kw) for these filters; say(text) tells which runner each uses."""
    from auto_app.select.runner import api_runner
    from auto_app.select.runner_cli import run_claude_cli
    from auto_app.select.runner_deepseek import is_deepseek, run_deepseek

    deepseek_filters = [f for f in filters if is_deepseek(f.config.model)]
    claude_filters = [f for f in filters if not is_deepseek(f.config.model)]
    kind = runner_kind(cfg)
    claude_runner = deepseek_runner = None
    if deepseek_filters:
        key = os.environ.get("DEEPSEEK_API_KEY") or ""
        if not key:
            raise SetupError(f"filters {[f.name for f in deepseek_filters]} use DeepSeek models but DEEPSEEK_API_KEY "
                             f"is not set — put it in .env")
        base = cfg.select.deepseek_base_url
        deepseek_runner = lambda flt, prompt, **kw: run_deepseek(flt, prompt, key, base, **kw)  # noqa: E731
        say(f"runner for {[f.name for f in deepseek_filters]}: DeepSeek API (DEEPSEEK_API_KEY)")
    if claude_filters:
        if kind == "api":
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise SetupError("AUTO_APP_SELECT__RUNNER=api but ANTHROPIC_API_KEY is not set — put it in .env, "
                                 "or use your Claude Code login (runner auto / claude-cli)")
            import anthropic

            claude_runner = api_runner(anthropic.Anthropic(max_retries=4))
            say("runner: Claude API (ANTHROPIC_API_KEY)")
        else:
            cli = cfg.select.claude_cli
            if not shutil.which(cli):
                raise SetupError(f"no ANTHROPIC_API_KEY and no `{cli}` CLI found — put the key in .env, or install "
                                 f"Claude Code and log in (or set AUTO_APP_SELECT__CLAUDE_CLI to its path)")
            claude_runner = lambda flt, prompt, **kw: run_claude_cli(flt, prompt, cli, **kw)  # noqa: E731
            say(f"runner: Claude Code CLI (`{cli}`, your login — usage counts against your subscription)")

    def runner(flt, prompt, **kw):
        return (deepseek_runner if is_deepseek(flt.config.model) else claude_runner)(flt, prompt, **kw)
    return runner
