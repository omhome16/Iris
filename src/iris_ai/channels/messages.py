"""What a channel hands the kernel, and what the kernel hands back."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Sender:
    id: str
    display: str = ""


@dataclass(frozen=True, slots=True)
class InboundMessage:
    channel: str
    conversation: str
    sender: Sender
    text: str
    attachments: tuple = ()
    message_id: str = ""


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    channel: str
    conversation: str
    text: str
    reply_to: str = ""
