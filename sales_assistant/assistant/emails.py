"""AI email writer.

Generates natural, human-sounding emails in eight styles. When the LLM is
available it writes the prose grounded on the quotation and retrieved knowledge;
otherwise it falls back to warm, personalised templates so the platform always
produces a usable draft.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..config import Settings, get_settings
from ..llm.client import LLMClient
from ..models import Estimate
from .quotation import Quotation, build_quotation


class EmailStyle(str, Enum):
    FIRST_INQUIRY = "first_inquiry"
    QUOTATION = "quotation"
    FOLLOW_UP = "follow_up"
    REMINDER = "reminder"
    BOOKING_CONFIRMATION = "booking_confirmation"
    PAYMENT_REMINDER = "payment_reminder"
    THANK_YOU = "thank_you"
    POST_TRIP = "post_trip"


_STYLE_BRIEF = {
    EmailStyle.FIRST_INQUIRY: "Warm reply to a first enquiry. Build rapport, show expertise, ask the missing questions, and invite a quick call.",
    EmailStyle.QUOTATION: "Send the detailed quotation. Lead with excitement for the trip, give the price clearly, summarise what's included/excluded, and add 2–3 genuinely useful tips.",
    EmailStyle.FOLLOW_UP: "Gentle follow-up after a quotation with no reply. Be helpful, not pushy; offer to adjust the plan or answer questions.",
    EmailStyle.REMINDER: "Friendly reminder about a pending decision or upcoming season/permit deadline.",
    EmailStyle.BOOKING_CONFIRMATION: "Confirm a booking. Reassure, list next steps, and what to prepare.",
    EmailStyle.PAYMENT_REMINDER: "Polite payment reminder. Restate the amount and method, keep the tone respectful and easy.",
    EmailStyle.THANK_YOU: "Thank the customer for booking/paying and set expectations for what happens next.",
    EmailStyle.POST_TRIP: "After the trip: thank them, ask for feedback/a review, and invite them back for a future adventure.",
}

_SYSTEM = (
    "You are a senior travel consultant at {company} with 15+ years of Nepal trekking "
    "experience. You write warm, professional, personal emails to prospective and booked "
    "clients. Never sound robotic or use marketing clichés. Be specific and genuinely "
    "helpful, draw on real Nepal trekking knowledge, and keep emails concise (150–280 words). "
    "Always include the customer's name when known, the destination, trip length, a clear "
    "call to action, and sign off as {consultant} ({company})."
)


@dataclass
class EmailDraft:
    style: str
    subject: str
    body: str
    used_llm: bool

    def to_dict(self) -> dict:
        return self.__dict__.copy()


def _greeting(name: str) -> str:
    return f"Dear {name}," if name else "Dear traveller,"


def _money(quote: Quotation) -> str:
    cost = quote.cost or {}
    cur = cost.get("currency", "USD")
    total = cost.get("total_price")
    pp = cost.get("per_person")
    if total is None:
        return ""
    line = f"{cur} {total:,.0f} total"
    if pp:
        line += f" ({cur} {pp:,.0f} per person)"
    return line


class EmailWriter:
    def __init__(self, settings: Settings | None = None, llm: LLMClient | None = None):
        self.settings = settings or get_settings()
        self.llm = llm or LLMClient(self.settings)

    # ----- public API -----

    def write(
        self,
        style: EmailStyle,
        estimate: Estimate | None = None,
        quotation: Quotation | None = None,
        context: str = "",
        extra_instructions: str = "",
    ) -> EmailDraft:
        if quotation is None and estimate is not None:
            quotation = build_quotation(estimate, self.settings)
        trip = estimate.trip if estimate else None
        customer = (trip.customer_name if trip else "") or ""

        # Try the LLM first.
        if self.llm.available:
            draft = self._write_llm(style, quotation, customer, trip, context, extra_instructions)
            if draft is not None:
                return draft
        # Fallback template.
        return self._write_template(style, quotation, customer, trip)

    # ----- LLM path -----

    def _write_llm(self, style, quotation, customer, trip, context, extra) -> EmailDraft | None:
        facts = self._fact_block(quotation, customer, trip)
        prompt = (
            f"Write a '{style.value}' email.\n"
            f"Goal: {_STYLE_BRIEF[style]}\n\n"
            f"Trip & quotation facts (use these, do not invent prices):\n{facts}\n"
        )
        if extra:
            prompt += f"\nAdditional instructions: {extra}\n"
        prompt += (
            "\nReturn the email as:\nSubject: <subject line>\n\n<email body>\n"
            "Do not include any commentary outside the email."
        )
        system = _SYSTEM.format(company=self.settings.company_name, consultant=self.settings.consultant_name)
        result = self.llm.complete(prompt, system=system, context=context)
        if not result.used_llm or not result.text:
            return None
        subject, body = self._split_subject(result.text)
        return EmailDraft(style.value, subject, body, used_llm=True)

    @staticmethod
    def _split_subject(text: str) -> tuple[str, str]:
        lines = text.strip().splitlines()
        subject = ""
        body_start = 0
        for i, line in enumerate(lines):
            if line.lower().startswith("subject:"):
                subject = line.split(":", 1)[1].strip()
                body_start = i + 1
                break
        body = "\n".join(lines[body_start:]).strip()
        if not subject:
            subject = "Your Nepal trek"
            body = text.strip()
        return subject, body

    def _fact_block(self, quotation: Quotation | None, customer: str, trip) -> str:
        bits = []
        if customer:
            bits.append(f"Customer name: {customer}")
        if trip:
            if trip.destination or trip.trek:
                bits.append(f"Destination/trek: {trip.trek or trip.destination}")
            if trip.duration_days:
                bits.append(f"Duration: {trip.duration_days} days")
            if trip.group_size:
                bits.append(f"Group size: {trip.group_size}")
            if trip.start_date:
                bits.append(f"Start: {trip.start_date}")
        if quotation:
            money = _money(quotation)
            if money:
                bits.append(f"Price: {money}")
            if quotation.permits:
                bits.append("Permits: " + ", ".join(quotation.permits))
            if quotation.important_notes:
                bits.append("Key advice: " + " ".join(quotation.important_notes[:2]))
        return "\n".join(f"- {b}" for b in bits) or "- (no structured facts available)"

    # ----- template fallback -----

    def _write_template(self, style, quotation, customer, trip) -> EmailDraft:
        company = self.settings.company_name
        signoff = f"\n\nWarm regards,\n{self.settings.consultant_name}\n{company}\n{self.settings.company_email}"
        dest = ""
        if trip:
            dest = trip.trek or trip.destination or ""
        days = trip.duration_days if trip else None
        money = _money(quotation) if quotation else ""
        g = _greeting(customer)
        dest_phrase = dest or "your Nepal adventure"

        if style == EmailStyle.FIRST_INQUIRY:
            subject = f"Your {dest_phrase} enquiry — let's plan it"
            body = (
                f"{g}\n\nThank you for reaching out about {dest_phrase}! It's one of our favourite "
                f"journeys and I'd love to tailor it to you.\n\n"
                "To build an accurate plan and quote, could you share:\n"
                "• Your preferred travel dates (month is fine)\n"
                "• Number of trekkers\n"
                "• How many days you have\n"
                "• Any preferences on comfort level (teahouse vs. upgraded hotels)\n\n"
                "With 15+ years guiding in the Himalaya, we'll make sure the pace, acclimatisation "
                "and logistics are right. Happy to jump on a quick call if that's easier."
            )
        elif style == EmailStyle.QUOTATION:
            subject = f"Your {dest_phrase} quotation" + (f" ({days} days)" if days else "")
            inc = "\n".join(f"• {x}" for x in (quotation.included[:6] if quotation else []))
            body = (
                f"{g}\n\nThank you for your patience — here is your personalised plan for {dest_phrase}"
                + (f" over {days} days" if days else "") + ".\n\n"
                + (f"Price: {money}\n\n" if money else "")
                + ("What's included:\n" + inc + "\n\n" if inc else "")
                + "A few tips from experience: travel insurance covering high-altitude evacuation is "
                "essential, and booking early secures the best teahouses and flight slots. The full "
                "itinerary, inclusions and terms are attached. Shall I hold these dates for you?"
            )
        elif style == EmailStyle.FOLLOW_UP:
            subject = f"Still keen on {dest_phrase}?"
            body = (
                f"{g}\n\nI wanted to gently follow up on the {dest_phrase} plan I sent over. "
                "No pressure at all — if anything needs adjusting (dates, budget, comfort level), "
                "I'm happy to rework it. Just let me know what would make it perfect for you."
            )
        elif style == EmailStyle.REMINDER:
            subject = f"A quick note on your {dest_phrase} plans"
            body = (
                f"{g}\n\nJust a friendly reminder about your {dest_phrase} trip. Permits and the best "
                "season slots can fill up, so if you'd like me to secure your dates, let me know and "
                "I'll take care of it."
            )
        elif style == EmailStyle.BOOKING_CONFIRMATION:
            subject = f"Confirmed: your {dest_phrase} adventure 🎉"
            body = (
                f"{g}\n\nWonderful news — your {dest_phrase} trip is confirmed! "
                "We're already arranging your permits and guide.\n\nNext steps:\n"
                "• Send a copy of your passport and insurance details\n"
                "• Review the packing list (attached)\n"
                "• Arrive in Kathmandu a day before departure\n\n"
                "We'll be in touch with final details closer to the date."
            )
        elif style == EmailStyle.PAYMENT_REMINDER:
            subject = f"Payment for your {dest_phrase} trip"
            body = (
                f"{g}\n\nA quick, friendly reminder regarding the balance for your {dest_phrase} trip"
                + (f" ({money})" if money else "") + ". "
                "You can pay by bank transfer or card — just reply and I'll send the details. "
                "Let me know if anything is unclear."
            )
        elif style == EmailStyle.THANK_YOU:
            subject = f"Thank you — see you in the Himalaya!"
            body = (
                f"{g}\n\nThank you so much for booking your {dest_phrase} trip with us. "
                "We're honoured to be part of your adventure and will make sure everything runs "
                "smoothly. I'll share final details shortly — in the meantime, start breaking in "
                "those boots!"
            )
        else:  # POST_TRIP
            subject = f"How was {dest_phrase}?"
            body = (
                f"{g}\n\nWelcome back! It was a pleasure arranging your {dest_phrase} trek. "
                "We'd love to hear how it went — and if you enjoyed it, a short review would mean "
                "the world to our small team. Whenever you're ready for the next adventure "
                "(Annapurna? Mustang? a peak climb?), we'll be here."
            )

        return EmailDraft(style.value, subject, body + signoff, used_llm=False)
