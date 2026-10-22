from bosshunter.requirement_filter import limits, required_degree, required_years, requirement_reason

PICKED = {"experience_limit": ["经验不限", "应届生", "1年以内"], "degree_limit": ["本科", "硕士"]}
CONFIG = {"platforms": {name: {"search": {"filters": PICKED}} for name in ("liepin", "51job", "zhilian", "zhilian_campus")}}


def test_each_platform_has_its_own_picked_limits_and_none_picked_means_no_limit():
    assert limits(CONFIG, "liepin") == (["经验不限", "应届生", "1年以内"], ["本科", "硕士"])
    own = {"platforms": {"liepin": {"search": {"filters": {"experience_limit": ["1-3年"]}}}, "boss": {"search": {"filters": {"experience": ["应届生"]}}}}}
    assert limits(own, "liepin") == (["1-3年"], [])
    assert limits(own, "51job") == ([], [])             # BOSS's own lists are not a limit for the others
    assert limits({}, "liepin") == ([], [])


def test_years_come_from_the_label_then_from_the_jd_sentences_about_experience():
    assert required_years("经验不限", "") == 0
    assert required_years("应届生", "") == 0
    assert required_years("1-3年", "") == 1
    assert required_years("5年以上", "") == 5
    assert required_years("", "岗位要求：1. 本科及以上；2. 3年以上Java开发经验；3. 熟悉Spring") == 3
    assert required_years("", "有3年以上经验者优先") is None             # a wish is not a requirement
    assert required_years("", "公司成立10年以上，欢迎加入") is None        # not about the candidate
    assert required_years("", "负责Agent开发") is None


def test_degree_comes_from_the_label():
    assert required_degree("1-3年·本科", "") == 3
    assert required_degree("大专", "") == 2
    assert required_degree("学历不限", "") is None


def test_postings_asking_for_more_experience_than_allowed_are_dropped():
    assert requirement_reason(CONFIG, "liepin", "3-5年", "本科") .startswith("要求 3 年以上经验")
    assert requirement_reason(CONFIG, "51job", "1-3年·本科", "")
    assert requirement_reason(CONFIG, "zhilian", "经验不限", "本科", "要求5年以上相关开发经验").startswith("要求 5 年")
    assert requirement_reason(CONFIG, "zhilian", "经验不限", "本科", "有3年以上经验者优先") == ""


def test_fresh_and_unlimited_postings_pass_and_the_default_is_no_limit():
    assert requirement_reason(CONFIG, "liepin", "经验不限", "本科") == ""
    assert requirement_reason(CONFIG, "liepin", "应届生", "硕士") == ""
    assert requirement_reason(CONFIG, "liepin", "1年以内", "学历不限") == ""
    assert requirement_reason({}, "liepin", "10年以上", "博士") == ""


def test_degrees_outside_the_list_are_dropped():
    assert requirement_reason(CONFIG, "liepin", "经验不限", "大专").startswith("学历要求（大专）")
    assert requirement_reason(CONFIG, "liepin", "经验不限", "博士").startswith("学历要求（博士）")
    assert requirement_reason(CONFIG, "liepin", "经验不限", "硕士") == ""
