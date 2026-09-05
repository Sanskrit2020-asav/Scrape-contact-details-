"""Starter knowledge base.

Deliberately contains **no prices, no dates and no permit fees**. Those are the
things that go stale and that the agent must never state from memory, so the
company adds them through the dashboard once they are current and approved.

What is here is durable, non-numeric context: rough trek difficulty, the usual
seasons, and the standing policy of moving money questions into a DM.
"""
from __future__ import annotations

SEED_ITEMS: list[dict] = [
    {
        "title": "How we handle price questions in comments",
        "category": "policy",
        "content": (
            "We never quote prices in a public comment. Costs depend on group size, "
            "season, hotel category and how much is included, so a number in a comment "
            "is misleading. Invite the person to message us and a human will send "
            "current details."
        ),
    },
    {
        "title": "How we handle availability and dates",
        "category": "policy",
        "content": (
            "Departure dates, availability and helicopter or flight slots change daily "
            "and are never confirmed in a comment. Move these to a direct message."
        ),
    },
    {
        "title": "Everest Base Camp — general character",
        "category": "difficulty",
        "content": (
            "A long walk rather than a technical climb. No climbing skill needed, but it "
            "spends many days at high altitude and the acclimatisation schedule matters "
            "more than fitness. Suitable for reasonably fit walkers who have prepared. "
            "Do not state exact day counts or altitudes as ours without approved detail."
        ),
    },
    {
        "title": "Annapurna Base Camp — general character",
        "category": "difficulty",
        "content": (
            "One of the more approachable Himalayan treks: lower than Everest Base Camp, "
            "with teahouses along the way and a lot of stone steps. Good first Himalayan "
            "trek for a fit walker."
        ),
    },
    {
        "title": "Manaslu Circuit — general character",
        "category": "difficulty",
        "content": (
            "A challenging trek: remote, quieter than Annapurna or Everest, and crossing "
            "a high pass. Very achievable with good preparation and proper "
            "acclimatisation, but not the right first Himalayan trek for most people. "
            "It is a restricted area, so the permit situation is not something to explain "
            "in a comment — move it to a message."
        ),
    },
    {
        "title": "Langtang Valley — general character",
        "category": "difficulty",
        "content": (
            "Shorter and closer to Kathmandu than the Everest or Annapurna regions, with "
            "a gentler profile. A good option for trekkers with less time."
        ),
    },
    {
        "title": "Trekking seasons in Nepal",
        "category": "seasonal",
        "content": (
            "Autumn (roughly late September to November) and spring (roughly March to "
            "May) are the main trekking seasons — clearest skies and most stable weather. "
            "The summer monsoon brings rain, cloud and leeches at lower elevations. Winter "
            "is cold and high passes can be blocked. Speak in general terms only; never "
            "state current conditions."
        ),
    },
    {
        "title": "Permits — what we say publicly",
        "category": "permits",
        "content": (
            "Most treks need permits, and restricted areas such as Manaslu and Upper "
            "Mustang have extra requirements including trekking with a licensed guide. "
            "Rules and fees change, so never state a fee, a rule or a number in a "
            "comment. Say we will confirm the current requirements by message."
        ),
    },
    {
        "title": "Altitude and safety — general advice",
        "category": "safety",
        "content": (
            "Altitude is the main risk on high Himalayan treks. Ascend gradually, take "
            "acclimatisation days, drink plenty, and descend if symptoms appear. This is "
            "general advice only — never assess an individual's fitness or a medical "
            "situation in a comment, and never comment on a current incident."
        ),
    },
    {
        "title": "Current trail, road and flight conditions",
        "category": "safety",
        "content": (
            "We never state current trail, road, flight or weather conditions in a "
            "comment. These change by the hour and a wrong answer is dangerous. Always "
            "escalate to a human."
        ),
    },
    {
        "title": "Who we are",
        "category": "general",
        "content": (
            "North Nepal Travel & Trek is a Nepal-based trekking and travel company "
            "arranging treks, tours, expeditions and helicopter tours across Nepal, "
            "Bhutan and Tibet."
        ),
    },
    {
        "title": "Internal — escalation contacts",
        "category": "policy",
        "human_only": True,
        "content": (
            "INTERNAL ONLY. Safety incidents and refund threats go straight to the "
            "operations manager. Never include internal routing in a public reply."
        ),
    },
]
