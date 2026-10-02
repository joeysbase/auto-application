"""NVIDIA careers (workday). Written by `auto-app add-company`; edit freely —
override methods here for anything specific to this company's board."""

from auto_app.platforms.workday import WorkdaySource


class NVIDIA(WorkdaySource):
    name = "nvidia"
    company = "NVIDIA"
    board = "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
