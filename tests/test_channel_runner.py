"""The runner maps a sender to an origin and drops duplicates."""

from iris_ai.channels.messages import InboundMessage, Sender
from iris_ai.channels.runner import ChannelRunner


def _message(sender: str, text: str = "hi", message_id: str = "1") -> InboundMessage:
    return InboundMessage(
        channel="discord",
        conversation="dm-1",
        sender=Sender(id=sender, display=sender),
        text=text,
        message_id=message_id,
    )


def test_an_owner_is_owner_and_a_stranger_is_untrusted():
    runner = ChannelRunner(channel="discord", owners=("ada",), others="untrusted")
    assert runner.accept(_message("ada")) == "owner"
    assert runner.accept(_message("grace", message_id="2")) == "untrusted"
    assert runner.session_for(_message("ada")) == "discord:dm-1"


def test_a_duplicate_and_an_ignored_stranger_are_dropped():
    runner = ChannelRunner(channel="discord", owners=("ada",), others="ignore")
    assert runner.accept(_message("ada", message_id="9")) == "owner"
    assert runner.accept(_message("ada", message_id="9")) is None
    assert runner.accept(_message("grace", message_id="10")) is None


def test_the_rate_limit_trips(monkeypatch):
    runner = ChannelRunner(channel="discord", owners=("ada",), rate_per_minute=1)
    assert runner.accept(_message("ada", message_id="1"), now=0) == "owner"
    assert runner.accept(_message("ada", message_id="2"), now=1) is None
    assert runner.accept(_message("ada", message_id="3"), now=61) == "owner"
