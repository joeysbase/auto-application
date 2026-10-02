"""Test company source (Workday)."""

from auto_app.platforms.workday import WorkdaySource


class Nvidia(WorkdaySource):
    name = "nvidia"
    company = "NVIDIA"
    board = "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"  # normalized when loaded
