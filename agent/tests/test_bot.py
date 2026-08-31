import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import bot

OPERATOR_ID = 12345
OTHER_ID = 99999


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(bot, "DATA_DIR", tmp_path)
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(bot, "_state", {"paused": False})
    monkeypatch.setattr(bot, "OPERATOR_TELEGRAM_ID", OPERATOR_ID)


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
