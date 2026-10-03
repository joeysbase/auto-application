"""Adobe careers (workday). Written by `auto-app source add`; edit freely —
override methods here for anything specific to this company's board."""

from auto_app.platforms.workday import WorkdaySource


class Adobe(WorkdaySource):
    name = "adobe"
    company = "Adobe"
    board = "https://adobe.wd5.myworkdayjobs.com/external_experienced"
