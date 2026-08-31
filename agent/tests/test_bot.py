import json
import subprocess
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
    monkeypatch.setattr(bot, "_state", {"paused": False})
    monkeypatch.setattr(bot, "OPERATOR_TELEGRAM_ID", OPERATOR_ID)
    monkeypatch.setattr(bot, "AGENTMAIL_API_KEY", "")
    monkeypatch.setattr(bot, "AGENTMAIL_INBOX_ID", "")


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

    async def send_message(self, chat_id, text):
        self.sent.append((chat_id, text))


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
    with patch.object(bot, "_run_draft", return_value="Answers here"):
        result = await bot._handle_draft_application("Kajabi|https://kajabi.com/affiliates")
    assert "no `send`" in result
    drafts = bot._list_open_drafts()
    assert drafts[0]["kind"] == "application"


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
