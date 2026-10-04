"""Filesystem locations. Config, browser profiles, and collected data live in the project
directory (git-ignored) unless AUTO_APP_HOME points elsewhere."""

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Paths:
    home: Path

    @property
    def config_file(self) -> Path:
        return self.home / "config" / "searches.yaml"

    @property
    def profiles(self) -> Path:
        return self.home / "browser-profiles"

    @property
    def raw(self) -> Path:
        """File store: the debug copy (`--debug-files`), and where data lived before the database."""
        return self.home / "data" / "raw"

    @property
    def db(self) -> Path:
        return self.home / "data" / "auto_app.db"

    @property
    def logs(self) -> Path:
        return self.home / "data" / "logs"

    @property
    def failures(self) -> Path:
        return self.home / "data" / "failures"

    @property
    def screenshots(self) -> Path:
        return self.home / "data" / "screenshots"

    def profile(self, source: str) -> Path:
        return self.profiles / source

    @property
    def me(self) -> Path:
        """Step 3: your profile.yaml, answers.yaml and CV (git-ignored; templates in profile.example/)."""
        return self.home / "profile"

    @property
    def apply_policy(self) -> Path:
        return self.home / "config" / "apply.yaml"


def get_paths() -> Paths:
    home = os.environ.get("AUTO_APP_HOME")
    return Paths(home=Path(home).expanduser() if home else PROJECT_ROOT)
