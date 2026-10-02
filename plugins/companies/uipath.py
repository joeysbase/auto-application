"""UiPath careers (ashby). Written by `auto-app add-company`; edit freely —
override methods here for anything specific to this company's board."""

from auto_app.platforms.ashby import AshbySource


class UiPath(AshbySource):
    name = "uipath"
    company = "UiPath"
    board = "uipath"
