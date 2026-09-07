"""Approved mountaineering history and peak facts.

The page posts climbing history and mountain stories, so comments ask things
like "who climbed it first?", "what year was that?" and "which peak is this?".
Those have real answers — but a model reciting a date from memory will
eventually get one wrong, and a wrong first-ascent date under the company's
name is an embarrassment that a customer will screenshot.

So history is treated exactly like pricing: the agent may only state what is
written here. The guardrail in :mod:`social_agent.agent.guardrails` blocks any
reply containing a year or a first-ascent claim that is not in approved
knowledge.

**What is deliberately NOT here**, because it changes and would go stale:

* record summit counts (Kami Rita Sherpa's total climbs, most summits by a
  woman, youngest/oldest summiteer)
* how many people have summited a peak, or died on it
* current permit fees and royalty rates
* this season's expedition numbers

If a comment asks for one of those, the right answer is to escalate, not to
recite a number that was true two years ago.

Sources are the standard published mountaineering record. Verify anything here
against the Himalayan Database or the relevant national alpine club before
relying on it commercially, and correct it in the dashboard if it is wrong —
these are seeds for a knowledge base the company owns, not the last word.
"""
from __future__ import annotations

#: Well-established first ascents. Kept to the ones that are not disputed.
HISTORY_ITEMS: list[dict] = [
    {
        "title": "Everest — first ascent",
        "category": "history",
        "content": (
            "Everest was first summited on 29 May 1953 by Edmund Hillary of New "
            "Zealand and Tenzing Norgay Sherpa, as part of the British expedition "
            "led by John Hunt. They climbed by the South Col route from the Nepal "
            "side. Tenzing had already reached a very high point on the 1952 Swiss "
            "attempt, which is part of why the 1953 climb succeeded."
        ),
    },
    {
        "title": "Everest — height",
        "category": "peaks",
        "content": (
            "Everest stands at 8,848.86m, the figure jointly announced by Nepal and "
            "China in December 2020 after both countries resurveyed it. Older "
            "sources say 8,848m and some say 8,850m; the 2020 joint figure is the "
            "one to use. Sagarmatha in Nepali, Chomolungma in Tibetan."
        ),
    },
    {
        "title": "Everest — first ascent without supplemental oxygen",
        "category": "history",
        "content": (
            "Reinhold Messner and Peter Habeler made the first ascent of Everest "
            "without supplemental oxygen on 8 May 1978 — many doctors at the time "
            "believed it was physiologically impossible. Messner returned in August "
            "1980 to make the first solo ascent, also without oxygen, from the "
            "Tibetan side during the monsoon."
        ),
    },
    {
        "title": "Everest — first woman to summit",
        "category": "history",
        "content": (
            "Junko Tabei of Japan became the first woman to summit Everest on 16 May "
            "1975, twelve days after her team was hit by an avalanche at Camp II "
            "that briefly buried her. Pasang Lhamu Sherpa became the first Nepali "
            "woman to summit, in April 1993."
        ),
    },
    {
        "title": "Annapurna I — the first 8,000m peak ever climbed",
        "category": "history",
        "content": (
            "Annapurna I (8,091m) was the first 8,000m peak ever climbed, summited "
            "on 3 June 1950 by Maurice Herzog and Louis Lachenal of the French "
            "expedition — three years before Everest. The descent was brutal and "
            "both men lost fingers and toes to frostbite. Annapurna still carries "
            "one of the highest fatality rates of the 8,000ers, which is worth "
            "remembering when people confuse it with the Annapurna Base Camp trek: "
            "the trek is a walk, the mountain is another thing entirely."
        ),
    },
    {
        "title": "Manaslu — first ascent",
        "category": "history",
        "content": (
            "Manaslu (8,163m) was first climbed on 9 May 1956 by Toshio Imanishi of "
            "Japan and Gyalzen Norbu Sherpa. It is often called a Japanese mountain "
            "for the number of Japanese expeditions in its early history. The name "
            "comes from the Sanskrit for 'mountain of the spirit'."
        ),
    },
    {
        "title": "Kanchenjunga — first ascent and the summit tradition",
        "category": "history",
        "content": (
            "Kanchenjunga (8,586m), the world's third highest, was first climbed on "
            "25 May 1955 by George Band and Joe Brown of the British expedition. "
            "They stopped a few metres short of the true summit, honouring a promise "
            "to the Chogyal of Sikkim that the top would remain untrodden — a "
            "tradition many later climbers have kept."
        ),
    },
    {
        "title": "Lhotse, Makalu, Cho Oyu, Dhaulagiri — first ascents",
        "category": "history",
        "content": (
            "Cho Oyu (8,188m): 19 October 1954, Austrian expedition — Herbert "
            "Tichy, Joseph Jöchler and Pasang Dawa Lama. "
            "Makalu (8,485m): 15 May 1955, French expedition led by Jean Franco. "
            "Lhotse (8,516m): 18 May 1956, Fritz Luchsinger and Ernst Reiss of "
            "Switzerland. "
            "Dhaulagiri I (8,167m): 13 May 1960, a Swiss-Austrian expedition."
        ),
    },
    {
        "title": "The eight-thousanders in Nepal",
        "category": "peaks",
        "content": (
            "Eight of the world's fourteen 8,000m peaks lie wholly or partly in "
            "Nepal: Everest (8,848.86m), Kanchenjunga (8,586m), Lhotse (8,516m), "
            "Makalu (8,485m), Cho Oyu (8,188m), Dhaulagiri I (8,167m), Manaslu "
            "(8,163m) and Annapurna I (8,091m). K2 is NOT in Nepal — it is in the "
            "Karakoram on the Pakistan-China border, and people mix this up "
            "constantly in comments."
        ),
    },
    {
        "title": "Machhapuchhre — the mountain nobody climbs",
        "category": "peaks",
        "content": (
            "Machhapuchhre (6,993m), the 'Fishtail', is sacred to the local people "
            "and closed to climbing. The only serious attempt, a 1957 British "
            "expedition led by Jimmy Roberts, stopped around 50m below the summit by "
            "agreement. It is the peak in most Annapurna Base Camp photographs, and "
            "the one people most often ask us to identify."
        ),
    },
    {
        "title": "Ama Dablam — the Everest region's most photographed peak",
        "category": "peaks",
        "content": (
            "Ama Dablam (6,812m) is the striking pyramid that dominates the trail "
            "between Namche and Dingboche, and the peak most often mistaken for "
            "Everest in photos. The name means 'mother's necklace'. First climbed in "
            "1961 by Mike Gill, Barry Bishop, Mike Ward and Wally Romanes."
        ),
    },
    {
        "title": "Sherpa climbers — how to talk about them",
        "category": "history",
        "content": (
            "Sherpa is an ethnic group from the Everest region, not a job title. "
            "Always name individual climbers rather than saying 'the Sherpas' — "
            "Tenzing Norgay, Gyalzen Norbu, Pasang Dawa Lama, Pasang Lhamu Sherpa. "
            "Nepali climbers were on almost every one of these first ascents and "
            "were often left out of the telling at the time; we do not repeat that "
            "in our replies."
        ),
    },
    {
        "title": "Records and statistics — never state these",
        "category": "history",
        "human_only": True,
        "content": (
            "INTERNAL. Never state in a public comment: the number of times anyone "
            "has summited a peak, total summit counts, death tolls or fatality "
            "numbers, how many climb in a season, or current permit and royalty "
            "fees. All of these change and a stale number is worse than no answer. "
            "Escalate instead."
        ),
    },
    {
        "title": "Uncertain history — how to handle it",
        "category": "history",
        "content": (
            "Some questions have no settled answer — most famously whether George "
            "Mallory and Andrew Irvine reached the summit before dying on Everest in "
            "June 1924. Mallory's body was found in 1999; the question is still "
            "open. Where history is genuinely disputed, say so plainly rather than "
            "picking a side. 'Nobody knows for certain' is a good answer and an "
            "honest one."
        ),
    },
]
