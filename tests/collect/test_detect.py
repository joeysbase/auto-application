from auto_app.collect.detect import find_platforms


def test_finds_platforms_in_urls_and_pages():
    assert find_platforms("https://jobs.ashbyhq.com/uipath/cb98") == [("ashby", "uipath")]
    assert find_platforms('<a href="https://job-boards.greenhouse.io/anthropic/jobs/1">') == [("greenhouse", "anthropic")]
    assert find_platforms('<script src="https://boards.greenhouse.io/embed/job_board/js?for=stripe">') == [("greenhouse", "stripe")]
    assert find_platforms("https://jobs.lever.co/palantir/6ed7") == [("lever", "palantir")]
    assert find_platforms("https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/x") == [
        ("workday", "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite")
    ]
    assert find_platforms("https://careers.example.com/") == []
