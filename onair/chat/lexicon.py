"""Small built-in vocabularies for the chat gate and selector (lane: core).

Kept deliberately short and generic. ``COMMON_WORDS`` is used only to notice when a message
*decodes* into English under a simple cipher (rot13, atbash, reversal) while reading as gibberish
on its face; it is not a spell checker.
"""

from __future__ import annotations

COMMON_WORDS = frozenset(
    """
a about above after again against all also always am an and any anyone anything are around as ask
at away back bad be because been before being best better between big bit both but by call came
can cant car change chat check come could day did didnt do does doesnt dog done dont down each
easy else end enough even ever every everyone everything eye face fact far feel few find fine
first for found free friend from fun funny game gave get give go god going gone good got great
had hand happy hard has hate have having he head hear hello help her here hey hi him his home
hope hot house how i if im in into is isnt it its ive just keep kill kind know last later learn
least left let life like line little live long look lot love made make man many may maybe me
mean message might mind more most move much music must my name need never new next nice night
no not nothing now of off oh ok okay old on once one only open or other our out over own part
people place play please point put read real really right said same saw say says school secret
see seem seen she should show side since so some someone something soon sorry start stay still
stop stream sure take talk tell than thank thanks that thats the their them then there these they
thing think this those though thought time to today together told too took true try turn two
under until up us use very wait want was watch water way we well went were what when where which
while who why will with without word words work world would write wrong yeah year yes yet you
your youre
""".split()
)

# Emotes, hype tokens and filler that carry no content of their own (lowercase, after folding
# repeated letters: "loool" -> "lool").
EMOTE_TOKENS = frozenset(
    """
pogchamp poggers pog kekw lulw lul lol lool lmao lmfao rofl kappa kappapride catjam monkas copium
omegalul pepehands pepega sadge uwu owo o7 <3 xd gg ez hype letsgo lets go gooo first based true
this same real mood vibe vibes cozy hi hii hai hello hey yo ok okay yes yeah yep nah no nice wow
omg w l fr ngl tbh so the is it and chat cute love pls plz wild clean insane raid squad gang in
here we haha hehe ayy ayo bruh bro sheesh lfg goat clutch peak hypers pogu residentsleeper
""".split()
)

# Tokens that count as filler (not emotes) when measuring a hype wall.
FILLER_TOKENS = frozenset("the is it and so in here we this chat".split())

# Everyday chat slang that is not a cipher payload.
SLANG = frozenset(
    """
uwu owo kekw lul lulw pog poggers pogchamp catjam kappa omegalul monkas copium sadge tryna gonna
wanna gotta kinda sorta lemme ngl tbh imo imho idk irl lmao lmfao rofl smh fr frfr bruh bro pls
plz thx ty tysm ily vtuber vod vods discord twitch youtube obs ai gg ez afk brb gn gm
""".split()
)
