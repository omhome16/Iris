"""Length-prefixed JSON-RPC 2.0 frames on a byte stream.

One frame is a 4-byte big-endian length and then a JSON object. Messages
larger than 1 MiB are refused. The host and the child both speak this.
"""

from __future__ import annotations

import json
import struct
from typing import Any, BinaryIO

MAX_MESSAGE = 1024 * 1024
_HEADER = struct.Struct(">I")


def request(msg_id: int | str, method: str, params: dict | None = None) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}}


def result(msg_id: int | str, value: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": value}


def error(msg_id: int | str, message: str, *, code: int = -32000) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def write_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(body) > MAX_MESSAGE:
        raise ValueError(f"message is {len(body)} bytes; the limit is {MAX_MESSAGE}")
    stream.write(_HEADER.pack(len(body)))
    stream.write(body)
    stream.flush()


def read_message(stream: BinaryIO) -> dict[str, Any]:
    header = _read_exact(stream, _HEADER.size)
    (size,) = _HEADER.unpack(header)
    if size > MAX_MESSAGE:
        raise ValueError(f"message is {size} bytes; the limit is {MAX_MESSAGE}")
    payload = json.loads(_read_exact(stream, size))
    if not isinstance(payload, dict):
        raise ValueError("host message must be a JSON object")
    return payload


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    buf = bytearray()
    while len(buf) < size:
        chunk = stream.read(size - len(buf))
        if not chunk:
            raise EOFError("component host closed the pipe")
        buf.extend(chunk)
    return bytes(buf)
