import asyncio
import json
import subprocess
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

import bot

OPERATOR_ID = 12345
OTHER_ID = 99999


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bot, "DRAFTS_DIR", tmp_path / "drafts")
    # ARTICLES_DIR must stay under REPO_ROOT, same invariant as production
    # (REPO_ROOT / "site" / "content" / "articles") -- _run_content_cycle relies on it via
    # path.relative_to(REPO_ROOT).
    monkeypatch.setattr(bot, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(bot, "ARTICLES_DIR", tmp_path / "articles")
    monkeypatch.setattr(bot, "_state", {"paused": False})
    monkeypatch.setattr(bot, "OPERATOR_TELEGRAM_ID", OPERATOR_ID)
    monkeypatch.setattr(bot, "AGENTMAIL_API_KEY", "")
    monkeypatch.setattr(bot, "AGENTMAIL_INBOX_ID", "")
    monkeypatch.setattr(bot, "GITHUB_TOKEN", "")
    monkeypatch.setattr(bot, "CONTENT_PIPELINE_INTERVAL_HOURS", 168.0)
    monkeypatch.setattr(bot, "KV_REST_API_URL", "")
    monkeypatch.setattr(bot, "KV_REST_API_TOKEN", "")


def _fake_run(returncode=0, stdout="ok", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


# ---------------------------------------------------------------- state

def test_state_roundtrip(tmp_path):
    bot._state["paused"] = True
    assert bot._save_state() is True

    bot._state["paused"] = False
    bot._load_state()
    assert bot._state["paused"] is True


def test_state_missing_file_defaults_unpaused():
    bot._load_state()
    assert bot._state["paused"] is False


def test_state_corrupt_file_starts_fresh(tmp_path):
    bot.STATE_FILE.write_text("not json")
    bot._load_state()
    assert bot._state["paused"] is False


# ---------------------------------------------------------------- spend parsing

def test_handle_spend_valid_format_calls_create():
    with patch.object(bot, "_create_spend_request", return_value="lsrq_abc") as mock_create:
        result = bot._handle_spend("15|Porkbun|https://porkbun.com|renew domain")
    mock_create.assert_called_once_with(1500, "Porkbun", "https://porkbun.com", "renew domain")
    assert result == "lsrq_abc"


def test_handle_spend_wrong_number_of_fields_returns_usage():
    with patch.object(bot, "_create_spend_request") as mock_create:
        result = bot._handle_spend("15|Porkbun")
    mock_create.assert_not_called()
    assert "Format:" in result


def test_handle_spend_non_numeric_amount_rejected():
    with patch.object(bot, "_create_spend_request") as mock_create:
        result = bot._handle_spend("fifteen|Porkbun|https://porkbun.com|renew")
    mock_create.assert_not_called()
    assert "dollar amount" in result


def test_handle_spend_non_positive_amount_rejected():
    with patch.object(bot, "_create_spend_request") as mock_create:
        result = bot._handle_spend("0|Porkbun|https://porkbun.com|renew")
    mock_create.assert_not_called()
    assert "positive" in result


def test_create_spend_request_pads_short_context():
    captured = {}

    def fake_run(args, **kwargs):
        captured["context"] = args[args.index("--context") + 1]
        return _fake_run(stdout="lsrq_x")

    with patch.object(subprocess, "run", side_effect=fake_run):
        bot._create_spend_request(1500, "Porkbun", "https://porkbun.com", "short")
    assert len(captured["context"]) >= 100


def test_create_spend_request_surfaces_stderr_on_failure():
    with patch.object(subprocess, "run", return_value=_fake_run(returncode=1, stderr="denied")):
        result = bot._create_spend_request(1500, "Porkbun", "https://porkbun.com", "x" * 100)
    assert "denied" in result
    assert "failed" in result


# ---------------------------------------------------------------- routing / kill switch

@pytest.mark.asyncio
async def test_route_status_works_while_paused():
    bot._state["paused"] = True
    with patch.object(bot, "_handle_status", return_value="status-ok"):
        result = await bot._route("status")
    assert result == "status-ok"


@pytest.mark.asyncio
async def test_route_spend_blocked_while_paused():
    bot._state["paused"] = True
    with patch.object(bot, "_handle_spend") as mock_spend:
        result = await bot._route("spend 15|Porkbun|https://porkbun.com|renew")
    mock_spend.assert_not_called()
    assert "Paused" in result


@pytest.mark.asyncio
async def test_route_chat_blocked_while_paused():
    bot._state["paused"] = True
    with patch.object(bot, "_ask_claude") as mock_ask:
        result = await bot._route("what's next?")
    mock_ask.assert_not_called()
    assert "Paused" in result


@pytest.mark.asyncio
async def test_route_spend_allowed_when_not_paused():
    with patch.object(bot, "_handle_spend", return_value="requested") as mock_spend:
        result = await bot._route("spend 15|Porkbun|https://porkbun.com|renew")
    mock_spend.assert_called_once_with("15|Porkbun|https://porkbun.com|renew")
    assert result == "requested"


@pytest.mark.asyncio
async def test_route_pause_then_resume():
    assert "Paused" in await bot._route("pause")
    assert bot._state["paused"] is True
    assert "Resumed" in await bot._route("resume")
    assert bot._state["paused"] is False


# ---------------------------------------------------------------- operator authorization

class FakeBot:
    def __init__(self):
        self.sent = []
        self.calls = []  # full kwargs per send_message call, for tests that need reply_markup

    async def send_message(self, chat_id, text=None, reply_markup=None, **kwargs):
        self.sent.append((chat_id, text))
        self.calls.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup, **kwargs})


def _fake_update(user_id, text):
    return SimpleNamespace(
        message=SimpleNamespace(text=text),
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=777),
    )


@pytest.mark.asyncio
async def test_non_operator_is_rejected_and_route_never_runs():
    fake_bot = FakeBot()
    context = SimpleNamespace(bot=fake_bot)
    with patch.object(bot, "_route") as mock_route:
        await bot.on_message(_fake_update(OTHER_ID, "spend 15|Porkbun|https://porkbun.com|x"), context)
    mock_route.assert_not_called()
    assert fake_bot.sent == [(777, "I only take commands from my operator.")]


@pytest.mark.asyncio
async def test_operator_message_reaches_route():
    fake_bot = FakeBot()
    context = SimpleNamespace(bot=fake_bot)
    with patch.object(bot, "_route", return_value="handled") as mock_route:
        await bot.on_message(_fake_update(OPERATOR_ID, "status"), context)
    mock_route.assert_called_once_with("status")
    assert fake_bot.sent == [(777, "handled")]


@pytest.mark.asyncio
async def test_app_command_sends_web_app_button_and_never_reaches_route():
    fake_bot = FakeBot()
    context = SimpleNamespace(bot=fake_bot)
    with patch.object(bot, "_route") as mock_route:
        await bot.on_message(_fake_update(OPERATOR_ID, "app"), context)
    mock_route.assert_not_called()
    assert len(fake_bot.calls) == 1
    markup = fake_bot.calls[0]["reply_markup"]
    button = markup.inline_keyboard[0][0]
    assert button.web_app.url == bot.DASHBOARD_URL


@pytest.mark.asyncio
async def test_dashboard_alias_also_sends_web_app_button():
    fake_bot = FakeBot()
    context = SimpleNamespace(bot=fake_bot)
    await bot.on_message(_fake_update(OPERATOR_ID, "Dashboard"), context)
    assert fake_bot.calls[0]["reply_markup"].inline_keyboard[0][0].web_app.url == bot.DASHBOARD_URL


# ---------------------------------------------------------------- drafts

def test_save_and_load_draft_roundtrip():
    draft_id = bot._save_draft("email", {"to": "x@example.com", "subject": "hi"}, "body text")
    loaded = bot._load_draft(draft_id)
    assert loaded["kind"] == "email"
    assert loaded["status"] == "draft"
    assert loaded["body"] == "body text"
    assert loaded["to"] == "x@example.com"


def test_load_draft_missing_returns_none():
    assert bot._load_draft("nope") is None


def test_list_open_drafts_excludes_sent():
    open_id = bot._save_draft("email", {"to": "a@example.com", "subject": "s"}, "b")
    sent_id = bot._save_draft("email", {"to": "b@example.com", "subject": "s"}, "b")
    bot._mark_draft_sent(bot._load_draft(sent_id))
    ids = [d["id"] for d in bot._list_open_drafts()]
    assert ids == [open_id]


@pytest.mark.asyncio
async def test_draft_email_bad_format_skips_claude_call():
    with patch.object(bot, "_run_draft") as mock_run:
        result = await bot._handle_draft_email("only|two")
    mock_run.assert_not_called()
    assert "Format:" in result


@pytest.mark.asyncio
async def test_draft_email_valid_saves_draft():
    with patch.object(bot, "_run_draft", return_value="Dear team, ..."):
        result = await bot._handle_draft_email("x@example.com|Hi|say hello")
    assert "Dear team" in result
    drafts = bot._list_open_drafts()
    assert len(drafts) == 1
    assert drafts[0]["kind"] == "email"
    assert drafts[0]["to"] == "x@example.com"


@pytest.mark.asyncio
async def test_draft_application_bad_format_skips_claude_call():
    with patch.object(bot, "_run_draft") as mock_run:
        result = await bot._handle_draft_application("OnlyOneField")
    mock_run.assert_not_called()
    assert "Format:" in result


@pytest.mark.asyncio
async def test_draft_application_valid_saves_draft_with_no_send_mentioned():
    with patch.object(bot, "_browser_fetch", return_value=(True, "Kajabi affiliate terms: 30%")):
        with patch.object(bot, "_run_draft", return_value="Answers here") as mock_draft:
            result = await bot._handle_draft_application("Kajabi|https://kajabi.com/affiliates")
    assert "no `send`" in result
    assert "Kajabi affiliate terms: 30%" in mock_draft.call_args[0][0]
    drafts = bot._list_open_drafts()
    assert drafts[0]["kind"] == "application"


@pytest.mark.asyncio
async def test_draft_application_fetch_failure_still_drafts_with_failure_noted():
    with patch.object(bot, "_browser_fetch", return_value=(False, "timed out")):
        with patch.object(bot, "_run_draft", return_value="Answers here") as mock_draft:
            await bot._handle_draft_application("Kajabi|https://kajabi.com/affiliates")
    prompt = mock_draft.call_args[0][0]
    assert "Could not fetch" in prompt
    assert "timed out" in prompt


@pytest.mark.asyncio
async def test_send_unknown_draft():
    result = await bot._handle_send("doesnotexist")
    assert "No open draft" in result


@pytest.mark.asyncio
async def test_send_application_refused_even_with_credentials_configured(monkeypatch):
    # The critical case: application-kind drafts must be refused unconditionally, not
    # merely because credentials happen to be missing. Configuring credentials must not
    # open a path to sending one.
    monkeypatch.setattr(bot, "AGENTMAIL_API_KEY", "fake-key")
    monkeypatch.setattr(bot, "AGENTMAIL_INBOX_ID", "fake-inbox")
    draft_id = bot._save_draft("application", {"program": "Kajabi", "url": "https://x"}, "answers")
    with patch.object(httpx, "AsyncClient") as mock_client_cls:
        result = await bot._handle_send(draft_id)
    mock_client_cls.assert_not_called()
    assert "Applications don't get an automated send" in result
    assert bot._load_draft(draft_id)["status"] == "draft"


@pytest.mark.asyncio
async def test_send_email_without_credentials_configured():
    draft_id = bot._save_draft("email", {"to": "x@example.com", "subject": "s"}, "body")
    result = await bot._handle_send(draft_id)
    assert "not configured" in result
    assert bot._load_draft(draft_id)["status"] == "draft"


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class FakeAsyncClient:
    def __init__(self, response, **kwargs):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        return self._response


@pytest.mark.asyncio
async def test_send_email_success_marks_sent(monkeypatch):
    monkeypatch.setattr(bot, "AGENTMAIL_API_KEY", "fake-key")
    monkeypatch.setattr(bot, "AGENTMAIL_INBOX_ID", "fake-inbox")
    draft_id = bot._save_draft("email", {"to": "x@example.com", "subject": "s"}, "body")
    with patch.object(httpx, "AsyncClient", lambda **kw: FakeAsyncClient(FakeResponse(200))):
        result = await bot._handle_send(draft_id)
    assert f"Sent `{draft_id}`" in result
    assert bot._load_draft(draft_id)["status"] == "sent"


@pytest.mark.asyncio
async def test_send_email_failure_keeps_draft_open(monkeypatch):
    monkeypatch.setattr(bot, "AGENTMAIL_API_KEY", "fake-key")
    monkeypatch.setattr(bot, "AGENTMAIL_INBOX_ID", "fake-inbox")
    draft_id = bot._save_draft("email", {"to": "x@example.com", "subject": "s"}, "body")
    with patch.object(httpx, "AsyncClient", lambda **kw: FakeAsyncClient(FakeResponse(401, "bad key"))):
        result = await bot._handle_send(draft_id)
    assert "Send failed" in result
    assert bot._load_draft(draft_id)["status"] == "draft"


@pytest.mark.asyncio
async def test_route_draft_and_send_blocked_while_paused():
    bot._state["paused"] = True
    with patch.object(bot, "_handle_draft_email") as mock_draft, \
         patch.object(bot, "_handle_send") as mock_send:
        draft_result = await bot._route("draft email x@example.com|s|b")
        send_result = await bot._route("send abc123")
    mock_draft.assert_not_called()
    mock_send.assert_not_called()
    assert "Paused" in draft_result
    assert "Paused" in send_result


@pytest.mark.asyncio
async def test_route_drafts_list_works_while_paused():
    bot._state["paused"] = True
    with patch.object(bot, "_handle_drafts_list", return_value="listed"):
        result = await bot._route("drafts")
    assert result == "listed"


# ---------------------------------------------------------------- browser run

class _FakeProc:
    def __init__(self, returncode, stdout=b"", stderr=b""):
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self):
        return self._stdout, self._stderr


@pytest.mark.asyncio
async def test_browser_fetch_success():
    with patch.object(asyncio, "create_subprocess_exec",
                      return_value=_FakeProc(0, stdout=b"URL: x\n\npage text")):
        ok, content = await bot._browser_fetch("https://example.com")
    assert ok is True
    assert content == "URL: x\n\npage text"


@pytest.mark.asyncio
async def test_browser_fetch_nonzero_exit_returns_stderr():
    with patch.object(asyncio, "create_subprocess_exec",
                      return_value=_FakeProc(2, stderr=b"refused: only http/https URLs")):
        ok, content = await bot._browser_fetch("ftp://example.com")
    assert ok is False
    assert "refused" in content


@pytest.mark.asyncio
async def test_browser_fetch_timeout_returns_failure_not_exception():
    async def fake_exec(*a, **kw):
        return _FakeProc(0)

    async def fake_wait_for(coro, timeout):
        coro.close()  # avoid an unawaited-coroutine warning from the one wait_for would have run
        raise asyncio.TimeoutError()

    with patch.object(asyncio, "create_subprocess_exec", side_effect=fake_exec):
        with patch.object(asyncio, "wait_for", side_effect=fake_wait_for):
            ok, content = await bot._browser_fetch("https://example.com")
    assert ok is False
    assert "browser_run" in content


# ---------------------------------------------------------------- content pipeline

VALID_ARTICLE_RAW = """TITLE: Best Course Platform for Fitness Coaches
SLUG: best-course-platform-fitness-coaches
DESCRIPTION: A comparison focused on what fitness coaches actually need.
PERSONA: fitness-coaches
AFFILIATE_PROGRAM: TBD - pending application
BODY:
This is the article body.

It has multiple paragraphs.
"""


def test_parse_article_valid():
    article = bot._parse_article(VALID_ARTICLE_RAW)
    assert article["title"] == "Best Course Platform for Fitness Coaches"
    assert article["slug"] == "best-course-platform-fitness-coaches"
    assert article["persona"] == "fitness-coaches"
    assert article["affiliate_program"] == "TBD - pending application"
    assert "This is the article body." in article["body"]


def test_parse_article_missing_fields_returns_none():
    assert bot._parse_article("TITLE: only a title\nno other fields") is None


def test_parse_article_empty_body_returns_none():
    raw = VALID_ARTICLE_RAW.replace(
        "BODY:\nThis is the article body.\n\nIt has multiple paragraphs.\n", "BODY:\n"
    )
    assert bot._parse_article(raw) is None


def test_parse_article_sanitizes_slug():
    raw = VALID_ARTICLE_RAW.replace(
        "SLUG: best-course-platform-fitness-coaches",
        "SLUG: Best Course Platform! For Fitness Coaches?",
    )
    article = bot._parse_article(raw)
    assert article["slug"] == "best-course-platform-for-fitness-coaches"


def test_existing_slugs_excludes_underscore_files(tmp_path):
    bot.ARTICLES_DIR.mkdir(parents=True)
    (bot.ARTICLES_DIR / "real-article.md").write_text("x")
    (bot.ARTICLES_DIR / "_template.md").write_text("x")
    assert bot._existing_slugs() == ["real-article"]


def test_existing_slugs_empty_when_dir_missing():
    assert bot._existing_slugs() == []


VALID_PLAN_RAW = """TOPIC: Comparing community feature limits for cohort-based creators
PERSONA: cohort-creators
URLS:
https://www.kajabi.com/pricing
https://www.podia.com/pricing
"""


def test_parse_plan_valid():
    plan = bot._parse_plan(VALID_PLAN_RAW)
    assert plan["persona"] == "cohort-creators"
    assert plan["urls"] == ["https://www.kajabi.com/pricing", "https://www.podia.com/pricing"]


def test_parse_plan_missing_fields_returns_none():
    assert bot._parse_plan("TOPIC: only a topic\nno other fields") is None


def test_parse_plan_no_valid_urls_returns_none():
    raw = VALID_PLAN_RAW.replace(
        "https://www.kajabi.com/pricing\nhttps://www.podia.com/pricing",
        "not a url\nalso not a url",
    )
    assert bot._parse_plan(raw) is None


def test_parse_plan_caps_at_four_urls():
    raw = "TOPIC: x\nPERSONA: consultants\nURLS:\n" + "\n".join(
        f"https://example.com/{i}" for i in range(6)
    )
    plan = bot._parse_plan(raw)
    assert len(plan["urls"]) == 4


@pytest.mark.asyncio
async def test_run_pipeline_research_fetches_each_planned_url_then_writes():
    fetch_calls = []

    async def fake_fetch(url, max_chars=4000):
        fetch_calls.append(url)
        return True, f"content for {url}"

    llm_calls = []

    async def fake_llm(system_prompt, user_prompt):
        llm_calls.append((system_prompt, user_prompt))
        if system_prompt == bot._PIPELINE_PLANNER_PROMPT:
            return VALID_PLAN_RAW
        return "TITLE: x\nSLUG: x\nDESCRIPTION: x\nPERSONA: cohort-creators\n" \
               "AFFILIATE_PROGRAM: TBD\nBODY:\nbody text"

    with patch.object(bot, "_browser_fetch", side_effect=fake_fetch):
        with patch.object(bot, "_run_pipeline_llm", side_effect=fake_llm):
            result = await bot._run_pipeline_research()

    assert fetch_calls == ["https://www.kajabi.com/pricing", "https://www.podia.com/pricing"]
    assert len(llm_calls) == 2
    write_prompt = llm_calls[1][1]
    assert "content for https://www.kajabi.com/pricing" in write_prompt
    assert "content for https://www.podia.com/pricing" in write_prompt
    assert "body text" in result


@pytest.mark.asyncio
async def test_run_pipeline_research_reports_failed_fetches_to_writer():
    async def fake_fetch(url, max_chars=4000):
        return False, "404 not found"

    async def fake_llm(system_prompt, user_prompt):
        if system_prompt == bot._PIPELINE_PLANNER_PROMPT:
            return VALID_PLAN_RAW
        return user_prompt  # echo back so the test can inspect what the writer actually saw

    with patch.object(bot, "_browser_fetch", side_effect=fake_fetch):
        with patch.object(bot, "_run_pipeline_llm", side_effect=fake_llm):
            result = await bot._run_pipeline_research()

    assert "FETCH FAILED" in result
    assert "404 not found" in result


@pytest.mark.asyncio
async def test_run_pipeline_research_unparseable_plan_returns_raw():
    # fake_llm deliberately returns DIFFERENT text for the two phases, and the test asserts
    # the call count -- a version that fell through to the writer phase with an empty plan
    # (instead of actually stopping) would still produce a result, and if the writer's fake
    # output happened to match the planner's, a same-string mock wouldn't have caught that
    # (that's exactly the shape of mutation this test failed to catch on its first version).
    async def fake_llm(system_prompt, user_prompt):
        if system_prompt == bot._PIPELINE_PLANNER_PROMPT:
            return "not a valid plan format"
        return "SHOULD NOT BE CALLED -- writer phase ran despite an unparseable plan"

    with patch.object(bot, "_run_pipeline_llm", side_effect=fake_llm) as mock_llm:
        with patch.object(bot, "_browser_fetch") as mock_fetch:
            result = await bot._run_pipeline_research()

    mock_fetch.assert_not_called()
    assert mock_llm.call_count == 1
    assert result == "not a valid plan format"


def test_write_article_file_defaults_to_draft_when_caller_omits_status():
    # Fail-closed default: a caller that forgets to set status (a bug) must not accidentally
    # publish. _run_content_cycle is the only caller that sets "published", explicitly, after
    # _self_correct_style has run -- this is the safety net for every other/future caller.
    article = bot._parse_article(VALID_ARTICLE_RAW)
    article["date"] = "2026-08-31"
    path = bot._write_article_file(article)
    content = path.read_text()
    assert 'status: "draft"' in content
    assert 'status: "' in content and content.count('status: "') == 1  # not silently duplicated


def test_write_article_file_honors_caller_supplied_status():
    # Reversed from the pre-2026-09-05 invariant on purpose (Sean: "I should not have to
    # approve drafts... it should be a self correcting loop") -- status now genuinely comes
    # from the caller, not hardcoded. See PLAN.md "Progress log" 2026-09-05.
    article = bot._parse_article(VALID_ARTICLE_RAW)
    article["date"] = "2026-08-31"
    article["status"] = "published"
    path = bot._write_article_file(article)
    content = path.read_text()
    assert 'status: "published"' in content
    assert 'status: "draft"' not in content


def test_write_article_file_includes_all_frontmatter_fields():
    article = bot._parse_article(VALID_ARTICLE_RAW)
    article["date"] = "2026-08-31"
    path = bot._write_article_file(article)
    content = path.read_text()
    assert 'title: "Best Course Platform for Fitness Coaches"' in content
    assert 'persona: "fitness-coaches"' in content
    assert "This is the article body." in content


# ---------------------------------------------------------------- style check

def test_style_violations_clean_text_has_none():
    assert bot._style_violations("This is a plain sentence about pricing tiers.") == []


def test_style_violations_detects_em_dash():
    assert "em dash" in bot._style_violations("A tool for creators — built for scale.")


def test_style_violations_detects_banned_word():
    violations = bot._style_violations("This feature will truly elevate your workflow.")
    assert any("elevate" in v for v in violations)


def test_style_violations_detects_negate_pivot_two_sentence():
    text = ('The question isn\'t "which platform is cheaper." It\'s whether instructors '
            "get their own login.")
    assert any("negate-then-pivot" in v for v in bot._style_violations(text))


def test_style_violations_detects_negate_pivot_comma_form():
    text = "That's not a pricing problem, it's a feature-gap problem."
    assert any("negate-then-pivot" in v for v in bot._style_violations(text))


def test_style_violations_detects_closer_phrase():
    text = "At the end of the day, both platforms cost about the same."
    assert any("closer" in v for v in bot._style_violations(text))


def test_style_violations_does_not_flag_unlock_as_domain_term():
    # This niche's own vendor copy legitimately says things like this -- banning "unlock"
    # would flag quoted domain language, not hype. See the comment above _STYLE_BANNED_PHRASES.
    text = "Thinkific's Start plan lets you grade student work and unlock lessons as they progress."
    assert bot._style_violations(text) == []


def test_style_violations_does_not_flag_dive_into_as_dive_in():
    assert bot._style_violations("We dive into the pricing page to check the claim.") == []


def test_write_article_file_records_no_style_flags_when_clean():
    article = bot._parse_article(VALID_ARTICLE_RAW)
    article["date"] = "2026-08-31"
    path = bot._write_article_file(article)
    assert 'styleFlags: ""' in path.read_text()


def test_write_article_file_records_style_flags_when_present():
    raw = VALID_ARTICLE_RAW.replace(
        "This is the article body.",
        "This is the article body. It isn't the cheap option. It's the reliable one.",
    )
    article = bot._parse_article(raw)
    article["date"] = "2026-08-31"
    path = bot._write_article_file(article)
    content = path.read_text()
    assert 'styleFlags: "negate-then-pivot sentence"' in content


# ---------------------------------------------------------------- self-correction loop

@pytest.mark.asyncio
async def test_self_correct_style_skips_llm_call_when_already_clean():
    with patch.object(bot, "_run_pipeline_llm") as mock_llm:
        body, violations = await bot._self_correct_style("A plain sentence about pricing.")
    mock_llm.assert_not_called()
    assert violations == []
    assert body == "A plain sentence about pricing."


@pytest.mark.asyncio
async def test_self_correct_style_converges_on_first_fix():
    flagged = "It isn't the cheap option. It's the reliable one."
    with patch.object(bot, "_run_pipeline_llm", return_value="The reliable option costs more.") as mock_llm:
        body, violations = await bot._self_correct_style(flagged)
    mock_llm.assert_called_once()
    assert violations == []
    assert body == "The reliable option costs more."


@pytest.mark.asyncio
async def test_self_correct_style_stops_at_max_attempts_when_never_clean():
    flagged = "It isn't the cheap option. It's the reliable one."
    with patch.object(bot, "_run_pipeline_llm", return_value=flagged) as mock_llm:
        body, violations = await bot._self_correct_style(flagged)
    assert mock_llm.call_count == bot.STYLE_FIX_MAX_ATTEMPTS
    assert violations == ["negate-then-pivot sentence"]
    assert body == flagged


@pytest.mark.asyncio
async def test_self_correct_style_passes_violations_to_the_fix_prompt():
    with patch.object(bot, "_run_pipeline_llm", return_value="Fixed.") as mock_llm:
        await bot._self_correct_style("It isn't cheap. It's fine.")
    user_prompt = mock_llm.call_args[0][1]
    assert "negate-then-pivot sentence" in user_prompt
    assert "It isn't cheap. It's fine." in user_prompt


def test_git_commit_and_push_no_token_still_commits_locally():
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return _fake_run(returncode=0)

    with patch.object(subprocess, "run", side_effect=fake_run):
        ok, message = bot._git_commit_and_push(["site/content/articles/x.md"], "test commit")
    assert ok is False
    assert "GITHUB_TOKEN" in message
    assert any("commit" in c for c in calls)
    assert not any("push" in c for c in calls)  # never attempts push without a token


def test_git_commit_and_push_add_failure_stops_before_commit():
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "add" in args:
            return _fake_run(returncode=1, stderr="add failed")
        return _fake_run(returncode=0)

    with patch.object(subprocess, "run", side_effect=fake_run):
        ok, message = bot._git_commit_and_push(["x.md"], "test")
    assert ok is False
    assert "add failed" in message
    assert not any("commit" in c for c in calls)


def test_git_commit_and_push_success_with_token(monkeypatch):
    monkeypatch.setattr(bot, "GITHUB_TOKEN", "fake-token-value")
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return _fake_run(returncode=0)

    with patch.object(subprocess, "run", side_effect=fake_run):
        ok, message = bot._git_commit_and_push(["x.md"], "test")
    assert ok is True
    assert message == "pushed"
    assert any("push" in c for c in calls)


def test_git_commit_and_push_failure_redacts_token(monkeypatch):
    monkeypatch.setattr(bot, "GITHUB_TOKEN", "super-secret-token")

    def fake_run(args, **kwargs):
        if "push" in args:
            return _fake_run(returncode=1, stderr="fatal: auth failed for super-secret-token")
        return _fake_run(returncode=0)

    with patch.object(subprocess, "run", side_effect=fake_run):
        ok, message = bot._git_commit_and_push(["x.md"], "test")
    assert ok is False
    assert "super-secret-token" not in message
    assert "***" in message


def test_git_commit_and_push_syncs_with_origin_before_add():
    # The mini app's approve/edit/reject actions commit to origin/main via GitHub's API,
    # bypassing this local clone -- without this sync, a later push here would be rejected as
    # non-fast-forward. Asserts the real invariant: fetch/merge happen, and strictly before add.
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return _fake_run(returncode=0)

    with patch.object(subprocess, "run", side_effect=fake_run):
        ok, _ = bot._git_commit_and_push(["x.md"], "test")
    assert ok is False  # no GITHUB_TOKEN in the clean fixture, expected to stop before push
    verbs = [c[3] for c in calls]  # ["git", "-C", repo_root, <verb>, ...]
    assert verbs[0] == "fetch"
    assert verbs[1] == "merge"
    assert verbs.index("add") > verbs.index("merge")


def test_git_commit_and_push_diverged_from_origin_blocks_before_any_write():
    # A real divergence (not just "no network") must stop the whole sequence cold -- writing
    # on top of history this clone doesn't have is exactly the bug this sync exists to prevent.
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "merge" in args:
            return _fake_run(returncode=1, stderr="fatal: Not possible to fast-forward")
        return _fake_run(returncode=0)

    with patch.object(subprocess, "run", side_effect=fake_run):
        ok, message = bot._git_commit_and_push(["x.md"], "test")
    assert ok is False
    assert "diverged" in message
    assert not any("add" in c for c in calls)
    assert not any("commit" in c for c in calls)


def test_git_commit_and_push_fetch_failure_is_not_fatal():
    # No network reaching origin must behave exactly as it did before this sync existed --
    # fall through and let add/commit/push run (and report their own failure if any), not
    # block a local-only commit over a transient fetch failure.
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if "fetch" in args:
            return _fake_run(returncode=1, stderr="could not resolve host")
        return _fake_run(returncode=0)

    with patch.object(subprocess, "run", side_effect=fake_run):
        ok, message = bot._git_commit_and_push(["x.md"], "test")
    assert any("add" in c for c in calls)
    assert any("commit" in c for c in calls)
    assert not any("merge" in c for c in calls)  # never merges when fetch itself failed
    assert ok is False  # still blocked on the pre-existing "no GITHUB_TOKEN" path
    assert "GITHUB_TOKEN" in message


# ---------------------------------------------------------------- mini-app status (Upstash)

def test_list_draft_articles_excludes_non_draft_and_underscore_files():
    bot.ARTICLES_DIR.mkdir(parents=True)
    (bot.ARTICLES_DIR / "a-draft.md").write_text(
        '---\ntitle: "A Draft"\ndescription: "d1"\nstatus: "draft"\n---\n\nbody'
    )
    (bot.ARTICLES_DIR / "b-published.md").write_text(
        '---\ntitle: "B Published"\ndescription: "d2"\nstatus: "published"\n---\n\nbody'
    )
    (bot.ARTICLES_DIR / "_template.md").write_text(
        '---\ntitle: "Template"\nstatus: "draft"\n---\n\nbody'
    )
    drafts = bot._list_draft_articles()
    assert [d["slug"] for d in drafts] == ["a-draft"]
    assert drafts[0]["title"] == "A Draft"
    assert drafts[0]["description"] == "d1"
    assert drafts[0]["styleFlags"] == ""


def test_list_draft_articles_surfaces_style_flags():
    bot.ARTICLES_DIR.mkdir(parents=True)
    (bot.ARTICLES_DIR / "flagged.md").write_text(
        '---\ntitle: "Flagged"\ndescription: "d"\nstatus: "draft"\n'
        'styleFlags: "em dash; banned word(s): leverage"\n---\n\nbody'
    )
    drafts = bot._list_draft_articles()
    assert drafts[0]["styleFlags"] == "em dash; banned word(s): leverage"


def test_list_draft_articles_empty_when_dir_missing():
    assert bot._list_draft_articles() == []


def test_gather_status_snapshot_shape(monkeypatch):
    monkeypatch.setattr(bot, "_state", {"paused": True, "last_pipeline_run": "2026-08-31T00:00:00+00:00"})
    with patch.object(subprocess, "run", return_value=_fake_run(stdout="abc1234")):
        snapshot = bot._gather_status_snapshot()
    assert snapshot["paused"] is True
    assert snapshot["repo_rev"] == "abc1234"
    assert snapshot["last_pipeline_run"] == "2026-08-31T00:00:00+00:00"
    assert isinstance(snapshot["drafts"], list)
    assert "pushed_at" in snapshot


@pytest.mark.asyncio
async def test_push_status_snapshot_noop_without_config():
    # Best-effort by design -- unconfigured must not raise or attempt a request at all.
    with patch.object(httpx, "AsyncClient") as mock_client:
        await bot._push_status_snapshot()
    mock_client.assert_not_called()


@pytest.mark.asyncio
async def test_push_status_snapshot_sends_authenticated_put(monkeypatch):
    monkeypatch.setattr(bot, "KV_REST_API_URL", "https://fake-upstash.example")
    monkeypatch.setattr(bot, "KV_REST_API_TOKEN", "fake-upstash-token")

    class FakeUpstashResponse:
        status_code = 200
        text = "OK"

    captured = {}

    class FakeAsyncClient:
        def __init__(self, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, content=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["content"] = content
            return FakeUpstashResponse()

    with patch.object(httpx, "AsyncClient", FakeAsyncClient):
        await bot._push_status_snapshot()

    assert captured["url"] == "https://fake-upstash.example/set/creatorstacked:status"
    assert captured["headers"]["Authorization"] == "Bearer fake-upstash-token"
    payload = json.loads(captured["content"])
    assert "paused" in payload


@pytest.mark.asyncio
async def test_push_status_snapshot_swallows_connection_errors():
    with patch.object(bot, "KV_REST_API_URL", "https://fake-upstash.example"), \
         patch.object(bot, "KV_REST_API_TOKEN", "fake-token"), \
         patch.object(httpx, "AsyncClient", side_effect=httpx.ConnectError("no route to host")):
        await bot._push_status_snapshot()  # must not raise


@pytest.mark.asyncio
async def test_run_content_cycle_unparseable_output_writes_nothing():
    with patch.object(bot, "_run_pipeline_research", return_value="garbage output"):
        with patch.object(bot, "_write_article_file") as mock_write:
            result = await bot._run_content_cycle()
    mock_write.assert_not_called()
    assert "didn't parse" in result


@pytest.mark.asyncio
async def test_run_content_cycle_writes_and_commits():
    with patch.object(bot, "_run_pipeline_research", return_value=VALID_ARTICLE_RAW):
        with patch.object(bot, "_run_pipeline_llm") as mock_fix_llm:
            with patch.object(bot, "_git_commit_and_push", return_value=(True, "pushed")) as mock_push:
                result = await bot._run_content_cycle()
    mock_fix_llm.assert_not_called()  # VALID_ARTICLE_RAW's body is clean, no fix pass needed
    mock_push.assert_called_once()
    pushed_paths = mock_push.call_args[0][0]
    assert any(p.endswith("best-course-platform-fitness-coaches.md") for p in pushed_paths)
    assert "pushed to GitHub" in result
    assert "published" in result.lower()
    written = (bot.ARTICLES_DIR / "best-course-platform-fitness-coaches.md").read_text()
    assert 'status: "published"' in written
    assert "Style check" not in result


@pytest.mark.asyncio
async def test_run_content_cycle_self_corrects_then_publishes_clean():
    flagged_raw = VALID_ARTICLE_RAW.replace(
        "This is the article body.",
        "This is the article body. It isn't the cheap option. It's the reliable one.",
    )
    with patch.object(bot, "_run_pipeline_research", return_value=flagged_raw):
        with patch.object(bot, "_run_pipeline_llm",
                           return_value="This is the fixed article body.") as mock_fix_llm:
            with patch.object(bot, "_git_commit_and_push", return_value=(True, "pushed")):
                result = await bot._run_content_cycle()
    mock_fix_llm.assert_called_once()
    assert "Style check" not in result  # converged, nothing left to warn about
    written = (bot.ARTICLES_DIR / "best-course-platform-fitness-coaches.md").read_text()
    assert 'status: "published"' in written
    assert 'styleFlags: ""' in written


@pytest.mark.asyncio
async def test_run_content_cycle_publishes_anyway_when_self_correction_cant_converge():
    flagged_raw = VALID_ARTICLE_RAW.replace(
        "This is the article body.",
        "This is the article body. It isn't the cheap option. It's the reliable one.",
    )
    # Fix pass returns the exact same still-flagged text every time -- never converges.
    with patch.object(bot, "_run_pipeline_research", return_value=flagged_raw):
        with patch.object(bot, "_run_pipeline_llm",
                           return_value=flagged_raw.split("BODY:\n")[1]) as mock_fix_llm:
            with patch.object(bot, "_git_commit_and_push", return_value=(True, "pushed")):
                result = await bot._run_content_cycle()
    assert mock_fix_llm.call_count == bot.STYLE_FIX_MAX_ATTEMPTS
    assert "Style check still flagged after 3 self-correction attempt(s)" in result
    assert "negate-then-pivot sentence" in result
    written = (bot.ARTICLES_DIR / "best-course-platform-fitness-coaches.md").read_text()
    # Publishes anyway -- this is visibility, not a gate.
    assert 'status: "published"' in written
    assert 'styleFlags: "negate-then-pivot sentence"' in written


def test_pipeline_due_when_never_run():
    assert bot._pipeline_due() is True


def test_pipeline_due_false_within_interval():
    bot._state["last_pipeline_run"] = datetime.now(timezone.utc).isoformat()
    assert bot._pipeline_due() is False


def test_pipeline_due_true_after_interval(monkeypatch):
    monkeypatch.setattr(bot, "CONTENT_PIPELINE_INTERVAL_HOURS", 1.0)
    bot._state["last_pipeline_run"] = (
        datetime.now(timezone.utc) - timedelta(hours=2)
    ).isoformat()
    assert bot._pipeline_due() is True


def test_pipeline_due_true_on_corrupt_timestamp():
    bot._state["last_pipeline_run"] = "not a real timestamp"
    assert bot._pipeline_due() is True


@pytest.mark.asyncio
async def test_route_pipeline_blocked_while_paused():
    bot._state["paused"] = True
    with patch.object(bot, "_handle_pipeline") as mock_pipeline:
        result = await bot._route("pipeline")
    mock_pipeline.assert_not_called()
    assert "Paused" in result


@pytest.mark.asyncio
async def test_route_pipeline_allowed_when_not_paused():
    with patch.object(bot, "_handle_pipeline", return_value="ran") as mock_pipeline:
        result = await bot._route("pipeline")
    mock_pipeline.assert_called_once()
    assert result == "ran"


@pytest.mark.asyncio
async def test_handle_pipeline_stamps_last_run():
    assert "last_pipeline_run" not in bot._state
    with patch.object(bot, "_run_content_cycle", return_value="done"):
        await bot._handle_pipeline()
    assert "last_pipeline_run" in bot._state
