"""Twitch IRC source: parsing, read-only writer, PING/PONG, RECONNECT, backoff, TLS policy."""

from __future__ import annotations

import asyncio
import ssl

import pytest

from onair.sources.twitch_irc import (
    ReadOnlyViolation,
    ReadOnlyWriter,
    TwitchChatSource,
    allowed_write,
    normalize_channel,
    parse_line,
    strip_cheer_tokens,
    to_chat_msg,
    to_support_event,
)

NOW = 1_800_000_000.0
PRIV = (
    "@badge-info=;badges=broadcaster/1,subscriber/12;display-name=Viewer_42;emote-only=1;first-msg=1;"
    "id=abc-123;tmi-sent-ts=1800000000000;user-id=4242 :viewer_42!viewer_42@viewer_42.tmi.twitch.tv "
    "PRIVMSG #chan :hello there; friends"
)


def test_parse_tags_prefix_params():
    line = parse_line(PRIV + "\r\n")
    assert line is not None
    assert line.command == "PRIVMSG"
    assert line.params == ["#chan", "hello there; friends"]
    assert line.tags["display-name"] == "Viewer_42"
    assert line.prefix.startswith("viewer_42!")


def test_tag_unescaping():
    line = parse_line(r"@msg=a\sb\:c\\d\re;x= :tmi.twitch.tv NOTICE #c :hi")
    assert line.tags["msg"] == "a b;c\\d\re"
    assert line.tags["x"] == ""


def test_to_chat_msg_fields():
    m = to_chat_msg(parse_line(PRIV), now=NOW)
    assert m is not None
    assert (m.platform, m.channel, m.msg_id, m.user_id, m.display_name) == (
        "twitch",
        "chan",
        "abc-123",
        "4242",
        "Viewer_42",
    )
    assert m.text == "hello there; friends"
    assert m.first_msg is True and m.emote_only is True
    assert m.badges == ("broadcaster", "subscriber")
    assert m.ts == pytest.approx(NOW)


def test_action_and_ctcp_and_bad_ts():
    act = to_chat_msg(parse_line(":u!u@u PRIVMSG #chan :\x01ACTION waves\x01"), now=NOW)
    assert act is not None and act.text == "waves"
    assert to_chat_msg(parse_line(":u!u@u PRIVMSG #chan :\x01VERSION\x01"), now=NOW) is None
    far = to_chat_msg(parse_line("@tmi-sent-ts=1 :u!u@u PRIVMSG #chan :hi"), now=NOW)
    assert far is not None and far.ts == NOW  # implausible timestamps fall back to now
    assert to_chat_msg(parse_line(":tmi.twitch.tv NOTICE #chan :x"), now=NOW) is None


@pytest.mark.parametrize(
    "line",
    [
        "CAP REQ :twitch.tv/tags twitch.tv/commands",
        "NICK justinfan12345",
        "JOIN #some_channel",
        "PONG :tmi.twitch.tv",
        "PASS SCHMOOPIIE",
    ],
)
def test_writer_allows_only_login_and_keepalive(line):
    assert allowed_write(line)


@pytest.mark.parametrize(
    "line",
    [
        "PRIVMSG #chan :hello",
        "PRIVMSG #chan :/ban someone",
        "WHISPER x :hi",
        "PART #chan",
        "NICK realname",
        "PASS oauth:abcdef",
        "JOIN #chan,#other",
        "PONG :x\r\nPRIVMSG #chan :sneaky",
        "QUIT",
        "CAP REQ :twitch.tv/membership",
        "JOIN #UPPER",
    ],
)
def test_writer_refuses_everything_else(line):
    assert not allowed_write(line)


class FakeWriter:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, b: bytes) -> None:
        self.data += b

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def lines(self) -> list[str]:
        return [x for x in self.data.decode().split("\r\n") if x]


def test_readonly_writer_raises_on_chat():
    async def go():
        w = ReadOnlyWriter(FakeWriter())  # type: ignore[arg-type]
        with pytest.raises(ReadOnlyViolation):
            await w.send("PRIVMSG #chan :hi")
        await w.send("PONG :tmi.twitch.tv")
        assert w.sent == ["PONG :tmi.twitch.tv"]

    asyncio.run(go())


def test_channel_validation():
    assert normalize_channel("#SomeChannel") == "somechannel"
    for bad in ("", "a", "has space", "x" * 26, "bad#name"):
        with pytest.raises(ValueError):
            normalize_channel(bad)


def test_tls_context_must_verify():
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with pytest.raises(ValueError):
        TwitchChatSource("chan", lambda m: None, ssl_context=ctx)
    src = TwitchChatSource("chan", lambda m: None)
    assert src._ssl.verify_mode == ssl.CERT_REQUIRED and src._ssl.check_hostname


def _fake_open(scripts: list[list[str]], writers: list[FakeWriter], calls: list[dict]):
    async def open_connection(host, port, **kw):
        calls.append({"host": host, "port": port, **kw})
        reader = asyncio.StreamReader()
        lines = scripts.pop(0) if scripts else []
        for line in lines:
            reader.feed_data((line + "\r\n").encode())
        reader.feed_eof()
        w = FakeWriter()
        writers.append(w)
        return reader, w

    return open_connection


def test_session_login_ping_privmsg_reconnect_and_backoff():
    got, mods, statuses, writers, calls = [], [], [], [], []
    scripts = [
        [
            ":tmi.twitch.tv 001 justinfan1 :Welcome",
            ":justinfan1!j@j JOIN #chan",
            "PING :tmi.twitch.tv",
            PRIV,
            "@target-msg-id=abc-123;login=x :tmi.twitch.tv CLEARMSG #chan :gone",
            ":u!u@u PRIVMSG #otherchan :not ours",
            ":tmi.twitch.tv RECONNECT",
        ],
        [":tmi.twitch.tv 001 justinfan1 :Welcome"],  # closes without a JOIN
        [],
    ]

    async def go():
        src = TwitchChatSource(
            "chan",
            got.append,
            on_moderation=lambda **kw: mods.append(kw),
            on_status=statuses.append,
            open_connection=_fake_open(scripts, writers, calls),
            backoff_initial=0.01,
            backoff_max=0.04,
        )
        task = src.start()
        for _ in range(200):
            await asyncio.sleep(0.01)
            if len(calls) >= 3:
                break
        await src.stop()
        assert task.done()
        return src

    src = asyncio.run(go())
    first = writers[0].lines()
    assert first[0] == "CAP REQ :twitch.tv/tags twitch.tv/commands"
    assert first[1].startswith("NICK justinfan") and first[2] == "JOIN #chan"
    assert "PONG :tmi.twitch.tv" in first
    assert all(allowed_write(x) for w in writers for x in w.lines())
    assert not any(x.startswith("PRIVMSG") for w in writers for x in w.lines())
    assert [m.msg_id for m in got] == ["abc-123"]  # other channel ignored
    assert mods == [{"user_id": None, "msg_id": "abc-123"}]
    assert "server-reconnect" in statuses and "welcome" in statuses
    assert all(c["server_hostname"] == "irc.chat.twitch.tv" and isinstance(c["ssl"], ssl.SSLContext) for c in calls)
    assert len(calls) >= 3  # reconnected after RECONNECT and after EOF
    assert src.backoff <= 0.04  # bounded exponential backoff


def test_backoff_grows_and_is_capped():
    attempts = []

    async def failing_open(host, port, **kw):
        attempts.append(asyncio.get_running_loop().time())
        raise ConnectionRefusedError

    async def go():
        src = TwitchChatSource(
            "chan", lambda m: None, open_connection=failing_open, backoff_initial=0.01, backoff_max=0.05
        )
        src.start()
        await asyncio.sleep(0.35)
        await src.stop()
        return src

    src = asyncio.run(go())
    assert len(attempts) >= 4
    gaps = [b - a for a, b in zip(attempts, attempts[1:], strict=False)]
    assert gaps[1] >= gaps[0] * 1.5 or gaps[0] >= 0.009
    assert src.backoff == pytest.approx(0.05)


# ----------------------------------------------------------------------------- support events (USERNOTICE / bits)

_UN = (
    "@badge-info=subscriber/{m};badges=subscriber/0;color=#1E90FF;display-name={name};emotes=;flags=;"
    "id={eid};login={login};mod=0;msg-id={msgid};{params}room-id=100000001;subscriber=1;"
    "system-msg=a\\ssystem\\smessage\\swe\\snever\\suse;tmi-sent-ts=1800000000000;user-id={uid};user-type= "
    ":tmi.twitch.tv USERNOTICE #chan{tail}"
)


def usernotice(msgid, params="", *, name="viewer_42", login=None, uid="200000042", eid=None, m=1, tail=""):
    return _UN.format(
        m=m,
        name=name,
        login=login or name.lower(),
        eid=eid or f"evt-{msgid}",
        msgid=msgid,
        params=params,
        uid=uid,
        tail=f" :{tail}" if tail else "",
    )


SUPPORT_FIXTURES = {
    "sub": usernotice(
        "sub",
        "msg-param-cumulative-months=1;msg-param-months=0;msg-param-should-share-streak=0;"
        "msg-param-sub-plan-name=Channel\\sSubscription;msg-param-sub-plan=1000;msg-param-was-gifted=false;",
    ),
    "resub": usernotice(
        "resub",
        "msg-param-cumulative-months=7;msg-param-months=0;msg-param-streak-months=7;"
        "msg-param-sub-plan-name=Channel\\sSubscription;msg-param-sub-plan=2000;",
        m=7,
        tail="seven months already, love the builds",
    ),
    "prime": usernotice("sub", "msg-param-cumulative-months=1;msg-param-sub-plan=Prime;"),
    "subgift": usernotice(
        "subgift",
        "msg-param-gift-months=1;msg-param-months=1;msg-param-origin-id=123;"
        "msg-param-recipient-display-name=lucky_viewer;msg-param-recipient-id=300000001;"
        "msg-param-recipient-user-name=lucky_viewer;msg-param-sender-count=0;msg-param-sub-plan=1000;",
        name="gifter_1",
        uid="200000001",
    ),
    "submysterygift": usernotice(
        "submysterygift",
        "msg-param-mass-gift-count=5;msg-param-origin-id=123;msg-param-sender-count=12;msg-param-sub-plan=1000;",
        name="gifter_1",
        uid="200000001",
    ),
    "giftpaidupgrade": usernotice(
        "giftpaidupgrade", "msg-param-sender-login=gifter_1;msg-param-sender-name=gifter_1;", name="kept_going"
    ),
    "primepaidupgrade": usernotice("primepaidupgrade", "msg-param-sub-plan=1000;", name="prime_to_paid"),
    "raid": usernotice(
        "raid",
        "msg-param-displayName=RaidLeader;msg-param-login=raidleader;msg-param-viewerCount=37;",
        name="RaidLeader",
        uid="200000077",
    ),
    "anon": usernotice(
        "subgift",
        "msg-param-recipient-display-name=lucky_viewer;msg-param-sub-plan=1000;",
        name="AnAnonymousGifter",
        uid="274598607",
    ),
}
BITS = (
    "@badge-info=;badges=bits/100;bits=250;color=;display-name=cheery_cat;emotes=;first-msg=0;flags=;"
    "id=bits-evt-1;mod=0;room-id=100000001;subscriber=0;tmi-sent-ts=1800000000000;turbo=0;user-id=200000055;"
    "user-type= :cheery_cat!cheery_cat@cheery_cat.tmi.twitch.tv PRIVMSG #chan :Cheer100 great stream Cheer150 tonight"
)


@pytest.mark.parametrize(
    "fixture,kind,name,amount,months,tier",
    [
        ("sub", "sub", "viewer_42", 0, 1, "1000"),
        ("resub", "resub", "viewer_42", 0, 7, "2000"),
        ("prime", "sub", "viewer_42", 0, 1, "Prime"),
        ("subgift", "subgift", "gifter_1", 0, 0, "1000"),
        ("submysterygift", "giftbomb", "gifter_1", 5, 0, "1000"),
        ("giftpaidupgrade", "sub", "kept_going", 0, 0, ""),
        ("primepaidupgrade", "sub", "prime_to_paid", 0, 0, "1000"),
        ("raid", "raid", "RaidLeader", 37, 0, ""),
        ("anon", "subgift", "", 0, 0, "1000"),
    ],
)
def test_usernotice_fixtures_map_to_support_events(fixture, kind, name, amount, months, tier):
    from onair.types import SupportEvent

    ev = to_support_event(parse_line(SUPPORT_FIXTURES[fixture]), now=NOW)
    assert isinstance(ev, SupportEvent)
    assert (ev.kind, ev.display_name, ev.amount, ev.months, ev.tier) == (kind, name, amount, months, tier)
    assert ev.platform == "twitch" and ev.channel == "chan" and ev.ts == pytest.approx(NOW)
    assert "system message" not in ev.message  # Twitch's system-msg is never used
    if fixture == "resub":
        assert ev.message == "seven months already, love the builds"
    if fixture == "subgift":
        assert ev.recipient == "lucky_viewer" and ev.user_id == "200000001"
    if fixture == "anon":
        assert ev.user_id == "anonymous-gifter"


def test_bits_cheer_is_a_support_event_not_chat():
    line = parse_line(BITS)
    assert to_chat_msg(line, now=NOW) is None
    ev = to_support_event(line, now=NOW)
    assert (ev.kind, ev.amount, ev.display_name, ev.event_id) == ("bits", 250, "cheery_cat", "bits-evt-1")
    assert ev.message == "great stream tonight"  # Cheer100-style tokens stripped
    assert strip_cheer_tokens("Cheer1 hi BibleThump100 there ShowLove5000") == "hi there"


def test_unknown_usernotice_is_reported_and_ordinary_chat_is_not_support():
    assert to_support_event(parse_line(usernotice("announcement", "msg-param-color=PRIMARY;")), now=NOW) == (
        "announcement"
    )
    assert to_support_event(parse_line(usernotice("viewermilestone")), now=NOW) == "viewermilestone"
    assert to_support_event(parse_line(PRIV), now=NOW) is None


def test_source_routes_support_and_channel_wide_clear():
    got, sup, ignored, mods, writers, calls = [], [], [], [], [], []
    scripts = [
        [
            ":tmi.twitch.tv 001 justinfan1 :Welcome",
            ":justinfan1!j@j JOIN #chan",
            SUPPORT_FIXTURES["submysterygift"],
            BITS,
            usernotice("announcement"),
            SUPPORT_FIXTURES["raid"].replace("#chan", "#otherchan"),
            PRIV,
            "@room-id=100000001;tmi-sent-ts=1800000000000 :tmi.twitch.tv CLEARCHAT #chan",
            "@room-id=100000001;target-user-id=4242 :tmi.twitch.tv CLEARCHAT #chan :viewer_42",
        ]
    ]

    async def go():
        src = TwitchChatSource(
            "chan",
            got.append,
            on_moderation=lambda **kw: mods.append(kw),
            on_support=sup.append,
            on_ignored_notice=ignored.append,
            open_connection=_fake_open(scripts, writers, calls),
            backoff_initial=5,
        )
        src.start()
        for _ in range(200):
            await asyncio.sleep(0.01)
            if len(mods) >= 2:
                break
        await src.stop()

    asyncio.run(go())
    assert [e.kind for e in sup] == ["giftbomb", "bits"]  # other channel ignored
    assert ignored == ["announcement"]
    assert [m.msg_id for m in got] == ["abc-123"]  # the cheer never doubles as chat
    assert mods == [
        {"user_id": None, "msg_id": None, "clear_all": True},
        {"user_id": "4242", "msg_id": None},
    ]
    assert not any(x.startswith("PRIVMSG") for w in writers for x in w.lines())
