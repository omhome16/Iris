"""Telegram MCP bridge — wraps the Telegram Bot API as an MCP server.

Two roles in one process:
1. MCP server (streamable HTTP on :8100/mcp) exposing tools Iris-core can
   call: `send_message`, `get_chat_history`, `broadcast`. This is the
   outbound channel — Iris reaches the owner through it.
2. Long-polling bridge: receives Telegram updates, dispatches slash
   commands to iris-core HTTP endpoints, forwards plain messages into
   the /chat graph, and sends replies back.

The bridge learns the owner's chat id from the first /start and persists
it to data/owner.json so Iris can message the owner proactively later.

Requires env: TELEGRAM_BOT_TOKEN, IRIS_CORE_URL (default http://127.0.0.1:8000)
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from mcp.server.mcpserver.server import MCPServer

log = logging.getLogger("telegram-mcp")

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
IRIS_CORE_URL = os.environ.get("IRIS_CORE_URL", "http://127.0.0.1:8000").rstrip("/")
IRIS_API_TOKEN = os.environ.get("IRIS_API_TOKEN", "")
OWNER_FILE = Path(os.environ.get("OWNER_FILE", "data/owner.json"))
BOT_API = f"https://api.telegram.org/bot{BOT_TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{BOT_TOKEN}"  # downloads only


def _core_headers() -> dict[str, str]:
    if IRIS_API_TOKEN:
        return {"Authorization": f"Bearer {IRIS_API_TOKEN}"}
    return {}

server = MCPServer(
    name="telegram",
    description="Telegram channel of Iris — send messages, read chat history, broadcast to the owner.",
    version="0.1.0",
)

chat_log: dict[int, list[dict]] = {}


def _owner_chat_id() -> int | None:
    if OWNER_FILE.exists():
        try:
            return int(json.loads(OWNER_FILE.read_text(encoding="utf-8")).get("chat_id"))
        except Exception:  # noqa: BLE001
            return None
    return None


def _save_owner(chat_id: int) -> None:
    OWNER_FILE.parent.mkdir(parents=True, exist_ok=True)
    OWNER_FILE.write_text(json.dumps({"chat_id": chat_id}), encoding="utf-8")


async def _tg(method: str, **params) -> dict | None:
    """Thin Telegram Bot API call; None on transport errors."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{BOT_API}/{method}", json=params)
        if r.status_code != 200:
            log.warning("telegram %s failed: %s %s", method, r.status_code, r.text[:200])
            return None
        data = r.json()
        if not data.get("ok"):
            log.warning("telegram %s refused: %s", method, data.get("description"))
            return None
        return data.get("result")


async def send_to_chat(chat_id: int, text: str) -> bool:
    result = await _tg("sendMessage", chat_id=chat_id, text=text, disable_web_page_preview=True)
    return result is not None


@server.tool(name="send_message", description="Send a message to a Telegram chat.")
async def send_message(chat_id: int, text: str) -> str:
    ok = await send_to_chat(chat_id, text)
    return json.dumps({"ok": ok, "chat_id": chat_id})


@server.tool(name="send_photo", description="Send a photo (by URL) to a Telegram chat.")
async def send_photo(chat_id: int, photo_url: str, caption: str = "") -> str:
    try:
        if not _url_is_public(photo_url):
            return json.dumps(
                {"ok": False, "error": "refusing non-public URL (SSRF guard)"}
            )
        async with httpx.AsyncClient(timeout=60) as client:
            dl = await client.get(photo_url)
            dl.raise_for_status()
        params: dict = {"chat_id": chat_id, "photo": dl.content}
        if caption:
            params["caption"] = caption
        result = await _tg("sendPhoto", **params)
        return json.dumps({"ok": result is not None, "chat_id": chat_id})
    except Exception as exc:  # noqa: BLE001 - a failed send must not break the turn
        return json.dumps({"ok": False, "error": str(exc)})


def _url_is_public(url: str) -> bool:
    """SSRF guard for the photo-download fetch: only http(s) URLs whose host
    resolves to a public IP may be fetched. Prevents the bridge from being
    used to probe internal services (metadata endpoints, LAN hosts)."""
    import ipaddress
    import socket
    from urllib.parse import urlparse

    try:
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            return False
        host = parts.hostname.rstrip(".")
        infos = socket.getaddrinfo(host, None)
    except Exception:  # noqa: BLE001 - unparseable/unresolvable → refuse
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if not ip.is_global or ip.is_reserved or ip.is_multicast:
            return False
    return True


@server.tool(name="get_chat_history", description="Recent messages in a chat (in-memory bridge log).")
async def get_chat_history(chat_id: int, limit: int = 10) -> str:
    entries = chat_log.get(chat_id, [])[-limit:]
    return json.dumps(entries, ensure_ascii=False)


@server.tool(name="broadcast", description="Send a message to the learned owner chat.")
async def broadcast(text: str) -> str:
    owner = _owner_chat_id()
    if owner is None:
        return json.dumps({"ok": False, "error": "no owner chat known yet"})
    ok = await send_to_chat(owner, text)
    return json.dumps({"ok": ok, "chat_id": owner})


# ── Command dispatch (pure logic, unit-tested) ───────────────────────────

HELP_TEXT = (
    "I'm Iris, your memory engine.\n\n"
    "Commands:\n"
    "/start — bind this chat as the owner\n"
    "/mind — show what I remember (MEMORY.md, USER.md, dreams, skills)\n"
    "/sleep — run the dream cycle now\n"
    "/wake — morning briefing (retention + rot + last dream)\n"
    "/forget <text> — retire a memory (asks for confirmation)\n"
    "/skills — list my procedural memory\n"
    "/tasks — pending scheduled tasks\n"
    "/rot — decayed memories\n"
    "/help — this message\n\n"
    "Anything else: just talk to me."
)


@dataclass
class PendingForget:
    query: str
    candidate: str
    path: str
    chunk_index: int


class CommandDispatcher:
    """Maps a Telegram command to an iris-core HTTP call. Testable with a fake client."""

    def __init__(self, core_url: str, client: httpx.AsyncClient | None = None) -> None:
        self.core_url = core_url.rstrip("/")
        self.pending: dict[int, PendingForget] = {}
        self._owns_client = client is None
        self._client = client

    async def dispatch(self, chat_id: int, text: str) -> str | None:
        """Return the reply to send, or None if the message is not a command."""
        if not text.startswith("/"):
            return None
        parts = text.split(maxsplit=1)
        cmd, arg = parts[0].lower(), parts[1] if len(parts) > 1 else ""
        client = self._client or httpx.AsyncClient(timeout=300)
        try:
            if cmd == "/start":
                return "Owner chat bound. Say hi or type /help."
            if cmd == "/help":
                return HELP_TEXT
            if cmd == "/mind":
                r = await client.get(f"{self.core_url}/mind", headers=_core_headers())
                return _format_mind(r.json())
            if cmd == "/sleep":
                r = await client.post(f"{self.core_url}/sleep", headers=_core_headers())
                return _format_sleep(r.json())
            if cmd == "/wake":
                ret = await client.get(f"{self.core_url}/retention", headers=_core_headers())
                rot = await client.get(f"{self.core_url}/rot", headers=_core_headers())
                return _format_wake(ret.json(), rot.json())
            if cmd == "/rot":
                r = await client.get(f"{self.core_url}/rot", headers=_core_headers())
                return _format_rot(r.json())
            if cmd == "/retention":
                r = await client.get(f"{self.core_url}/retention", headers=_core_headers())
                return _format_retention(r.json())
            if cmd == "/skills":
                r = await client.get(f"{self.core_url}/skills", headers=_core_headers())
                return _format_skills(r.json())
            if cmd == "/tasks":
                r = await client.get(f"{self.core_url}/tasks", headers=_core_headers())
                return _format_tasks(r.json())
            if cmd == "/forget":
                return await self._forget(chat_id, arg, client)
            if cmd == "/forget-confirm":
                return await self._forget_confirm(chat_id, client)
            if cmd == "/forget-cancel":
                self.pending.pop(chat_id, None)
                return "Forget cancelled — nothing was touched."
            return f"Unknown command {cmd}. Try /help."
        finally:
            if self._owns_client:
                await client.aclose()

    async def _forget(self, chat_id: int, arg: str, client: httpx.AsyncClient) -> str:
        if not arg.strip():
            return "Usage: /forget <what to forget>"
        r = await client.post(
            f"{self.core_url}/forget", json={"query": arg}, headers=_core_headers()
        )
        data = r.json()
        if not data.get("candidates"):
            return "Nothing in memory matched that."
        c = data["candidates"][0]
        self.pending[chat_id] = PendingForget(arg, c["content"], c["path"], c["chunk_index"])
        return (
            f"Candidate to retire:\n\n{c['content']}\n\n"
            f"Reply /forget-confirm to retire it, /forget-cancel to abort."
        )

    async def _forget_confirm(self, chat_id: int, client: httpx.AsyncClient) -> str:
        p = self.pending.pop(chat_id, None)
        if p is None:
            return "No pending forget. Run /forget <text> first."
        r = await client.post(
            f"{self.core_url}/forget/confirm",
            json={"path": p.path, "chunk_index": p.chunk_index},
            headers=_core_headers(),
        )
        data = r.json()
        if not data.get("ok"):
            return f"Forget failed: {data.get('error', 'unknown')}"
        return f"Retired. The entry is superseded, not deleted — provenance kept."


def _format_mind(data: dict) -> str:
    out = ["Iris's mind:"]
    out.append(f"\n— MEMORY.md —\n{data.get('memory', '')}")
    out.append(f"\n— USER.md —\n{data.get('user', '')}")
    out.append(f"\n— Recent dreams —\n{data.get('dreams_tail', '')}")
    if data.get("skills"):
        out.append(f"\n— Skills ({len(data['skills'])}) —\n" + ", ".join(data["skills"]))
    stats = data.get("stats", {})
    out.append(f"\n— Index — {stats.get('total_chunks', '?')} chunks")
    return "\n".join(out)[:3900]


def _format_sleep(data: dict) -> str:
    return (
        f"Dream cycle done. staged={data.get('staged', 0)} promoted={data.get('promoted', 0)} "
        f"themes={data.get('themes', 0)} added={data.get('added', 0)} superseded={data.get('superseded', 0)}"
    )


def _format_wake(ret: dict, rot: dict) -> str:
    chunks = ret.get("chunks", [])
    if not chunks:
        return "No memory chunks yet. Say something and I'll start remembering."
    low = [c for c in chunks if c.get("retention", 1.0) < 0.5]
    out = [
        f"Good morning. {len(chunks)} memory chunks, "
        f"{len(low)} decaying, {rot.get('count', 0)} flagged as rot."
    ]
    return "\n".join(out)


def _format_rot(data: dict) -> str:
    entries = data.get("entries", [])
    if not entries:
        return "No rot detected — everything is fresh."
    out = [f"{data.get('count')} memories decayed past the rot threshold:"]
    for e in entries[:10]:
        out.append(f"• {e['content'][:80]} (retention {e['retention']:.2f}, {e['age_days']}d)")
    return "\n".join(out)


def _format_retention(data: dict) -> str:
    chunks = data.get("chunks", [])
    if not chunks:
        return "No chunks yet."
    avg = sum(c.get("retention", 1.0) for c in chunks) / len(chunks)
    return f"{len(chunks)} chunks, average retention {avg:.2f}."


def _format_skills(data: dict) -> str:
    skills = data.get("skills", [])
    if not skills:
        return "No skills yet. Teach me one with a 'write a skill for...' message."
    return "\n".join(f"• {s['name']}: {s['description']}" for s in skills)


def _format_tasks(data: dict) -> str:
    tasks = data.get("tasks", [])
    if not tasks:
        return "No scheduled tasks."
    out = [f"{len(tasks)} scheduled task(s):"]
    for t in sorted(tasks, key=lambda t: t.get("run_at", "")):
        out.append(f"• {t.get('run_at', '?')} — {t.get('instruction', '')[:80]}")
    return "\n".join(out)


async def _typing_loop(chat_id: int) -> None:
    """Repeat the Telegram 'typing…' action every few seconds until the turn
    is done — Iris feels alive instead of frozen while the LLM thinks."""
    try:
        while True:
            await _tg("sendChatAction", chat_id=chat_id, action="typing")
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - typing is cosmetic; never crash for it
        return


async def _stream_chat_turn(chat_id: int, text: str, image: str | None = None) -> tuple[str, bool]:
    """Stream a chat turn from /chat/stream into a progressively edited
    Telegram message: typing indicator while thinking, then a growing reply
    (throttled edits), with tool activity shown inline. Returns
    (final_reply_text, delivered) where delivered means at least one message
    was sent to the chat (the caller must not send a second copy)."""
    typing_task = asyncio.create_task(_typing_loop(chat_id))
    sent_id: int | None = None
    buf = ""
    display = ""
    last_edit = 0.0
    throttle = 1.2
    payload: dict = {"message": text, "session_id": str(chat_id)}
    if image:
        payload["image"] = image
    try:
        async with httpx.AsyncClient(timeout=300) as client:
            async with client.stream(
                "POST",
                f"{IRIS_CORE_URL}/chat/stream",
                json=payload,
                headers=_core_headers(),
            ) as r:
                async for line in r.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    kind = ev.get("kind")
                    if kind == "text":
                        buf += ev.get("delta", "")
                        display = buf
                    elif kind == "reply":
                        buf = ev.get("text", buf)
                        display = buf
                    elif kind == "tool_call" and not display.endswith("…"):
                        name = (ev.get("call") or {}).get("name", "tool")
                        display = f"{buf or '…'}\n\n🔧 {name}…"
                    elif kind == "approval":
                        # Human-in-the-loop: telegram forgets stay on the
                        # bridge's own two-phase flow, so cancel the graph
                        # interrupt and point the owner at /forget.
                        buf = "I'd like your OK before touching that memory — reply /forget <text> and confirm there."
                        display = buf
                        try:
                            async with httpx.AsyncClient(timeout=30) as c:
                                await c.post(
                                    f"{IRIS_CORE_URL}/chat/resume",
                                    json={"session_id": str(chat_id), "decision": "cancelled"},
                                    headers=_core_headers(),
                                )
                        except Exception:  # noqa: BLE001 - best-effort cancel
                            log.warning("approval cancel failed", exc_info=True)
                    if not display or kind not in ("text", "reply", "tool_call", "approval"):
                        continue
                    now = time.monotonic()
                    if sent_id is None:
                        result = await _tg(
                            "sendMessage", chat_id=chat_id, text=display, disable_web_page_preview=True
                        )
                        if result:
                            sent_id = result.get("message_id")
                            last_edit = now
                    elif now - last_edit >= throttle:
                        await _tg(
                            "editMessageText", chat_id=chat_id, message_id=sent_id, text=display,
                            disable_web_page_preview=True,
                        )
                        last_edit = now
        if sent_id is not None and display != buf:
            # final sync edit with the clean reply (no tool decoration)
            await _tg(
                "editMessageText", chat_id=chat_id, message_id=sent_id, text=buf,
                disable_web_page_preview=True,
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("streamed chat failed: %s", exc)
        if not buf:
            buf = "I can't reach my brain right now — try again in a bit."
    finally:
        typing_task.cancel()
    return buf, sent_id is not None


# ── Bridge loop ───────────────────────────────────────────────────────────

async def _handle_voice(chat_id: int, voice: dict) -> None:
    """Voice message → download audio → iris-core /voice → reply.

    The transcript comes back with the reply; the original audio never
    touches disk here (streamed through memory).
    """
    await send_to_chat(chat_id, "🎙 Listening…")
    try:
        file_id = voice["file_id"]
        file_info = await _tg("getFile", file_id=file_id)
        file_path = file_info.get("file_path")
        if not file_path:
            raise RuntimeError("telegram returned no file path")
        async with httpx.AsyncClient(timeout=60) as client:
            # Telegram serves file downloads from the /file/ base URL, not
            # the Bot API one — https://api.telegram.org/file/bot<token>/<path>
            dl = await client.get(f"{FILE_API}/{file_path}")
            dl.raise_for_status()
            r = await client.post(
                f"{IRIS_CORE_URL}/voice",
                data={"user_id": str(chat_id)},
                files={"audio": ("voice.ogg", dl.content, "audio/ogg")},
                headers=_core_headers(),
                timeout=180,
            )
            data = r.json()
            reply = data.get("reply", "I couldn't hear you.")
            transcript = data.get("transcript")
    except Exception as exc:  # noqa: BLE001 - voice must never kill the poller
        log.warning("voice handling failed: %s", exc)
        reply = "I couldn't hear you — voice needs GROQ_API_KEY, and a clear recording."
        transcript = None
    if transcript:
        _log_chat(chat_id, "user", f"[voice] {transcript[:200]}")
    await send_to_chat(chat_id, reply)
    _log_chat(chat_id, "iris", reply)


async def _handle_photo(chat_id: int, photo: list[dict], caption: str = "") -> None:
    """Photo message → download the largest size → base64 data URI → /chat.

    Iris sees the image through the vision-capable strong model; the caption
    (the agent's own description) is logged to the daily note by the write
    path. Reply is sent progressively like plain messages.
    """
    if not photo:
        await send_to_chat(chat_id, "I got a photo but couldn't read it.")
        return
    largest = max(photo, key=lambda p: p.get("file_size", 0) or 0)
    try:
        file_info = await _tg("getFile", file_id=largest["file_id"])
        file_path = file_info.get("file_path")
        if not file_path:
            raise RuntimeError("telegram returned no file path")
        mime = file_path.rsplit(".", 1)[-1].lower()
        mime_type = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "gif"}.get(mime, "jpeg")
        async with httpx.AsyncClient(timeout=60) as client:
            dl = await client.get(f"{FILE_API}/{file_path}")
            dl.raise_for_status()
        data_uri = f"data:image/{mime_type};base64," + base64.b64encode(dl.content).decode("ascii")
        text = caption.strip() or "[photo attached]"
        reply, delivered = await _stream_chat_turn(chat_id, text, image=data_uri)
        if not delivered:
            await send_to_chat(chat_id, reply)
        _log_chat(chat_id, "user", f"[photo] {text[:200]}")
        _log_chat(chat_id, "iris", reply)
    except Exception as exc:  # noqa: BLE001 - photo handling must never kill the poller
        log.warning("photo handling failed: %s", exc)
        await send_to_chat(chat_id, "I couldn't read that photo — try sending it again.")


async def _poll_loop() -> None:
    """Long-poll Telegram updates; forward messages into iris-core /chat."""
    offset = 0
    while True:
        try:
            updates = await _tg("getUpdates", offset=offset, timeout=30)
            for u in updates or []:
                offset = max(offset, u["update_id"] + 1)
                msg = u.get("message") or u.get("edited_message")
                if not msg:
                    continue
                chat_id = msg["chat"]["id"]
                text = (msg.get("text") or "").strip()
                user = (msg.get("from") or {}).get("first_name", "Owner")

                voice = msg.get("voice")
                photo = msg.get("photo")
                if not text and not voice and photo:
                    await _handle_photo(chat_id, photo, msg.get("caption") or "")
                    continue
                if not text and voice:
                    await _handle_voice(chat_id, voice)
                    continue
                if not text:
                    await send_to_chat(chat_id, "I can only read text, voice, or photos for now.")
                    continue
                _log_chat(chat_id, "user", text)
                if text.startswith("/start"):
                    _save_owner(chat_id)
                if text.startswith("/"):
                    dispatcher = CommandDispatcher(IRIS_CORE_URL)
                    reply = await dispatcher.dispatch(chat_id, text)
                    if reply:
                        await send_to_chat(chat_id, reply)
                        _log_chat(chat_id, "iris", reply)
                    continue
                # plain message → the graph (onboarding wizard included)
                reply, delivered = await _stream_chat_turn(chat_id, text)
                if not delivered:
                    await send_to_chat(chat_id, reply)
                _log_chat(chat_id, "iris", reply)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - polling must never die
            # str(exc) is empty for some transport exceptions — include the
            # type name so the error is actually diagnosable in the log.
            log.warning("poll error (%s): %s", type(exc).__name__, exc or "(no detail)")
            await asyncio.sleep(3)


def _log_chat(chat_id: int, role: str, text: str) -> None:
    chat_log.setdefault(chat_id, []).append({"role": role, "text": text[:2000]})
    if len(chat_log[chat_id]) > 200:
        chat_log[chat_id] = chat_log[chat_id][-100:]


async def main() -> None:
    if not BOT_TOKEN:
        log.error("TELEGRAM_BOT_TOKEN is not set; the bridge cannot run.")
        raise SystemExit(1)
    log.info("telegram-mcp starting; owner=%s", _owner_chat_id())
    poller = asyncio.create_task(_poll_loop())
    try:
        await server.run_streamable_http_async(
            host=os.environ.get("HOST", "0.0.0.0"),
            port=int(os.environ.get("PORT", "8100")),
            streamable_http_path="/mcp",
        )
    finally:
        poller.cancel()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())