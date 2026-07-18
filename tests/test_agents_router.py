"""groundly/agents/router.py: one cheap call classifies factoid/multi-hop/global.
Unconfigured [providers.router] -> skip the call entirely (cost gate); unparseable
reply -> factoid (safe default, degrades to vector at P3 anyway)."""

import pytest

from groundly.agents.router import classify
from groundly.llm.chat import ChatUnreachableError


@pytest.fixture(autouse=True)
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("GROUNDLY_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    return tmp_path / "home"


def test_classify_returns_none_and_skips_call_when_router_unconfigured(home, stub_chat):
    chat = stub_chat("factoid")
    assert classify("what is a deadlock?", chat) is None
    assert chat.calls == []


def _configure_router(home):
    (home / "config.toml").write_text('[providers.router]\nbase_url = "http://x"\nmodel = "m"\n')


@pytest.mark.parametrize("label", ["factoid", "multi-hop", "global"])
def test_classify_returns_parsed_label(home, stub_chat, label):
    _configure_router(home)
    chat = stub_chat(label)
    assert classify("q", chat) == label
    assert chat.calls[0][0] == "router"


def test_classify_unparseable_reply_defaults_to_factoid(home, stub_chat):
    _configure_router(home)
    chat = stub_chat("uh, not sure!")
    assert classify("q", chat) == "factoid"


def test_classify_unreachable_degrades_to_none(home):
    _configure_router(home)

    def unreachable_chat(call_class, messages, *, transport=None):
        raise ChatUnreachableError("router unreachable")

    assert classify("q", unreachable_chat) is None
