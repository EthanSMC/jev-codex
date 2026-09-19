"""The Codex transport pauses before input; no browser or paid APIs are used."""

import io
import json
import os
import selectors
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from test_agent import decision, page, runner  # noqa: F401

from jev_ultrafast import agent as loop
from jev_ultrafast import codex, model
from jev_ultrafast.browser import StalePage


def provider_for(reply):
    output = io.StringIO()

    class Reader:
        def readline(self, limit):
            request = json.loads(output.getvalue().splitlines()[-1])
            result = reply(request)
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False) + "\n"

    return codex.CodexTextProvider(Reader(), output), output


def test_codex_text_needs_no_text_api_and_resumes_once(runner, monkeypatch):  # noqa: F811
    monkeypatch.delenv("TEXT_MODEL_API_KEY", raising=False)
    api = Mock(side_effect=AssertionError("Must not call a text API"))
    monkeypatch.setattr(loop, "field_text", api)
    provider, output = provider_for(lambda r: {"request_id": r["request_id"], "text": "苏黎世"})
    runner.text_provider = provider
    runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert json.loads(output.getvalue())["context"]["page"]["url"] == page()["url"]
    runner.state["browser"].act.assert_called_once()
    assert runner.state["browser"].act.call_args.kwargs["text"] == "苏黎世"
    assert runner.state["history"][0]["text_helper"] == "codex-handoff"
    api.assert_not_called()
    with pytest.raises(ValueError, match="Observe and choose"):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    assert runner.state["browser"].act.call_count == 1


@pytest.mark.parametrize("bad_reply", [
    lambda r: "",
    lambda r: "not JSON\n",
    lambda r: "x" * (codex.MAX_REPLY + 1),
    lambda r: '{"text":"truncated"}',
    lambda r: [],
    lambda r: {"request_id": "old-request", "text": "wrong"},
    lambda r: {"request_id": r["request_id"], "cancel": True},
    lambda r: {"request_id": r["request_id"], "text": None},
    lambda r: {"request_id": r["request_id"], "text": " "},
    lambda r: {"request_id": r["request_id"], "text": 123},
    lambda r: {"request_id": r["request_id"], "text": "x" * 2001},
    lambda r: {"request_id": r["request_id"], "text": "book", "action": "click"},
])
def test_bad_or_cancelled_codex_reply_never_types(runner, bad_reply):  # noqa: F811
    runner.text_provider, _ = provider_for(bad_reply)
    with pytest.raises(ValueError, match="nothing typed"):
        runner.command("act", {"fingerprint": runner.state["page"]["fingerprint"]})
    runner.state["browser"].act.assert_not_called()
    assert runner.state["decision"] is None


@pytest.mark.parametrize("changed", [False, True])
def test_page_change_during_handoff_rechecks_before_input(runner, monkeypatch, changed):  # noqa: F811
    provider, output = provider_for(lambda r: {"request_id": r["request_id"], "text": "book"})
    runner.text_provider = provider
    browser = runner.state["browser"]
    browser.act.side_effect = [StalePage("Changed while Codex was responding"), None]
    monkeypatch.setattr(loop, "choose", Mock(return_value=decision()))
    runner.command("tick")
    assert runner.state["history"] == []
    assert runner.state["status"] == "ready"
    if changed:
        runner.state["page"]["url"] = "https://different.test/"
    runner.command("tick")
    assert len(output.getvalue().splitlines()) == (2 if changed else 1)
    assert len(runner.state["history"]) == 1


def test_cli_round_trip_over_pipes_without_any_model_api():
    # Use the real CLI and agent loop, mocking only the browser and Jev choices.
    script = '''
from unittest.mock import Mock
from test_agent import page, decision
from jev_ultrafast import agent as loop
from jev_ultrafast.codex import main
b = Mock(fresh=Mock(return_value=True), observe=Mock(return_value=page()))
loop.Browser = Mock(return_value=b)
loop.choose = Mock(side_effect=[decision(), decision("DONE")])
loop.field_text = Mock(side_effect=AssertionError("No text API"))
code = main(["--url", "https://example.test", "--goal", "Find a book"])
assert b.act.call_count == 1
assert b.act.call_args.kwargs["text"] == "一本书"
assert b.close.call_count == 1
raise SystemExit(code)
'''
    env = {**os.environ, "TYPESAFE_API_KEY": "offline-test"}
    env.pop("TEXT_MODEL_API_KEY", None)
    env["PYTHONPATH"] = os.pathsep.join([str(Path(__file__).parent), str(Path(__file__).parent.parent)])
    process = subprocess.Popen(
        [sys.executable, "-u", "-c", script], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, env=env,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=10), "No text request received"
        request = json.loads(process.stdout.readline())
        assert request["event"] == "text_request"
        assert process.poll() is None  # Waiting for this conversation's reply.
        reply = json.dumps({"request_id": request["request_id"], "text": "一本书"}, ensure_ascii=False)
        stdout, stderr = process.communicate(reply + "\n", timeout=10)
        assert process.returncode == 0, stderr
        result = json.loads(stdout.splitlines()[-1])
        assert result["event"] == "result" and result["status"] == "done"
        assert result["history"][0]["text"] == "一本书"
        assert result["independently_verified"] is False
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


def test_missing_typesafe_key_fails_before_opening_browser(monkeypatch, capsys):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    browser = Mock(side_effect=AssertionError("Must not open browser"))
    monkeypatch.setattr(codex, "Agent", browser)
    assert codex.main(["--url", "https://example.test", "--goal", "Find a book"]) == 1
    assert json.loads(capsys.readouterr().out)["event"] == "error"
    browser.assert_not_called()


def test_api_mode_remains_available(monkeypatch, capsys):
    monkeypatch.setenv("TYPESAFE_API_KEY", "offline-test")
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "offline-test")
    state = {"status": "done", "page": page(), "history": [], "elements": []}
    instance = Mock(run=Mock(return_value=iter([])), snapshot=Mock(return_value=state))
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=instance)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(codex, "Agent", factory)
    assert codex.main(["--url", "https://example.test", "--goal", "Find a book", "--text-provider", "api"]) == 0
    assert factory.call_args.kwargs["text_provider"] is None
    assert json.loads(capsys.readouterr().out)["event"] == "result"


@pytest.mark.parametrize("output", [[], None, {"text": ""}, {"text": "ok", "extra": 1}])
def test_shared_value_validation_rejects_non_field_output(output):
    with pytest.raises(ValueError, match="nothing typed"):
        model.validate_field_value(output)
