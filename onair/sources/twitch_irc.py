"""Twitch chat source: anonymous, READ-ONLY IRC over TLS (lane: core).

- TLS to ``irc.chat.twitch.tv:6697`` with certificate *and* hostname verification
  (``ssl.create_default_context()``: ``CERT_REQUIRED`` + ``check_hostname``).
- Logs in anonymously as ``justinfan<random>``: no account, no token, nothing to leak.
- Requests ``twitch.tv/tags twitch.tv/commands`` and joins one channel.
- Read-only is enforced in code, not by convention: ``ReadOnlyWriter`` refuses every line except
  ``PASS`` / ``NICK`` / ``CAP`` / ``JOIN`` / ``PONG`` of the exact shapes this client sends, so
  it cannot send chat, whispers or moderation commands even if asked to.
- Answers ``PING`` with ``PONG``, honours ``RECONNECT``, and reconnects with exponential backoff
  (1 s doubling to 60 s, reset after a successful join).
- Each ``PRIVMSG`` becomes one untrusted ``ChatMsg`` (IRCv3 tags ``id``, ``user-id``,
  ``display-name``, ``first-msg``, ``badges``, ``emote-only``, ``tmi-sent-ts``). ``CLEARCHAT`` /
  ``CLEARMSG`` are reported so moderated chat is never answered; a channel-wide ``CLEARCHAT``
  (no target) clears everything waiting.
- Support arrives on the same anonymous connection: ``USERNOTICE`` by ``msg-id`` (``sub``,
  ``resub``, ``subgift``, ``submysterygift`` -> ``giftbomb``, ``giftpaidupgrade`` /
  ``primepaidupgrade`` -> ``sub``, ``raid``) and ``PRIVMSG`` with a ``bits`` tag (a cheer, with
  its ``Cheer100``-style tokens stripped from the message). Each becomes one untrusted
  ``SupportEvent``; other ``msg-id``s are reported as ignored.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import re
import ssl
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..types import ChatMsg, SupportEvent

logger = logging.getLogger(__name__)

TWITCH_HOST = "irc.chat.twitch.tv"
TWITCH_TLS_PORT = 6697
CAPS = "twitch.tv/tags twitch.tv/commands"
_CHANNEL_RE = re.compile(r"^[a-z0-9_]{3,25}$")
_WRITE_ALLOW = re.compile(
    r"^(?:CAP REQ :twitch\.tv/tags twitch\.tv/commands"
    r"|CAP END"
    r"|PASS SCHMOOPIIE"
    r"|NICK justinfan\d{4,8}"
    r"|JOIN #[a-z0-9_]{3,25}"
    r"|PONG :?[A-Za-z0-9_.\-]{1,64})$"
)


class ReadOnlyViolation(RuntimeError):
    """A write other than the anonymous login / keepalive allowlist was attempted."""


def allowed_write(line: str) -> bool:
    return isinstance(line, str) and "\r" not in line and "\n" not in line and bool(_WRITE_ALLOW.match(line))


def normalize_channel(channel: str) -> str:
    c = (channel or "").strip().lower().lstrip("#")
    if not _CHANNEL_RE.match(c):
        raise ValueError("invalid Twitch channel name")
    return c


# --------------------------------------------------------------------------------------------
# parsing (pure)


@dataclass
class IrcLine:
    tags: dict[str, str] = field(default_factory=dict)
    prefix: str = ""
    command: str = ""
    params: list[str] = field(default_factory=list)


_TAG_ESCAPES = {":": ";", "s": " ", "\\": "\\", "r": "\r", "n": "\n"}


def _unescape_tag(v: str) -> str:
    return re.sub(r"\\(.)?", lambda m: _TAG_ESCAPES.get(m.group(1) or "", m.group(1) or ""), v)


def parse_line(raw: str) -> IrcLine | None:
    rest = (raw or "").rstrip("\r\n")
    if not rest:
        return None
    tags: dict[str, str] = {}
    if rest.startswith("@"):
        sp = rest.find(" ")
        if sp < 0:
            return None
        for kv in rest[1:sp].split(";"):
            if not kv:
                continue
            k, eq, v = kv.partition("=")
            tags[k] = _unescape_tag(v) if eq else ""
        rest = rest[sp + 1 :].lstrip(" ")
    prefix = ""
    if rest.startswith(":"):
        sp = rest.find(" ")
        if sp < 0:
            return None
        prefix, rest = rest[1:sp], rest[sp + 1 :].lstrip(" ")
    params: list[str] = []
    while rest:
        if rest.startswith(":"):
            params.append(rest[1:])
            break
        sp = rest.find(" ")
        if sp < 0:
            params.append(rest)
            break
        params.append(rest[:sp])
        rest = rest[sp + 1 :].lstrip(" ")
    if not params:
        return None
    command = params.pop(0).upper()
    return IrcLine(tags, prefix, command, params)


def to_chat_msg(line: IrcLine, *, now: float | None = None) -> ChatMsg | None:
    """One PRIVMSG -> one untrusted ChatMsg; anything else -> None. A cheer (``bits`` tag) is a
    support event, not chat: see ``to_support_event``."""
    if line.command != "PRIVMSG" or len(line.params) < 2 or line.tags.get("bits"):
        return None
    now = time.time() if now is None else now
    channel = line.params[0].lstrip("#").lower()
    text = line.params[1]
    m = re.fullmatch(r"\x01ACTION (.*)\x01", text, re.S)
    if m:
        text = m.group(1)
    elif text.startswith("\x01"):
        return None  # other CTCP
    nick = line.prefix.split("!", 1)[0]
    tags = line.tags
    try:
        sent = int(tags.get("tmi-sent-ts", "")) / 1000.0
        ts = sent if abs(sent - now) < 600 else now
    except ValueError:
        ts = now
    badges = tuple(b.split("/", 1)[0] for b in (tags.get("badges") or "").split(",") if b)
    return ChatMsg(
        platform="twitch",
        channel=channel,
        msg_id=tags.get("id") or f"{nick}-{int(now * 1000)}",
        user_id=tags.get("user-id") or nick,
        display_name=tags.get("display-name") or nick,
        text=text,
        ts=ts,
        first_msg=tags.get("first-msg") == "1",
        badges=badges,
        emote_only=tags.get("emote-only") == "1",
    )


# USERNOTICE msg-id -> SupportEvent kind. Anything else is ignored (with a receipt upstream).
SUPPORT_MSG_IDS = {
    "sub": "sub",
    "resub": "resub",
    "subgift": "subgift",
    "anonsubgift": "subgift",
    "submysterygift": "giftbomb",
    "anonsubmysterygift": "giftbomb",
    "giftpaidupgrade": "sub",
    "anongiftpaidupgrade": "sub",
    "primepaidupgrade": "sub",
    "raid": "raid",
}
_ANON_GIFTER = "ananonymousgifter"
_CHEER_TOKEN = re.compile(r"(?<!\S)[A-Za-z][A-Za-z0-9]*?\d+(?!\S)")


def _int_tag(tags: dict[str, str], key: str) -> int:
    try:
        return max(0, int(tags.get(key, "") or 0))
    except ValueError:
        return 0


def strip_cheer_tokens(text: str) -> str:
    """Remove ``Cheer100``-style cheermote tokens from a cheer message."""
    return " ".join(_CHEER_TOKEN.sub(" ", text or "").split())


def _event_ts(tags: dict[str, str], now: float) -> float:
    try:
        sent = int(tags.get("tmi-sent-ts", "")) / 1000.0
        return sent if abs(sent - now) < 600 else now
    except ValueError:
        return now


def to_support_event(line: IrcLine, *, now: float | None = None) -> SupportEvent | str | None:
    """A sub / gift / gift bomb / raid ``USERNOTICE`` or a ``bits`` cheer ``PRIVMSG`` -> one untrusted
    ``SupportEvent``. An unhandled ``USERNOTICE`` returns its ``msg-id`` string (ignored with a
    receipt); anything else returns None. Names and messages stay untrusted viewer content: the
    stage guards names and keeps messages out of the turn by default.
    """
    now = time.time() if now is None else now
    tags = line.tags
    if line.command == "PRIVMSG" and tags.get("bits") and len(line.params) >= 2:
        nick = line.prefix.split("!", 1)[0]
        text = line.params[1]
        m = re.fullmatch(r"\x01ACTION (.*)\x01", text, re.S)
        if m:
            text = m.group(1)
        return SupportEvent(
            platform="twitch",
            channel=line.params[0].lstrip("#").lower(),
            event_id=tags.get("id") or f"bits-{nick}-{int(now * 1000)}",
            kind="bits",
            user_id=tags.get("user-id") or nick,
            display_name=tags.get("display-name") or nick,
            ts=_event_ts(tags, now),
            amount=_int_tag(tags, "bits"),
            message=strip_cheer_tokens(text),
        )
    if line.command != "USERNOTICE" or not line.params:
        return None
    msg_id = tags.get("msg-id", "")
    kind = SUPPORT_MSG_IDS.get(msg_id)
    if kind is None:
        return msg_id or "unknown"
    login = (tags.get("login") or "").lower()
    name = tags.get("display-name") or login
    user_id = tags.get("user-id") or login
    if login == _ANON_GIFTER or msg_id.startswith("anon"):
        name, user_id = "", "anonymous-gifter"  # shows as "someone"
    plan = tags.get("msg-param-sub-plan", "")
    amount = 0
    if kind == "giftbomb":
        amount = _int_tag(tags, "msg-param-mass-gift-count")
    elif kind == "raid":
        amount = _int_tag(tags, "msg-param-viewerCount")
        name = tags.get("msg-param-displayName") or name
    months = 0
    if kind in ("sub", "resub"):
        months = _int_tag(tags, "msg-param-cumulative-months") or _int_tag(tags, "msg-param-months")
    return SupportEvent(
        platform="twitch",
        channel=line.params[0].lstrip("#").lower(),
        event_id=tags.get("id") or f"{msg_id}-{user_id}-{int(now * 1000)}",
        kind=kind,  # type: ignore[arg-type]
        user_id=user_id,
        display_name=name,
        ts=_event_ts(tags, now),
        amount=amount,
        months=months,
        tier=plan if plan in ("1000", "2000", "3000", "Prime") else "",
        recipient=tags.get("msg-param-recipient-display-name", "") if kind == "subgift" else "",
        message=line.params[1] if len(line.params) > 1 and kind in ("sub", "resub") else "",
    )


# --------------------------------------------------------------------------------------------
# client


class ReadOnlyWriter:
    """Wraps a StreamWriter; the only way this client can put bytes on the wire."""

    def __init__(self, writer: asyncio.StreamWriter):
        self._w = writer
        self.sent: list[str] = []

    async def send(self, line: str) -> None:
        if not allowed_write(line):
            raise ReadOnlyViolation("write refused by the read-only allowlist")
        self.sent.append(line)
        self._w.write((line + "\r\n").encode("utf-8"))
        await self._w.drain()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._w.close()


OpenConnection = Callable[..., Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]


class TwitchChatSource:
    def __init__(
        self,
        channel: str,
        on_message: Callable[[ChatMsg], None],
        *,
        on_moderation: Callable[..., None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_support: Callable[[SupportEvent], None] | None = None,
        on_ignored_notice: Callable[[str], None] | None = None,
        host: str = TWITCH_HOST,
        port: int = TWITCH_TLS_PORT,
        open_connection: OpenConnection | None = None,
        ssl_context: ssl.SSLContext | None = None,
        backoff_initial: float = 1.0,
        backoff_max: float = 60.0,
    ):
        self.channel = normalize_channel(channel)
        self._on_message = on_message
        self._on_moderation = on_moderation
        self._on_status = on_status
        self._on_support = on_support
        self._on_ignored_notice = on_ignored_notice
        self.host, self.port = host, port
        self._open = open_connection or asyncio.open_connection
        self._ssl = ssl_context or ssl.create_default_context()
        if self._ssl.verify_mode != ssl.CERT_REQUIRED or not self._ssl.check_hostname:
            raise ValueError("TLS context must verify the certificate and hostname")
        self._backoff0, self._backoff_max = backoff_initial, backoff_max
        self.backoff = backoff_initial
        self.status = "idle"
        self.nick = f"justinfan{random.randint(10000, 99999)}"
        self._stopping = False
        self._writer: ReadOnlyWriter | None = None
        self._task: asyncio.Task | None = None
        self.connects = 0
        self.messages = 0
        self.support_events = 0

    def _set_status(self, s: str) -> None:
        self.status = s
        if self._on_status:
            with contextlib.suppress(Exception):
                self._on_status(s)

    def start(self) -> asyncio.Task:
        if self._task is None or self._task.done():
            self._stopping = False
            self._task = asyncio.get_running_loop().create_task(self.run())
        return self._task

    async def stop(self) -> None:
        self._stopping = True
        if self._writer:
            self._writer.close()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
        self._set_status("stopped")

    async def run(self) -> None:
        while not self._stopping:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except ReadOnlyViolation:
                logger.error("on-air twitch: read-only violation; stopping the source")
                self._set_status("read-only-violation")
                return
            except Exception as exc:
                self._set_status(f"error:{type(exc).__name__}")
                logger.warning("on-air twitch: session ended: %s", type(exc).__name__)
            if self._stopping:
                break
            delay = self.backoff
            self.backoff = min(max(self.backoff * 2, self._backoff0), self._backoff_max)
            self._set_status("reconnecting")
            await asyncio.sleep(delay)

    async def _session(self) -> None:
        self._set_status("connecting")
        reader, writer = await asyncio.wait_for(
            self._open(self.host, self.port, ssl=self._ssl, server_hostname=self.host), timeout=20.0
        )
        self.connects += 1
        w = ReadOnlyWriter(writer)
        self._writer = w
        self._set_status("tls-verified")
        try:
            await w.send(f"CAP REQ :{CAPS}")
            await w.send(f"NICK {self.nick}")
            await w.send(f"JOIN #{self.channel}")
            while not self._stopping:
                raw = await asyncio.wait_for(reader.readline(), timeout=360.0)  # Twitch PINGs ~every 5 min
                if not raw:
                    self._set_status("closed")
                    return
                if len(raw) > 16384:
                    continue
                again = await self._handle(raw.decode("utf-8", errors="replace"), w)
                if again:
                    return  # RECONNECT: drop this session; the loop reconnects promptly
        finally:
            w.close()
            self._writer = None

    async def _handle(self, raw: str, w: ReadOnlyWriter) -> bool:
        line = parse_line(raw)
        if line is None:
            return False
        cmd = line.command
        if cmd == "PING":
            token = (
                re.sub(r"[^A-Za-z0-9_.\-]", "", (line.params[0] if line.params else "tmi.twitch.tv"))[:64]
                or "tmi.twitch.tv"
            )
            await w.send(f"PONG :{token}")
            return False
        if cmd == "RECONNECT":
            self._set_status("server-reconnect")
            self.backoff = 0.0
            return True
        if cmd == "001":
            self._set_status("welcome")
        elif cmd == "JOIN":
            self._set_status("joined")
            self.backoff = self._backoff0
        elif cmd == "NOTICE":
            self._set_status("notice")
        elif cmd in ("CLEARCHAT", "CLEARMSG"):
            if self._on_moderation:
                with contextlib.suppress(Exception):
                    if cmd == "CLEARCHAT":
                        target = line.tags.get("target-user-id")
                        if target:
                            self._on_moderation(user_id=target, msg_id=None)
                        else:  # a channel-wide clear: drop every waiting chat item
                            self._on_moderation(user_id=None, msg_id=None, clear_all=True)
                    elif line.tags.get("target-msg-id"):
                        self._on_moderation(user_id=None, msg_id=line.tags.get("target-msg-id"))
        elif cmd == "USERNOTICE" or (cmd == "PRIVMSG" and line.tags.get("bits")):
            # support events (subs, gifts, raids, cheers): never chat, never instructions
            if line.params and line.params[0].lstrip("#").lower() != self.channel:
                return False
            ev = to_support_event(line)
            if isinstance(ev, SupportEvent):
                self.support_events += 1
                if self._on_support:
                    try:
                        self._on_support(ev)
                    except Exception:
                        logger.exception("on-air twitch: support handler error")
            elif isinstance(ev, str) and self._on_ignored_notice:
                with contextlib.suppress(Exception):
                    self._on_ignored_notice(ev)
        elif cmd == "PRIVMSG":
            msg = to_chat_msg(line)
            if msg is not None and msg.channel == self.channel:
                self.messages += 1
                try:
                    self._on_message(msg)
                except Exception:
                    logger.exception("on-air twitch: message handler error")
        return False
