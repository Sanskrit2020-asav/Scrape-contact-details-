"""Application guardrails — the layer between the model and publishing.

The model is capable and instructed, but it is not the last word. Everything
here runs *after* a decision comes back and can override it in one direction
only: toward more caution. Nothing in this module can turn an escalation into a
reply, raise a confidence score, or make a risky comment publishable.

Checks applied, in order:

1. **Safety override** (§17) — a comment about a current incident is escalated
   no matter what the model returned.
2. **Complaint/refund override** (§18) — never argued with, never answered.
3. **Invented specifics** (§16) — a reply containing a price, a date or a
   permit fee that is not in approved knowledge is withheld.
4. **Banned phrasing** (§11, §19) — corporate filler and hard-sell language.
5. **Length** (§12) — trimmed to sentence boundaries, not chopped mid-word.
6. **Repetition** (§14) — too similar to something we recently said.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..ai.schema import AgentDecision
from ..database.models import Action, RiskLevel
from ..observability import get_logger
from .variation import is_repetitive

log = get_logger(__name__)

# --- trigger vocabularies -------------------------------------------------

#: Current-incident language. Deliberately broad: a false escalation costs an
#: hour of a human's attention, a false reply about a landslide costs more.
SAFETY_TERMS: tuple[str, ...] = (
    "landslide", "flood", "flooding", "earthquake", "avalanche", "quake",
    "accident", "crash", "died", "death", "dead", "killed", "injured", "injury",
    "missing", "rescue", "stranded", "trapped", "evacuat", "emergency",
    "disaster", "closed", "closure", "blocked", "cancelled", "canceled",
    "grounded", "delayed", "strike", "curfew", "unrest", "protest", "bandh",
    "altitude sickness", "hape", "hace", "frostbite", "helicopter rescue",
)

#: "Right now" language. A safety word plus a currency word is what makes a
#: comment about a live situation rather than a general question.
CURRENCY_TERMS: tuple[str, ...] = (
    "right now", "currently", "today", "tonight", "at the moment", "this week",
    "these days", "still", "is it open", "any news", "update", "latest",
    "happening", "just now", "yesterday", "this morning", "safe to",
)

#: Questions about the state of things *now*. These carry their own currency —
#: "is the trail open?" is a question about today even without a disaster word —
#: so they escalate on their own. Spec §10 lists current trail, road and flight
#: conditions as escalate-only, and the cost of being wrong here is a trekker
#: walking into a closed pass.
CONDITION_TERMS: tuple[str, ...] = (
    "trail open", "trails open", "road open", "roads open", "pass open",
    "route open", "is it open", "are they open", "still open", "reopened",
    "trail condition", "trail conditions", "road condition", "road conditions",
    "route condition", "conditions like", "current condition",
    "flight status", "flights running", "flights operating", "flights flying",
    "helicopter running", "safe to trek", "safe to travel", "safe to visit",
    "safe to go", "safe right now", "safe at the moment", "is it safe now",
    "weather like", "weather right now", "weather at the moment",
    "how is the weather", "how's the weather", "hows the weather",
    "what is the weather", "what's the weather", "whats the weather",
)

#: Proximity patterns for the same idea, because the subject and the state
#: word are often separated: "are flights *to Lukla* running", "is the trail
#: *to ABC* open". A plain substring list cannot see those.
CONDITION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:flight|flights|helicopter|helicopters|chopper|heli)\b[^.?!]{0,60}?"
        r"\b(?:running|operating|flying|fly|open|cancel\w*|delay\w*|disrupt\w*|"
        r"available|status|grounded|schedule[ds]?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:trail|trails|road|roads|route|routes|pass|passes|airport|border|teahouse[s]?|lodge[s]?)\b"
        r"[^.?!]{0,60}?\b(?:open|opened|closed|clear|blocked|passable|accessible|"
        r"condition[s]?|damaged|washed|running|available)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:is|are|any)\b[^.?!]{0,40}?\b(?:safe|dangerous|risky)\b[^.?!]{0,40}?"
        r"\b(?:now|today|currently|right now|at the moment|this week|this month)\b",
        re.IGNORECASE,
    ),
)

#: Subjects that are plainly not ours. Kept deliberately narrow: the test for
#: "off topic" is that a comment is actively about something else, NOT that it
#: fails to mention a mountain. "Beautiful!" and "😍" are genuine engagement
#: with the post and must still get a reply, so no keyword whitelist is used —
#: only this blacklist of things we stay out of.
OFF_TOPIC_TERMS: tuple[str, ...] = (
    "vote for", "election", "elected", "political party", "prime minister",
    "parliament", "candidate", "campaign rally",
    "bitcoin", "crypto", "forex", "trading signal", "investment opportunity",
    "binary option", "casino", "betting", "lottery", "loan offer",
    "sell you", "buy my", "my shop", "my page", "check my profile",
    "follow back", "follow me", "sub4sub", "dm for promo",
    "make money", "work from home", "earn daily",
)

COMPLAINT_TERMS: tuple[str, ...] = (
    "scam", "scammed", "fraud", "cheated", "ripped off", "rip off", "refund",
    "money back", "compensation", "lawyer", "legal action", "sue", "court",
    "police", "complaint", "terrible", "worst", "liar", "lied", "stole",
    "stolen", "never again", "avoid this company", "unprofessional",
)

#: Phrases that make a reply sound like a corporate bot or a hard sell.
BANNED_PHRASES: tuple[str, ...] = (
    "thank you for reaching out", "we appreciate your interest",
    "please be advised", "kindly note", "rest assured", "we regret to inform",
    "as an ai", "i am an ai", "as a language model", "book now",
    "limited offer", "limited time", "don't miss out", "dont miss out",
    "act now", "hurry", "special discount", "best price guaranteed",
    "unforgettable journey of a lifetime", "adventure awaits",
    "we look forward to hearing from you", "feel free to reach out to us at",
)

_MONEY_RE = re.compile(
    r"(?:(?:us\$|usd|npr|rs\.?|nrs|€|£|\$)\s?\d[\d,.]*)"
    r"|(?:\b\d[\d,.]*\s?(?:usd|dollars?|euros?|rupees?|npr|rs\b))",
    re.IGNORECASE,
)
_DATE_RE = re.compile(
    r"\b(?:on|from|starting|departing|departs?|available)\s+"
    r"(?:\d{1,2}(?:st|nd|rd|th)?\s+)?"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
    r"(?:\s+\d{1,2}(?:st|nd|rd|th)?)?(?:,?\s*\d{4})?\b",
    re.IGNORECASE,
)
#: A bare year, the commonest way a wrong historical claim gets stated.
_YEAR_RE = re.compile(r"\b(1[89]\d{2}|20[0-2]\d)\b")

#: An altitude presented as fact.
_ALTITUDE_RE = re.compile(r"\b\d{1,2}[,.]?\d{3}\s?(?:m|metres|meters|ft|feet)\b", re.IGNORECASE)

#: Statistics that move. A summit count or death toll that was right two years
#: ago is wrong now, so these are escalated no matter what the model believes.
MOVING_STATISTIC_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:how many|number of|total)\b[^.?!]{0,50}?"
        r"\b(?:people|climbers|summit\w*|died|deaths?|fatalit\w+|attempts?)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:summit\w*|climbed|died|deaths?|fatalit\w+)\b[^.?!]{0,30}?"
        r"\b(?:how many|this year|per year|each year|so far|to date|in total)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:death rate|fatality rate|success rate|permit fee|royalty)\b", re.IGNORECASE),
)

_SENTENCE_RE = re.compile(r"(?<=[.!?…])\s+")
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF❤️]+"
)

MAX_SENTENCES = 3
MAX_EMOJI = 2


@dataclass
class GuardrailResult:
    """The decision after guardrails, plus what was changed and why."""

    decision: AgentDecision
    modified: bool = False
    blocked: bool = False
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add_violation(self, note: str) -> None:
        self.violations.append(note)


def _contains(text: str, terms: tuple[str, ...]) -> str | None:
    lowered = f" {text.lower()} "
    for term in terms:
        if term in lowered:
            return term
    return None


def mentions_current_incident(text: str) -> str | None:
    """Does this comment ask about a live situation?

    "How dangerous is altitude sickness?" is a general question and can be
    answered. "Is the trail closed right now?", "is the trail open?" and "there
    was an avalanche" are not, and are escalated.

    Three ways to trigger: an unambiguous incident word on its own, a
    conditions question on its own, or an ambiguous safety word paired with
    present-tense framing.
    """
    condition_hit = _contains(text, CONDITION_TERMS)
    if condition_hit:
        return condition_hit

    for pattern in CONDITION_PATTERNS:
        match = pattern.search(text or "")
        if match:
            return match.group(0).strip()[:60]

    safety_hit = _contains(text, SAFETY_TERMS)
    if not safety_hit:
        return None
    unambiguous = {
        "landslide", "flood", "flooding", "earthquake", "avalanche", "quake",
        "missing", "rescue", "stranded", "trapped", "evacuat", "disaster",
        "died", "death", "dead", "killed", "helicopter rescue",
    }
    if safety_hit in unambiguous:
        return safety_hit
    currency_hit = _contains(text, CURRENCY_TERMS)
    return f"{safety_hit}+{currency_hit}" if currency_hit else None


def trim_to_sentences(text: str, max_sentences: int = MAX_SENTENCES) -> str:
    parts = [p.strip() for p in _SENTENCE_RE.split(text.strip()) if p.strip()]
    return " ".join(parts[:max_sentences]) if len(parts) > max_sentences else text.strip()


def trim_to_length(text: str, max_chars: int) -> str:
    """Shorten to ``max_chars`` on a sentence, then word, boundary."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    parts = [p.strip() for p in _SENTENCE_RE.split(text) if p.strip()]
    built = ""
    for part in parts:
        candidate = f"{built} {part}".strip()
        if len(candidate) > max_chars:
            break
        built = candidate
    if built:
        return built
    clipped = text[:max_chars].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return clipped or text[:max_chars]


def _strip_extra_emoji(text: str, limit: int = MAX_EMOJI) -> str:
    """Keep the first ``limit`` emoji, drop the rest. Emoji spam reads as a bot."""
    seen = 0
    def replace(match: re.Match) -> str:
        nonlocal seen
        if seen >= limit:
            return ""
        seen += 1
        return match.group(0)
    return re.sub(r"\s{2,}", " ", _EMOJI_RE.sub(replace, text)).strip()


def _escalate(
    decision: AgentDecision, reason: str, *, risk: str = RiskLevel.HIGH.value
) -> AgentDecision:
    return AgentDecision(
        action=Action.ESCALATE.value,
        intent=decision.intent,
        confidence=decision.confidence,
        reply=None,
        needs_human=True,
        reason=f"{reason} (guardrail override; model said: {decision.reason or decision.action})",
        risk_level=risk,
        raw=decision.raw,
    )


def apply_guardrails(
    decision: AgentDecision,
    *,
    comment_text: str,
    previous_replies: list[str] | None = None,
    max_reply_length: int = 240,
    approved_knowledge_text: str = "",
) -> GuardrailResult:
    """Run every guardrail. Can only make the outcome more conservative."""
    result = GuardrailResult(decision=decision)

    # 1. Safety beats engagement, always.
    incident = mentions_current_incident(comment_text)
    if incident:
        result.add_violation(f"current-incident language: {incident}")
        if decision.action != Action.ESCALATE.value or decision.risk_level != RiskLevel.HIGH.value:
            result.decision = _escalate(decision, f"Safety topic detected ({incident})")
            result.modified = True
            log.warning(
                "safety guardrail escalated a comment",
                extra={"trigger": incident, "model_action": decision.action},
            )
        return result

    # 2. Complaints and refunds are never answered by the agent.
    complaint = _contains(comment_text, COMPLAINT_TERMS)
    if complaint:
        result.add_violation(f"complaint/refund language: {complaint}")
        if decision.action != Action.ESCALATE.value:
            result.decision = _escalate(decision, f"Complaint or refund topic ({complaint})")
            result.modified = True
            log.warning(
                "complaint guardrail escalated a comment",
                extra={"trigger": complaint, "model_action": decision.action},
            )
        return result

    # 2a. Off our subject. Ours is the mountains, climbing and its history,
    # trekking, and travel in Nepal, Bhutan and Tibet. Politics, crypto and
    # other people's businesses are not, however politely they are raised.
    off_topic = _contains(comment_text, OFF_TOPIC_TERMS)
    if off_topic:
        result.add_violation(f"off our subject: {off_topic!r}")
        if decision.action != Action.IGNORE.value:
            result.decision = AgentDecision(
                action=Action.IGNORE.value,
                intent="irrelevant" if decision.intent != "spam" else "spam",
                confidence=decision.confidence,
                reply=None,
                needs_human=False,
                reason=f"Not about our subject ({off_topic}); staying out of it",
                risk_level=RiskLevel.LOW.value,
                raw=decision.raw,
            )
            result.modified = True
            log.info("guardrail ignored an off-topic comment", extra={"trigger": off_topic})
        return result

    # 2b. Questions asking for a statistic that changes — summit counts, death
    # tolls, permit fees. There is a real answer, but it moves, and a stale
    # number is worse than no answer.
    for pattern in MOVING_STATISTIC_PATTERNS:
        match = pattern.search(comment_text or "")
        if match:
            result.add_violation(f"asks for a moving statistic: {match.group(0).strip()[:50]!r}")
            if decision.action != Action.ESCALATE.value:
                result.decision = _escalate(
                    decision, "Asks for a statistic that changes over time",
                    risk=RiskLevel.MEDIUM.value,
                )
                result.modified = True
                log.info("guardrail escalated a moving-statistic question")
            return result

    # Nothing below concerns a decision with no reply text.
    if decision.action != Action.REPLY.value or not decision.reply:
        return result

    reply = decision.reply.strip()
    knowledge = approved_knowledge_text.lower()

    # 3. Specifics the model may not invent.
    money = _MONEY_RE.search(reply)
    if money and money.group(0).lower() not in knowledge:
        result.add_violation(f"unapproved figure in reply: {money.group(0)!r}")
        result.decision = _escalate(
            decision, "Reply contained a price or figure not present in approved knowledge",
            risk=RiskLevel.MEDIUM.value,
        )
        result.modified = True
        result.blocked = True
        log.warning("guardrail blocked an invented figure", extra={"matched": money.group(0)})
        return result

    date = _DATE_RE.search(reply)
    if date and date.group(0).lower() not in knowledge:
        result.add_violation(f"unapproved date in reply: {date.group(0)!r}")
        result.decision = _escalate(
            decision, "Reply stated a departure date not present in approved knowledge",
            risk=RiskLevel.MEDIUM.value,
        )
        result.modified = True
        result.blocked = True
        return result

    # 3b. Historical claims. A wrong first-ascent year under the company's name
    # is the error a climber screenshots, so a year or altitude in the reply must
    # be traceable to approved knowledge.
    # A year the commenter themselves used is fair to echo back — "2019 was a
    # good year up there" is conversation, not a historical claim. Only years
    # the agent introduces on its own need to be traceable to knowledge.
    year = next(
        (m for m in _YEAR_RE.finditer(reply)
         if m.group(0) not in approved_knowledge_text and m.group(0) not in (comment_text or "")),
        None,
    )
    if year:
        result.add_violation(f"unapproved year in reply: {year.group(0)!r}")
        result.decision = _escalate(
            decision, f"Reply stated the year {year.group(0)} which is not in approved knowledge",
            risk=RiskLevel.MEDIUM.value,
        )
        result.modified = True
        result.blocked = True
        log.warning("guardrail blocked an unapproved date", extra={"matched": year.group(0)})
        return result

    altitude = _ALTITUDE_RE.search(reply)
    if altitude and altitude.group(0).lower() not in knowledge \
            and altitude.group(0).lower() not in (comment_text or "").lower():
        # Compare digits only: "8,848.86m" in knowledge should cover "8848m".
        digits = re.sub(r"\D", "", altitude.group(0))
        if digits and digits[:4] not in re.sub(r"[^\d]", "", knowledge):
            result.add_violation(f"unapproved altitude in reply: {altitude.group(0)!r}")
            result.decision = _escalate(
                decision, f"Reply stated an altitude ({altitude.group(0)}) not in approved knowledge",
                risk=RiskLevel.MEDIUM.value,
            )
            result.modified = True
            result.blocked = True
            return result

    # 4. Voice.
    banned = _contains(reply, BANNED_PHRASES)
    if banned:
        result.add_violation(f"banned phrase: {banned!r}")
        result.decision = AgentDecision(
            action=decision.action, intent=decision.intent,
            confidence=min(decision.confidence, 0.5), reply=reply,
            needs_human=True,
            reason=f"{decision.reason} | banned phrase {banned!r} — needs a human",
            risk_level=decision.risk_level, raw=decision.raw,
        )
        result.modified = True
        log.warning("guardrail flagged brand-voice violation", extra={"phrase": banned})
        return result

    # 5. Shape: sentences, emoji, length.
    shaped = _strip_extra_emoji(trim_to_sentences(reply))
    shaped = trim_to_length(shaped, max_reply_length)
    if shaped != reply:
        result.notes.append("reply shortened to comment length")
        result.modified = True

    if not shaped:
        result.decision = _escalate(decision, "Reply was empty after guardrails", risk=RiskLevel.MEDIUM.value)
        result.blocked = True
        result.modified = True
        return result

    # 6. Don't say the same thing twice.
    repetitive, similar_to = is_repetitive(shaped, previous_replies or [])
    if repetitive:
        result.add_violation(f"too similar to a recent reply: {similar_to!r}")
        result.decision = AgentDecision(
            action=decision.action, intent=decision.intent,
            confidence=min(decision.confidence, 0.6), reply=shaped,
            needs_human=True,
            reason=f"{decision.reason} | repeats a recent reply — needs a human",
            risk_level=decision.risk_level, raw=decision.raw,
        )
        result.modified = True
        log.info("guardrail flagged a repetitive reply", extra={"similar_to": similar_to[:60]})
        return result

    if shaped != decision.reply:
        result.decision = AgentDecision(
            action=decision.action, intent=decision.intent, confidence=decision.confidence,
            reply=shaped, needs_human=decision.needs_human, reason=decision.reason,
            risk_level=decision.risk_level, raw=decision.raw,
        )
    return result
