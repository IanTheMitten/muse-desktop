"""Lyric tags, taxonomy v3 -- pure: no database, no network.

`docs/notes/2026-10-11-lyric-taxonomy-draft.md` is the taxonomy and the
record of why each label reads the way it does (three pilot runs on hosted
Jev, 2026-10-12); `docs/specs/2026-10-12-lyric-tag-store.md` is the spec for
this module. It holds the same purity contract `muse/reco.py` and
`muse/drift.py` do: `muse/library/lyric_tags.py` stores, and
`muse/lyric_tag_batch.py` reads files and calls the model. A test asserts
nothing here imports `sqlite3` or a network client.

A song's tags are `{segment: {group: {label: probability}}}`. Segment `song`
holds the questions asked once per song (setting, mode, six severity flags);
the others are `hook`, `start`/`middle`/`end` (thirds of the sung span) or a
single `body`. Only raw probabilities are stored. `scores` and `tag_vector`
derive everything else when read, so changing a formula never needs a
re-ask.
"""

import hashlib
import re

from .lrc import parse_lrc

TAXONOMY_VERSION = 3
DETAIL_THRESHOLD = 0.25

# --- taxonomy ---------------------------------------------------------------
# Each group is one `choice` question: (instructions, {label: description}).
# The descriptions are the question text the model reads.

GROUPS = {
    "theme_broad": ("What is this part of the song mainly about?", {
        "love": "Romantic or sexual relationships: attraction, being together, conflict, loss of a partner.",
        "self": "The singer's own identity, self-worth, growth, or inner conflict, with no partner at the centre.",
        "struggle": "Mental-health difficulty, depression, anxiety, addiction, trauma, or feeling unable to cope.",
        "faith": "God, Jesus, worship, prayer, spiritual belief, or doubt about faith.",
        "ambition": "Success, money, fame, hustle, proving people wrong.",
        "celebration": "Partying, dancing, drinking, having fun, living for tonight.",
        "kinship": "Family, friends, home, or where one comes from, not romantic.",
        "loss": "Death, grief, or mourning someone who is gone, not a breakup.",
        "world": "Society, politics, injustice, or the state of the world.",
        "journey": "Travel, leaving, escape, freedom, being on the move.",
        "conflict": "Fighting someone who is not a partner: feuds, rivals, diss tracks, revenge, threats, "
                    "violence. If the enemy is an ex-partner, choose love.",
        "none": "No clear subject, or nonsense / pure sound."}),
    "emotion": ("What is the dominant feeling in this part of the song?", {
        "joy": "Happiness, delight, fun.", "tenderness": "Warmth, affection, gentleness.",
        "desire": "Wanting, craving, yearning for something or someone.",
        "hope": "Looking forward with optimism.",
        "pride": "Confidence, triumph, self-satisfaction.", "sadness": "Sorrow, hurt, heaviness.",
        "nostalgia": "Bittersweet fondness for the past.",
        "anger": "Rage, frustration, resentment, spite, being jaded.",
        "fear": "Anxiety, dread, insecurity.", "peace": "Calm, acceptance, contentment.",
        "awe": "Wonder, reverence, being overwhelmed by something greater.",
        "gratitude": "Thankfulness.", "none": "No clear feeling."}),
    "stance": ("How does the singer position themselves in this part of the song?", {
        "vulnerable": "Opening up, admitting weakness or need.",
        "pleading": "Begging, asking someone to stay, change, or come back.",
        "defiant": "Refusing, resisting, pushing back.", "boastful": "Bragging, showing off.",
        "celebratory": "Cheering, inviting others to enjoy.",
        "reflective": "Thinking it over calmly, looking inward.",
        "resigned": "Giving up, accepting defeat.", "accusing": "Blaming someone else.",
        "declaring": "Proclaiming something boldly as true.", "mocking": "Taunting, sarcasm, ridicule.",
        "none": "No clear stance."}),
    "address": ("Who are the words in this part of the song spoken to?", {
        "lover": "A current or hoped-for partner.", "ex": "A former partner.",
        "god": "God or a higher power.", "self": "The singer talking to themselves.",
        "crowd": "A general audience, listeners.",
        "specific_other": "A specific non-romantic person (parent, friend, enemy).",
        "narration": "No one; telling a story about others in third person.", "none": "Unclear."}),
    "time": ("Where in time is the attention of this part of the song?", {
        "past": "Remembering, looking back.", "present": "This moment, right now.",
        "future": "Hopes, plans, fears about what is coming.", "none": "No clear time focus."}),
    # Pilot v3a tagged happy love songs `struggling`; v3b's "longing can be
    # here" then lost heartbreak (drivers license 0.91 -> 0.08). v3c names
    # heartbreak and grief as darkness. `harm_as_fun` was `glorifying`, which
    # Jev read as glorifying God on worship songs.
    "outlook": ("Where does this part of the song stand between darkness and light? Darkness means real "
                "pain: heartbreak, grief, hopelessness, fear, danger, or harm. A crush, mild nerves, or gentle "
                "wistfulness is not darkness.", {
        "despair": "Stuck in real darkness, with no way out in sight.",
        "struggling": "In real pain (heartbreak, grief, despair, addiction, danger), but fighting it.",
        "turning": "The moment hope or change appears after darkness.",
        "overcome": "On the other side: healed, rescued, looking back at the dark.",
        "untroubled": "No real pain: happy, calm, in love, or only a crush, mild nerves, or gentle wistfulness.",
        "harm_as_fun": "Presents something harmful (drugs, violence, cruelty) as fun, cool, or deserved. "
                       "Praising or glorifying God is not this.",
        "none": "Unclear."}),
}

_NONE = "This theme is present but none of these fits."

# Broad theme -> the detail question(s) asked when it scores >= DETAIL_THRESHOLD.
DETAIL = {
    "love": {"detail_love": ("What kind of love story is this part of the song about?", {
        "new_attraction": "Meeting or falling for someone; the excitement of something starting.",
        "devotion": "Committed, lasting love; promising to stay.",
        "desire": "Physical attraction and wanting someone, focused on the body and the moment.",
        "unrequited": "Loving someone who does not love back, or who does not know.",
        "toxic_tie": "A relationship that hurts but cannot be left; on-and-off, addictive.",
        "breakup": "The end of a relationship as it happens: leaving, being left.",
        "longing_ex": "After the end: missing an ex, wanting them back, unable to move on.",
        "moving_on": "After the end: healing, letting go, being better off.",
        "betrayal": "Cheating, lies, being used by a partner.",
        "none": "Love is present but none of these fits."})},
    "self": {"detail_self": ("What about the singer's self is this part of the song about?", {
        "growing_up": "Youth, coming of age, changing from who one was.",
        "self_doubt": "Feeling not good enough, insecure, ashamed.",
        "self_assertion": "Confidence, pride, standing up for oneself.",
        "authenticity": "Staying true to oneself under pressure to change or conform.",
        "regret": "Wishing one had done things differently.",
        "fame_image": "Being watched and judged in public; the cost of fame on who one is.",
        "none": _NONE})},
    "struggle": {"detail_struggle": ("What kind of struggle is this part of the song about?", {
        "depression": "Emptiness, hopelessness, numbness, wanting to disappear or die.",
        "anxiety": "Worry, panic, overthinking, pressure.",
        "addiction": "Dependence on a substance or habit.",
        "loneliness": "Isolation, having no one.",
        "trauma": "Lasting scars from abuse, violence, or a painful past.",
        "recovery": "Fighting back, getting better, asking for help.",
        "none": _NONE})},
    "faith": {
        "detail_faith_subject": ("What does this part of the song say about God?", {
            "cross_salvation": "The cross, Jesus' death and blood, being saved.",
            "grace_forgiveness": "Mercy, forgiveness, being made new.",
            "faithfulness": "God's goodness and promises kept through every season.",
            "power_victory": "God's strength, battles won, chains broken, the enemy defeated.",
            "presence_spirit": "The Holy Spirit, God being near, meeting God in this moment.",
            "majesty_creation": "God's greatness, holiness, and glory, shown in creation.",
            "name_of_jesus": "Who Jesus is: his names, titles, and nature.",
            "heaven_return": "Eternity, heaven, Christ's return.",
            "season_story": "Christmas, Easter, or retelling a Bible story or figure.",
            "none": _NONE}),
        "detail_faith_posture": ("How does the singer come to God in this part of the song?", {
            "adoration": "Worshipping God for who God is.",
            "thanksgiving": "Thanking God for what God has done.",
            "surrender": "Giving over one's life, will, or burdens; trust.",
            "longing": "Hunger for God, intimacy, wanting more of God.",
            "lament_doubt": "Pain, questions, waiting, feeling God is far away.",
            "petition": "Asking God for help, healing, rescue, or revival.",
            "declaration": "Proclaiming truth boldly, like a battle cry.",
            "testimony": "Telling what God did in the singer's own life.",
            "invitation": "Calling others to come, believe, or worship.",
            "none": _NONE})},
    "ambition": {"detail_ambition": ("What kind of ambition is this part of the song about?", {
        "hustle": "Working relentlessly, the grind, sacrifice for the goal.",
        "wealth_flex": "Money, luxury, and possessions shown off.",
        "fame_success": "Having made it: status, recognition, being on top.",
        "proving_doubters": "Showing up the people who doubted or dismissed the singer.",
        "underdog_rise": "Coming from nothing, the climb from the bottom.",
        "legacy": "Being remembered, greatness that outlasts the singer.",
        "none": _NONE})},
    "celebration": {"detail_celebration": ("What kind of celebration is this part of the song about?", {
        "party_night": "A night out, a club, a party in full swing.",
        "dancing": "Moving to the music, the dance floor itself.",
        "substances_fun": "Drinking or getting high as part of the fun.",
        "carefree_summer": "Easy, sunny, no-worries freedom.",
        "victory_lap": "Celebrating a win or an achievement.",
        "none": _NONE})},
    "kinship": {"detail_kinship": ("Which bond is this part of the song about?", {
        "parent": "The singer's mother or father.",
        "child": "The singer's own child; being a parent.",
        "sibling": "A brother or sister.",
        "friendship": "Friends, loyalty, the crew.",
        "hometown_roots": "Where one comes from: a hometown, heritage, roots.",
        "family_conflict": "Fighting, estrangement, or hurt within a family.",
        "none": _NONE})},
    "loss": {"detail_loss": ("What kind of loss is this part of the song about?", {
        "fresh_grief": "The raw pain of a recent death.",
        "remembrance": "Remembering someone gone, with time passed.",
        "tribute": "Honouring someone who died; speaking to them.",
        "own_mortality": "The singer's own death, time running out.",
        "none": _NONE})},
    "world": {"detail_world": ("What about the world is this part of the song about?", {
        "injustice": "Racism, unfairness, oppression.",
        "politics_power": "Governments, leaders, the powerful.",
        "war_violence": "War, shootings, violence in society.",
        "media_society": "Media, social media, consumerism, the culture.",
        "poverty_class": "Being poor, class, the struggle to get by.",
        "environment": "Nature under threat, the planet.",
        "none": _NONE})},
    "journey": {"detail_journey": ("What kind of journey is this part of the song about?", {
        "leaving_home": "Leaving where one is from.",
        "escape": "Running away from a situation or a life.",
        "freedom_road": "The open road, being free and on the move.",
        "homecoming": "Coming back home.",
        "searching": "Looking for meaning, a place, or oneself.",
        "none": _NONE})},
    "conflict": {"detail_conflict": ("What kind of conflict is this part of the song about?", {
        "diss_battle": "Insulting a rival, rap battles, calling someone out.",
        "revenge": "Getting even with someone who wronged the singer.",
        "broken_friendship": "A friend who turned into an enemy.",
        "threat_violence": "Threatening, fighting, or hurting someone; crime.",
        "rebellion": "Defying authority, parents, or the system.",
        "none": _NONE})},
}

SEVERITY_LEVELS = ("absent", "mention", "theme", "graphic")
SEVERITY_VALUE = {"absent": 0.0, "mention": 1 / 3, "theme": 2 / 3, "graphic": 1.0}


def _severity(thing: str) -> tuple[str, dict[str, str]]:
    cap = thing[0].upper() + thing[1:]
    # "Literal depictions only": pilot v3a read Bad Blood's "bullet holes" as
    # violence 0.67.
    return (f"How much do the lyrics of this song depict {thing}? Judge only what the words show, "
            f"not whether the song approves of it. Count only literal depictions: a metaphor "
            f"(\"bullet holes\" for hurt feelings) is at most a mention.", {
        "absent": f"The lyrics never depict {thing}.",
        "mention": f"{cap} appears in a passing line but is not what the song is about.",
        "theme": f"{cap} is a real subject of the song.",
        "graphic": f"{cap} is described vividly and in detail, at the centre of the song."})


SEVERITY = {
    "sev_drugs_alcohol": _severity("drug use or getting drunk or high"),
    "sev_self_harm_suicide": _severity("self-harm, wanting to die, or suicide"),
    "sev_violence": _severity("hurting or killing people, weapons, or threats"),
    "sev_abuse": _severity("abuse by a partner or parent, or sexual abuse"),
    "sev_sexual": _severity("explicit sexual content"),
    "sev_death": _severity("someone dying or being dead"),
}

SONG_GROUPS = {
    # Asked once on the whole lyric: per segment, setting flipped between
    # `none` and a place, which measured imagery rather than story.
    "setting": ("Which place or scene do the words of this song explicitly name or describe? "
                "Answer none unless the lyrics themselves mention it; do not infer a place from the mood.", {
        "night": "The lyrics mention night, darkness, late hours, or the moon.",
        "city": "The lyrics mention streets, cars, buildings, or urban life.",
        "nature": "The lyrics mention sky, sea, weather, seasons, desert, or landscapes.",
        "road": "The lyrics mention driving, travelling, trains, or being on the way somewhere.",
        "party": "The lyrics mention a club, dance floor, drinks, or a crowd partying.",
        "home": "The lyrics mention rooms, a bed, a house, or a hometown.",
        "none": "The lyrics name no place and describe no scene."}),
    "mode": ("How is this song told?", {
        "story": "A plot with characters or events.",
        "confession": "The singer opening up about themselves.",
        "letter": "Spoken to one person, like a message.",
        "anthem": "A rallying call for everyone to join.",
        "boast": "Bragging, showing off.",
        "rant": "An attack or complaint aimed at someone.",
        "satire": "Humour, parody, shock for laughs.",
        "prayer": "Sung to God.",
        "none": "None of these fits."}),
    **SEVERITY,
}

# --- segmentation -------------------------------------------------------------

MIN_HOOK_LINES = 4   # a shorter hook gave unreliable answers; merged into the body
MIN_HOOK_WORDS = 3   # distinct words; shorter repeated lines are chants or tags
MIN_BODY_LINES = 12  # below this, one `body` segment instead of thirds
CHANT_SHARE = 0.75

_ADLIB_RE = re.compile(r"\([^)]*\)")
_HEADER_RE = re.compile(r"^\[.*\]$")
# Sung sounds with no words, stretched spellings included.
_VOCABLE_RE = re.compile(
    r"^(o+h*|a+h+|a+|l+a+|n+a+|d+a+|y+e+a+h*|y+e+h*|h+e+y+|w*h+o+a+h*|w+o+a+h*|m+|"
    r"u+h+|u+m+|h+a+|a+y+|e+h+|y+o+|h+m+)$")

Line = tuple[int | None, str]


# Kana and Han are written without spaces between words.
_UNSPACED_RE = re.compile(r"[぀-ヿㇰ-ㇿ㐀-䶿一-鿿ｦ-ﾝ]")


def key(line: str) -> str:
    """Repeat-detection key: ad-libs in parentheses removed, case and
    punctuation folded, so "my album (my album)" matches "my album". Any
    script's letters count, so a Hangul or kana line has a key."""
    return re.sub(r"[^\w ]|_", "", _ADLIB_RE.sub("", line).lower()).strip()


def _words(k: str) -> set[str]:
    """Distinct words of a key, for MIN_HOOK_WORDS. A token holding kana or
    Han counts as one word per two characters, a rough Japanese word
    length, since nothing in the text marks where its words end."""
    words: set[str] = set()
    for token in k.split():
        if _UNSPACED_RE.search(token):
            words.update(token[i:i + 2] for i in range(0, len(token), 2))
        else:
            words.add(token)
    return words


def is_chant(line: str) -> bool:
    """Mostly sung sounds once ad-libs are removed: "Oh-oh-oh (by the
    bonfire)" is a chant; "And we all sing, oh-oh" is not."""
    words = re.findall(r"[^\W\d_]+", _ADLIB_RE.sub("", line).lower())
    if not words:
        return True
    return sum(bool(_VOCABLE_RE.match(w)) for w in words) / len(words) >= CHANT_SHARE


def clean(raw: list[Line]) -> list[Line]:
    """Drop blank lines, section headers, symbol-only and ad-lib-only lines,
    and chants."""
    return [(ms, t.strip()) for ms, t in raw
            if t.strip() and not _HEADER_RE.match(t.strip()) and key(t) and not is_chant(t)]


def lines_from_lrc(text: str) -> list[Line]:
    return clean([(line.ms, line.text) for line in parse_lrc(text)])


def lines_from_text(text: str) -> list[Line]:
    return clean([(None, line) for line in text.splitlines()])


def text_hash(lines: list[Line]) -> str:
    """What the segmenter saw -- the same digest the 2026-10-12 prototype run
    wrote as `lyrics_hash`, so its files can be checked against a track's
    current lyrics before import (spec D2, D6)."""
    return hashlib.sha256("\n".join(t for _, t in lines).encode()).hexdigest()


def segments(lines: list[Line]) -> dict[str, list[Line]]:
    """hook = lines repeated 2+ times (by `key`), each kept once; merged
    back into the body when shorter than MIN_HOOK_LINES. The body is split
    into thirds of the *sung span* when every line is timed, so a long
    intro or outro does not shift them; else thirds by line count. Empty
    segments are dropped -- a long instrumental break can leave `middle`
    empty."""
    counts: dict[str, int] = {}
    for _, t in lines:
        counts[key(t)] = counts.get(key(t), 0) + 1
    lines = [(ms, t) for ms, t in lines
             if counts[key(t)] < 2 or len(_words(key(t))) >= MIN_HOOK_WORDS]
    hook, seen = [], set()
    for ms, t in lines:
        k = key(t)
        if counts[k] >= 2 and k not in seen:
            seen.add(k)
            hook.append((ms, t))
    if len(hook) < MIN_HOOK_LINES:
        hook, body, seen = [], [], set()
        for ms, t in lines:  # one copy of each line, in order
            if key(t) not in seen:
                seen.add(key(t))
                body.append((ms, t))
    else:
        body = [(ms, t) for ms, t in lines if counts[key(t)] < 2]

    segs: dict[str, list[Line]] = {}
    if hook:
        segs["hook"] = hook
    if len(body) < MIN_BODY_LINES:
        segs["body"] = body
    elif all(ms is not None for ms, _ in body):
        t0, t1 = lines[0][0], lines[-1][0]
        span = max(1, t1 - t0)
        for name, lo, hi in (("start", 0, 1 / 3), ("middle", 1 / 3, 2 / 3), ("end", 2 / 3, 1.01)):
            segs[name] = [(ms, t) for ms, t in body if lo <= (ms - t0) / span < hi]
    else:
        n = len(body)
        a, b = n // 3, 2 * n // 3
        segs.update(start=body[:a], middle=body[a:b], end=body[b:])
    return {k: v for k, v in segs.items() if v}

# --- asking -------------------------------------------------------------------


def tag_song(ask, song: str, lines: list[Line]) -> dict:
    """Ask every question for one song. `ask(state, groups)` returns
    `{group: {label: probability}}` for `groups` shaped like `GROUPS`; it is
    injected so this module stays pure (the batch passes a System One call,
    tests a fake). Raises ValueError when no lyric lines survive cleaning.
    """
    segs = segments(lines)
    if not segs:
        raise ValueError("no lyric lines left after cleaning")
    whole, seen = [], set()
    for _, t in lines:
        if key(t) not in seen:
            seen.add(key(t))
            whole.append(t)
    tags = {"song": ask({"song": song, "lyrics": "\n".join(whole)}, SONG_GROUPS)}
    for name, seg in segs.items():
        state = {"song": song, "segment": name, "lyrics": "\n".join(t for _, t in seg)}
        answers = dict(ask(state, GROUPS))
        detail = {g: q for broad, groups in DETAIL.items()
                  if answers["theme_broad"].get(broad, 0) >= DETAIL_THRESHOLD
                  for g, q in groups.items()}
        if detail:
            answers.update(ask(state, detail))
        tags[name] = answers
    return tags

# --- derived values (read time) -------------------------------------------------

OUTLOOK_LADDER = {"despair": 1.0, "struggling": 0.75, "turning": 0.5, "overcome": 0.25, "untroubled": 0.0}


def _outlook_score(p: dict[str, float]) -> float | None:
    """Expected ladder position, renormalized over the ladder labels only --
    `harm_as_fun` and `none` are off the ladder. None with no ladder mass."""
    mass = sum(p.get(k, 0.0) for k in OUTLOOK_LADDER)
    if mass <= 0:
        return None
    return sum(p.get(k, 0.0) * v for k, v in OUTLOOK_LADDER.items()) / mass


def scores(tags: dict) -> dict | None:
    """Three negative scores, kept separate (owner, 2026-10-12): one
    combined darkness scored a glorifying drug-party song and flag-free
    heartbreak as light.

      mood_darkness      how hopeless the song feels: the outlook ladder, mean over segments
      end_mood_darkness  the same for the last segment, for transitions
      heavy_content      the worst severity flag; all six are in `severity`
      glorifying         mean p(harm_as_fun): makes harmful things look fun or cool

    None for a `no_lines` set (no tags at all).
    """
    if not tags:
        return None
    song = tags.get("song", {})
    severity = {g: sum(song.get(g, {}).get(level, 0.0) * v for level, v in SEVERITY_VALUE.items())
                for g in SEVERITY}
    segs = [a for name, a in tags.items() if name != "song" and "outlook" in a]
    ladder = [s for s in (_outlook_score(a["outlook"]) for a in segs) if s is not None]
    end = tags.get("end") or tags.get("body")
    return {
        "mood_darkness": sum(ladder) / len(ladder) if ladder else 0.0,
        "end_mood_darkness": _outlook_score(end["outlook"]) if end and "outlook" in end else None,
        "heavy_content": max(severity.values()),
        "severity": severity,
        "glorifying": sum(a["outlook"].get("harm_as_fun", 0.0) for a in segs) / len(segs) if segs else 0.0,
    }


def tag_vector(tags: dict) -> dict[str, float]:
    """A flat `{"group:label": weight}` vector for similarity (piece 2).

    Per-segment groups are averaged with the hook weighted the same as the
    body segments together (taxonomy note); a group asked in only some
    segments (a detail question) contributes only there. Song-level groups
    enter at full weight.
    """
    body = [n for n in ("start", "middle", "end", "body") if n in tags]
    weights = {}
    if "hook" in tags and body:
        weights["hook"] = 0.5
        weights.update({n: 0.5 / len(body) for n in body})
    else:
        named = [n for n in tags if n != "song"]
        weights.update({n: 1 / len(named) for n in named})
    vec: dict[str, float] = {}
    for seg, w in weights.items():
        for grp, probs in tags[seg].items():
            for label, p in probs.items():
                vec[f"{grp}:{label}"] = vec.get(f"{grp}:{label}", 0.0) + w * p
    for grp, probs in tags.get("song", {}).items():
        for label, p in probs.items():
            vec[f"{grp}:{label}"] = p
    return vec
