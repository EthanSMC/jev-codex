"""A JSON-lines handoff to the current Codex conversation, without a text API."""

import argparse
import json
import os
import sys
import time
import uuid
from contextlib import contextmanager

from .agent import Agent
from .model import validate_field_value
from .questions import TEXT_VALUE

MAX_REPLY = 16384


def emit(stream, event, **data):
    print(json.dumps({"event": event, **data}, ensure_ascii=False), file=stream, flush=True)


class CodexTextProvider:
    """Pause before input; accept only a value for this specific request."""

    def __init__(self, reader=None, writer=None):
        self.reader = reader if reader is not None else sys.stdin
        self.writer = writer if writer is not None else sys.stdout

    def __call__(self, context):
        request_id = uuid.uuid4().hex
        started = time.perf_counter()
        emit(
            self.writer,
            "text_request",
            request_id=request_id,
            instructions=TEXT_VALUE + "\nFor this transport, also include request_id as shown in reply_format. "
            "If the value is unavailable, reply with request_id and cancel: true.",
            context=context,
            reply_format={"request_id": request_id, "text": "the field value"},
        )
        line = self.reader.readline(MAX_REPLY + 1)
        if not line:
            raise ValueError("Codex input closed; nothing typed.")
        if len(line) > MAX_REPLY or not line.endswith("\n"):
            raise ValueError("Codex reply must be one bounded JSON line; nothing typed.")
        try:
            reply = json.loads(line)
        except ValueError:
            raise ValueError("Codex reply is not valid JSON; nothing typed.") from None
        if not isinstance(reply, dict) or reply.get("request_id") != request_id:
            raise ValueError("Codex reply does not match the pending request; nothing typed.")
        if set(reply) == {"request_id", "cancel"} and reply["cancel"] is True:
            raise ValueError("Codex cancelled text input; nothing typed.")
        value = validate_field_value({k: v for k, v in reply.items() if k != "request_id"})
        return value, {
            "model": "codex-handoff",
            "latency_ms": round((time.perf_counter() - started) * 1000),
            "usage": {},
        }


@contextmanager
def protocol_terminal():
    """Keep PTY replies private and support long Unicode lines; restore on exit."""
    if os.name != "posix" or not sys.stdin.isatty():
        yield
        return
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--text-provider", choices=("codex", "api"), default="codex")
    args = parser.parse_args(argv)
    if not os.environ.get("TYPESAFE_API_KEY"):
        emit(sys.stdout, "error", message="Set TYPESAFE_API_KEY before starting Jev.")
        return 1
    if args.text_provider == "api" and not os.environ.get("TEXT_MODEL_API_KEY"):
        emit(sys.stdout, "error", message="API text mode requires TEXT_MODEL_API_KEY.")
        return 1
    provider = CodexTextProvider() if args.text_provider == "codex" else None
    try:
        with protocol_terminal(), Agent(args.url, args.goal, text_provider=provider) as agent:
            for state in agent.run():
                emit(
                    sys.stdout,
                    "progress",
                    status=state["status"],
                    actions=len(state["history"]),
                    elapsed_ms=state["elapsed_ms"],
                    url=state["page"]["url"],
                )
            state = agent.snapshot()
            emit(
                sys.stdout,
                "result",
                status=state["status"],
                page={k: state["page"][k] for k in ("url", "title", "text")},
                elements=state["elements"],
                history=state["history"],
                independently_verified=False,
            )
            return 0 if state["status"] == "done" else 2
    except KeyboardInterrupt:
        emit(sys.stdout, "error", message="Run interrupted; inspect the outcome before restarting.")
        return 130
    except (ValueError, RuntimeError, TimeoutError) as error:
        emit(sys.stdout, "error", message=str(error))
        return 1


if __name__ == "__main__":
    sys.exit(main())
