"""Corafone Debt-Collections Voice Agent — Single File (LiveKit Cloud)

All logic, policy, ledger, and audit code lives in this one file.
Deployed via `lk agent deploy` from the corafone-agent/ directory.

Architecture:
  - AgentServer + @server.rtc_session(agent_name="corafone-collector")
  - 4 Agent subclasses with personas dict for branching handoffs
  - Session-level stt + llm + vad + turn_handling (shared)
  - Per-agent tts override only (per-agent voice: Ashley/Edward/Diego/Olivia)
  - Pure Python policy (validate + solvers) — no LLM
  - asyncpg ledger → Neon Postgres
  - Rule-based post-call audit classifier

"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Annotated, Optional

import asyncpg
from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    JobContext,
    RunContext,
    TurnHandlingOptions,
    cli,
    function_tool,
    inference,
    room_io,
)
from pydantic import Field
from livekit.plugins import ai_coustics

logger = logging.getLogger("corafone-collector")
logger.setLevel(logging.INFO)

load_dotenv(".env.local")

DATABASE_URL = os.getenv("DATABASE_URL")


# ═══════════════════════════════════════════════════════════
# SECTION 1: POLICY (pure Python, deterministic, no I/O)
# ═══════════════════════════════════════════════════════════

MAX_DISCOUNT_PCT = 0.20
MAX_PAYMENTS = 3
MIN_PAYMENT_PCT = 0.25
MAX_WINDOW_MONTHS = 3.0
CADENCE_SPAN_MONTHS = {"weekly": 0.231, "biweekly": 0.462, "monthly": 1.0}
CADENCES = {"weekly", "biweekly", "monthly"}


def validate(proposal_type: str, schedule: list[dict], cadence: str | None, principal: float) -> dict:
    """Pure Python validator. Returns {ok, reasons, counter_offers}.
    This function has NO I/O and NO LLM access.
    """
    total = sum(item["amount"] for item in schedule)
    floor = MIN_PAYMENT_PCT * total
    n = len(schedule)
    min_inst = min(item["amount"] for item in schedule) if schedule else 0.0

    if proposal_type == "full":
        ok = (n == 1 and total >= principal)
        counter = [best_full(principal)] if not ok else []

    elif proposal_type == "downpayment_plus_one":
        ok = (n == 2 and total >= principal and min_inst >= floor)
        counter = [best_downpayment_plus_one(principal)] if not ok else []

    elif proposal_type == "settlement":
        discount = principal - total
        window = n * CADENCE_SPAN_MONTHS.get(cadence or "monthly", 1.0)
        ok = (
            total <= principal
            and discount / principal <= MAX_DISCOUNT_PCT
            and n <= MAX_PAYMENTS
            and min_inst >= floor
            and window <= MAX_WINDOW_MONTHS
        )
        counter = [best_settlement(principal)] if not ok else []

    elif proposal_type == "payment_plan":
        window = n * CADENCE_SPAN_MONTHS.get(cadence or "monthly", 1.0)
        ok = (
            total >= principal
            and n <= MAX_PAYMENTS
            and (cadence or "monthly") in CADENCES
            and min_inst >= floor
            and window <= MAX_WINDOW_MONTHS
        )
        counter = [best_plan(principal)] if not ok else []

    else:
        ok = False
        counter = []

    reasons = []
    if not ok:
        if n > MAX_PAYMENTS:
            reasons.append(f"Plan allows at most {MAX_PAYMENTS} payments")
        if min_inst < floor:
            reasons.append(f"Each payment must be >= ${floor:.2f} (25% of the ${total:.2f} total)")
        if proposal_type in ("full", "downpayment_plus_one") and total < principal:
            reasons.append(f"Total must be at least ${principal:.2f}")
        if proposal_type == "settlement":
            discount = principal - total
            if discount / principal > MAX_DISCOUNT_PCT:
                reasons.append(f"Discount cannot exceed {MAX_DISCOUNT_PCT * 100:.0f}%")
        if proposal_type in ("settlement", "payment_plan"):
            window = n * CADENCE_SPAN_MONTHS.get(cadence or "monthly", 1.0)
            if window > MAX_WINDOW_MONTHS:
                reasons.append(f"Plan must complete within {int(MAX_WINDOW_MONTHS)} months")
        if proposal_type == "payment_plan" and (cadence or "monthly") not in CADENCES:
            reasons.append(f"Cadence must be one of: {', '.join(CADENCES)}")

    return {"ok": ok, "reasons": reasons, "counter_offers": counter}


def best_full(principal: float) -> dict:
    today = datetime.now(UTC).date().isoformat()
    return {"type": "full", "total": principal, "schedule": [{"date": today, "amount": principal}]}


def best_downpayment_plus_one(principal: float) -> dict:
    today = datetime.now(UTC).date().isoformat()
    in_30d = (datetime.now(UTC) + timedelta(days=30)).date().isoformat()
    half = round(principal / 2, 2)
    return {
        "type": "downpayment_plus_one",
        "total": principal,
        "schedule": [
            {"date": today, "amount": half},
            {"date": in_30d, "amount": round(principal - half, 2)},
        ],
    }


def best_settlement(principal: float) -> dict:
    total = round(principal * (1 - MAX_DISCOUNT_PCT), 2)
    payment = round(total / MAX_PAYMENTS, 2)
    schedule = []
    remaining = total
    for i in range(MAX_PAYMENTS):
        amount = round(remaining, 2) if i == MAX_PAYMENTS - 1 else payment
        date = (datetime.now(UTC) + timedelta(days=30 * (i + 1))).date().isoformat()
        schedule.append({"date": date, "amount": amount})
        remaining -= amount
    return {"type": "settlement", "total": total, "schedule": schedule}


def best_plan(principal: float) -> dict:
    payment = round(principal / MAX_PAYMENTS, 2)
    schedule = []
    remaining = principal
    for i in range(MAX_PAYMENTS):
        amount = round(remaining, 2) if i == MAX_PAYMENTS - 1 else payment
        date = (datetime.now(UTC) + timedelta(days=30 * (i + 1))).date().isoformat()
        schedule.append({"date": date, "amount": amount})
        remaining -= amount
    return {"type": "payment_plan", "total": principal, "schedule": schedule}


def all_counters_in_preference_order(principal: float) -> list[dict]:
    return [best_full(principal), best_downpayment_plus_one(principal),
            best_settlement(principal), best_plan(principal)]


# ═══════════════════════════════════════════════════════════
# SECTION 2: LEDGER (asyncpg → Neon Postgres)
# ═══════════════════════════════════════════════════════════

async def insert_agreement(
    pool: asyncpg.Pool,
    call_data: "CallData",
    validated_plan: dict,
    consent_phrase: str,
    call_id: str,
) -> str:
    """Write a committed agreement to Neon Postgres. Returns agreement_id."""
    async with pool.acquire() as conn:
        agreement_id = await conn.fetchval(
            """
            INSERT INTO agreements (debt_id, creditor_name, call_id, type, principal,
                                    total_agreed, schedule, cadence, consumer_name,
                                    consumer_phone, consent_phrase)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING agreement_id
            """,
            call_data.debt_id,
            call_data.creditor_name,
            call_id,
            validated_plan["type"],
            call_data.principal,
            validated_plan["total"],
            json.dumps(validated_plan["schedule"]),
            validated_plan.get("cadence"),
            call_data.consumer_name,
            call_data.consumer_phone,
            consent_phrase,
        )
    return str(agreement_id)


async def log_breach(
    pool: asyncpg.Pool,
    call_id: str,
    rule: str,
    transcript_excerpt: str = "",
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO compliance_breaches (call_id, rule, transcript_excerpt)
            VALUES ($1, $2, $3)
            """,
            call_id,
            rule,
            transcript_excerpt,
        )


async def log_dispute(pool: asyncpg.Pool, debt_id: str, reason: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO compliance_breaches (call_id, rule, transcript_excerpt)
            VALUES ('N/A', 'dispute', $1)
            """,
            f"Debt {debt_id}: {reason}",
        )


# ═══════════════════════════════════════════════════════════
# SECTION 3: AUDIT (post-call transcript classifier)
# ═══════════════════════════════════════════════════════════

BANNED_PHRASES = {
    "threat": ["sue", "lawsuit", "garnish", "garnishment", "arrest", "wage"],
    "false_urgency": ["expires in", "only today", "last chance", "deadline today"],
    "third_party": ["your employer", "your family", "your neighbor", "your spouse"],
}


def run_audit(chat_ctx_items: list) -> list[dict]:
    """Rule-based classifier on transcript items.
    Returns list of {rule, excerpt} dicts.
    """
    hits = []
    for item in chat_ctx_items:
        if getattr(item, "type", None) != "message" or getattr(item, "role", None) != "assistant":
            continue
        text = getattr(item, "content", "") or ""
        text_lower = text.lower()
        for rule, phrases in BANNED_PHRASES.items():
            for phrase in phrases:
                if phrase in text_lower:
                    idx = text_lower.find(phrase)
                    excerpt = text[max(0, idx - 50) : idx + len(phrase) + 50]
                    hits.append({"rule": rule, "excerpt": excerpt})
                    break  # one hit per rule per message
    return hits


# ═══════════════════════════════════════════════════════════
# SECTION 4: CallData (shared state across all agents)
# ═══════════════════════════════════════════════════════════

@dataclass
class CallData:
    # ── LiveKit context (injected by entrypoint) ──
    ctx: Optional[JobContext] = None
    personas: dict[str, Agent] = field(default_factory=dict)
    prev_agent: Optional[Agent] = None

    # ── Debt context (read from participant attributes) ──
    debt_id: str = ""
    creditor_name: str = ""
    principal: float = 1000.0
    days_delinquent: int = 180
    consumer_name: str = ""
    consumer_phone: str = ""
    expected_last_4_ssn: str = ""  # Opender turns text into numbers for verify_identity
    expected_dob: str = ""  # format MM/DD/YYYY

    # ── Mutable call state ──
    identity_verified: bool = False
    mini_miranda_read: bool = False
    validated_plan: Optional[dict] = None  # set ONLY by validate_proposal on ok=True
    dispute_raised: bool = False
    rejection_count: int = 0
    current_agent_name: str = "opener"
    call_ended: bool = False

    # ── Postgres connection pool (set by entrypoint) ──
    db_pool: Optional[asyncpg.Pool] = None

    def summarize(self) -> str:
        """Injected into each agent's system message on handoff."""
        return (
            f"Consumer {self.consumer_name}, debt ID {self.debt_id}, "
            f"${self.principal:.2f} owed to {self.creditor_name}, "
            f"{self.days_delinquent} days delinquent. "
            f"Identity verified: {self.identity_verified}. "
            f"Current agent: {self.current_agent_name}."
        )


RunContext_T = RunContext[CallData]


# ═══════════════════════════════════════════════════════════
# SECTION 5: BaseAgent (context preservation + transfer)
# ═══════════════════════════════════════════════════════════

class BaseAgent(Agent):
    async def on_enter(self) -> None:
        agent_name = self.__class__.__name__
        logger.info(f"Entering {agent_name}")

        userdata: CallData = self.session.userdata
        userdata.current_agent_name = agent_name
        if userdata.ctx and userdata.ctx.room:
            await userdata.ctx.room.local_participant.set_attributes({"agent": agent_name})

        chat_ctx = self.chat_ctx.copy()

        # Copy truncated context from previous agent (last 6 messages)
        if userdata.prev_agent:
            items_copy = self._truncate_chat_ctx(
                userdata.prev_agent.chat_ctx.items, keep_function_call=True
            )
            existing_ids = {item.id for item in chat_ctx.items}
            items_copy = [item for item in items_copy if item.id not in existing_ids]
            chat_ctx.items.extend(items_copy)

        chat_ctx.add_message(
            role="system",
            content=f"You are the {agent_name}. {userdata.summarize()}"
        )
        await self.update_chat_ctx(chat_ctx)
        await self.session.generate_reply()

    def _truncate_chat_ctx(
        self,
        items: list,
        keep_last_n_messages: int = 6,
        keep_system_message: bool = False,
        keep_function_call: bool = False,
    ) -> list:
        def _valid_item(item) -> bool:
            if not keep_system_message and getattr(item, "type", None) == "message" and getattr(item, "role", None) == "system":
                return False
            if not keep_function_call and getattr(item, "type", None) in ["function_call", "function_call_output"]:
                return False
            return True

        new_items = []
        for item in reversed(items):
            if _valid_item(item):
                new_items.append(item)
            if len(new_items) >= keep_last_n_messages:
                break
        new_items = new_items[::-1]

        while new_items and getattr(new_items[0], "type", None) in ["function_call", "function_call_output"]:
            new_items.pop(0)
        return new_items

    async def _transfer_to_agent(self, name: str, context: RunContext_T) -> Agent:
        userdata = context.userdata
        next_agent = userdata.personas[name]
        userdata.prev_agent = context.session.current_agent
        return next_agent


# ═══════════════════════════════════════════════════════════
# SECTION 6: Agent 1 — Opener (voice: Ashley)
# ═══════════════════════════════════════════════════════════

_WORD_TO_DIGIT = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "for": "4", "too": "2", "to": "2",
}


def normalize_ssn(raw: str) -> str:
    """Convert spoken-digit words to numeric digits and strip non-digits.
    Handles: 'one two three four' -> '1234', '1234' -> '1234',
    'one two three for' -> '1234', '1 2 3 4' -> '1234'"""
    raw = raw.strip().lower()
    # Try direct digit extraction first (already numeric)
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 4:
        return digits
    # Otherwise convert word-by-word
    words = raw.replace("-", " ").split()
    result = []
    for word in words:
        if word in _WORD_TO_DIGIT:
            result.append(_WORD_TO_DIGIT[word])
        elif word.isdigit():
            result.append(word)
    return "".join(result)


class OpenerAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are Cora, a professional debt collector for Corafone Recovery.

Your first utterance must be the mini-Miranda warning, read verbatim:
"This is an attempt to collect a debt by a debt collector, and any information
obtained will be used for that purpose."
Then state: "This call may be recorded."
After you have read the warning, call mark_mini_miranda_read.

Next, ask the consumer: "May I have your full name and the last 4 digits of
your Social Security Number to verify your identity?"

When the consumer provides their name and last 4 SSN, call verify_identity
with their name and last_4_ssn values. Do not call verify_identity before
you have heard the consumer speak both values.

If verify_identity succeeds, say "you are now being transferred to settle your bill" immediately call to_negotiator.
If verify_identity fails, ask again.

You MUST NEVER state a dollar amount, offer a payment plan, or agree to any payment.
If the consumer asks about payment options, say: "I'll connect you with our
negotiation specialist who can discuss that with you." Then call to_negotiator.

If the consumer disputes the debt, mentions a lawyer, bankruptcy, credit counseling,
DMP, says 'stop calling', or requests a human agent, call log_dispute and then
immediately call to_escalation.

Be calm, brief, and compliant. Never be aggressive.""",
            tts=inference.TTS(model="inworld/inworld-tts-2", voice="Ashley"),
        )

    @function_tool
    async def verify_identity(
        self,
        context: RunContext_T,
        name: Annotated[str, Field(description="Consumer's full name as stated")],
        last_4_ssn: Annotated[
            str,
            Field(
                description=(
                    "The last 4 digits of the consumer's Social Security Number as digits. "
                    "If the consumer says 'one two three four', convert to '1234'. "
                    "Always pass exactly 4 numeric digits, never words."
                )
            ),
        ],
        dob: Annotated[Optional[str], Field(description="Date of birth in MM/DD/YYYY if provided")] = None,
    ) -> str:
        """Verify consumer identity by looking up name + last_4_ssn in the database."""
        userdata: CallData = context.userdata
        logger.info(f"verify_identity CALLED: name='{name}', last_4_ssn='{last_4_ssn}', dob='{dob}'")

        if not userdata.db_pool:
            logger.warning("verify_identity: db_pool is None — DATABASE_URL not set on LiveKit Cloud")
            return "Identity verification unavailable — no database connection."

        # Normalize spoken-digit words to numeric digits before querying
        normalized_ssn = normalize_ssn(last_4_ssn)
        logger.info(f"verify_identity: normalized_ssn='{normalized_ssn}' (from raw last_4_ssn='{last_4_ssn}')")

        # Look up consumer by name + last_4_ssn from the database
        row = None
        async with userdata.db_pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT consumer_name, last_4_ssn, dob, consumer_phone, principal, days_delinquent
                FROM consumers
                WHERE consumer_name ILIKE $1 AND last_4_ssn = $2
                """,
                name.strip(),
                normalized_ssn,
            )
            if row:
                logger.info(
                    f"verify_identity: MATCH FOUND — consumer_name='{row['consumer_name']}', "
                    f"ssn='{row['last_4_ssn']}', debt_id='{userdata.debt_id}'"
                )

        if row:
            # Populate userdata with the found consumer's data
            userdata.consumer_name = row["consumer_name"]
            userdata.expected_last_4_ssn = row["last_4_ssn"] or ""
            userdata.expected_dob = row["dob"] or ""
            userdata.consumer_phone = row["consumer_phone"] or ""
            userdata.principal = float(row["principal"]) if row["principal"] else userdata.principal
            userdata.days_delinquent = row["days_delinquent"] or userdata.days_delinquent
            userdata.identity_verified = True
            logger.info(f"verify_identity: SUCCESS — identity_verified=True for '{row['consumer_name']}'")
            return f"Identity verified. Welcome, {row['consumer_name']}. You may now discuss the debt."

        logger.warning(
            f"verify_identity: NO MATCH — name='{name.strip()}', normalized_ssn='{normalized_ssn}'. "
            f"Both name and last 4 SSN must match. Please ask again."
        )
        return (
            "Identity verification failed. No matching record found. "
            "Please ask the consumer to provide their correct full name and last 4 "
            "digits of their Social Security Number."
        )

    @function_tool
    async def mark_mini_miranda_read(self, context: RunContext_T) -> str:
        """Call after reading the mini-Miranda to record compliance."""
        context.userdata.mini_miranda_read = True
        return "Mini-Miranda recorded."

    @function_tool
    async def log_dispute(
        self,
        context: RunContext_T,
        reason: Annotated[str, Field(description="Brief reason for dispute")],
    ) -> str:
        """Log a dispute and stop collection activity."""
        userdata: CallData = context.userdata
        userdata.dispute_raised = True
        if userdata.db_pool:
            await log_dispute(userdata.db_pool, userdata.debt_id, reason)
        return "Dispute logged."

    @function_tool
    async def to_negotiator(self, context: RunContext_T) -> Agent:
        """Transfer to Negotiator after identity verification."""
        userdata: CallData = context.userdata
        if not userdata.identity_verified:
            raise ValueError("Cannot transfer to negotiator — identity not verified")
        if not userdata.mini_miranda_read:
            raise ValueError("Cannot transfer to negotiator — mini-Miranda not read")
        await self.session.say("Thank you for verifying. Now let's discuss your account.")
        return await self._transfer_to_agent("negotiator", context)

    @function_tool
    async def to_escalation(self, context: RunContext_T) -> Agent:
        """Transfer to Escalation specialist."""
        await self.session.say("I'll transfer you to our escalation specialist.")
        return await self._transfer_to_agent("escalation", context)

    @function_tool
    async def refuse_payment_discussion(self, context: RunContext_T) -> Agent:
        """Call this when the consumer mentions any payment amount, plan, or asks
        about payment options. You cannot discuss payments. This transfers to
        the Negotiator who handles all payment discussions."""
        userdata: CallData = context.userdata
        if not userdata.identity_verified:
            raise ValueError("Cannot transfer — identity not verified yet. Ask for name and last 4 SSN first.")
        if not userdata.mini_miranda_read:
            raise ValueError("Cannot transfer — mini-Miranda not read yet.")
        await self.session.say(
            "I'll connect you with our negotiation specialist "
            "who can discuss payment options with you."
        )
        return await self._transfer_to_agent("negotiator", context)


# ═══════════════════════════════════════════════════════════
# SECTION 7: Agent 2 — Negotiator (voice: Edward)
# ═══════════════════════════════════════════════════════════

class NegotiatorAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are Edward, a Corafone Recovery negotiator.
You are respectful but firm. You never threaten legal action, arrest, garnishment,
or any unverifiable consequence. You never invent deadlines, urgency, or consequences.
Deadlines come ONLY from the validated tool output — never from you.
You never discuss family members, employers, neighbors, or third parties.

CRITICAL RULES (never violate these):
1. You MUST call best_offers at the start of your first turn. Do not speak before calling it.
2. You MUST call validate_proposal before agreeing to ANY plan the consumer proposes.
   If you find yourself about to say "yes" or "that works" to a payment plan,
   STOP and call validate_proposal first.
3. You may ONLY offer the exact numbers returned by best_offers or validate_proposal.
   Never say a number that did not come from a tool result.
4. If validate_proposal returns ok=False, you MUST read the reasons aloud and
   present the counter_offers. Never agree to an invalid plan.
5. If validate_proposal returns ok=True AND the consumer accepts the plan,
   call to_closer immediately.
6. If the consumer rejects all 4 offers, call to_escalation.
7. You MUST NOT agree to, confirm, or say "yes" to any plan that has not been
   validated by validate_proposal with ok=True.
8. You MUST NOT state any payment amount, discount, or schedule that did not
   come from a tool result.

Offer outcome #1 (full payment) first. If rejected, offer #2. If rejected, #3. If rejected, #4.
NEVER skip ahead. NEVER offer a number that wasn't returned by best_offers.

When the consumer names ANY amount, schedule, or plan, you MUST call validate_proposal
with their exact schedule before verbally agreeing. If validation returns ok=False,
read the reasons aloud, then present the counter_offers in ranked order.

If the consumer disputes, mentions lawyer/bankruptcy/DMP, or says 'stop calling',
call log_dispute then to_escalation.

Track rejections in state — do not rely on tool-call budget.""",
            tts=inference.TTS(model="inworld/inworld-tts-2", voice="Edward"),
        )

    @function_tool
    async def best_offers(self, context: RunContext_T) -> list[dict]:
        """Return all four counter-offers in preference order."""
        return all_counters_in_preference_order(context.userdata.principal)

    @function_tool
    async def validate_proposal(
        self,
        context: RunContext_T,
        type: Annotated[str, Field(description="One of: full, downpayment_plus_one, settlement, payment_plan")],
        schedule: Annotated[list[dict], Field(description="List of {date: ISO-8601 string, amount: float}")],
        cadence: Annotated[Optional[str], Field(description="weekly, biweekly, or monthly — only for payment_plan")] = None,
    ) -> dict:
        """Validate a consumer proposal. Sets validated_plan ONLY if ok=True.
        Returns next_action telling you what to do next."""
        userdata: CallData = context.userdata
        result = validate(type, schedule, cadence, userdata.principal)
        if result["ok"]:
            userdata.validated_plan = {
                "type": type,
                "total": sum(item["amount"] for item in schedule),
                "schedule": schedule,
                "cadence": cadence,
            }
            result["next_action"] = (
                "Plan is VALID. Read the plan back to the consumer clearly: "
                "total amount, number of payments, and each payment date and amount. "
                "Then ask: 'Do you accept this payment plan?' "
                "If they say yes, call to_closer. If they say no, continue negotiating."
            )
        else:
            result["next_action"] = (
                "Plan is INVALID. Read the reasons aloud to the consumer. "
                "Then present the counter_offers in ranked order. "
                "Do NOT agree to this plan."
            )
        return result

    @function_tool
    async def log_dispute(
        self,
        context: RunContext_T,
        reason: Annotated[str, Field(description="Brief reason")],
    ) -> str:
        userdata: CallData = context.userdata
        userdata.dispute_raised = True
        if userdata.db_pool:
            await log_dispute(userdata.db_pool, userdata.debt_id, reason)
        return "Dispute logged."

    @function_tool
    async def log_compliance_breach(
        self,
        context: RunContext_T,
        rule: Annotated[str, Field(description="Name of breached rule, e.g. 'threat' or 'false_urgency'")],
    ) -> str:
        userdata: CallData = context.userdata
        if userdata.db_pool:
            await log_breach(userdata.db_pool, userdata.ctx.room.name, rule)
        return "Breach logged."

    @function_tool
    async def to_closer(self, context: RunContext_T) -> Agent:
        userdata: CallData = context.userdata
        if userdata.validated_plan is None:
            raise ValueError("Cannot transfer to closer — no validated plan")
        await self.session.say("Great. Let me connect you to our closer to confirm the agreement.")
        return await self._transfer_to_agent("closer", context)

    @function_tool
    async def to_escalation(self, context: RunContext_T) -> Agent:
        await self.session.say("I'll transfer you to our escalation specialist.")
        return await self._transfer_to_agent("escalation", context)


# ═══════════════════════════════════════════════════════════
# SECTION 8: Agent 3 — Closer (voice: Diego)
# ═══════════════════════════════════════════════════════════

class CloserAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are Diego, a Corafone Recovery closer.
Your job is to capture the consumer's verbal consent to the validated plan.
Read the plan back clearly: total amount, number of payments, and each payment date and amount.
Then ask the consumer to confirm in their own words: 'Yes, I agree to pay $X on DATE...'
Once they confirm, call commit_agreement with the verbatim consent phrase.
Read back the agreement_id. Thank them and end the call politely.
NEVER change any numbers. If the consumer wants to renegotiate, call to_negotiator.
If they refuse to consent, call to_escalation.""",
            tts=inference.TTS(model="inworld/inworld-tts-2", voice="Diego"),
        )

    @function_tool
    async def commit_agreement(
        self,
        context: RunContext_T,
        consent_phrase: Annotated[str, Field(description="The consumer's exact words of consent")],
    ) -> dict:
        """Write the agreement to Postgres. Requires validated_plan to be set."""
        userdata: CallData = context.userdata
        if userdata.validated_plan is None:
            raise ValueError("No validated plan — cannot commit")
        if userdata.db_pool is None:
            raise ValueError("No database connection")
        agreement_id = await insert_agreement(
            userdata.db_pool,
            userdata,
            userdata.validated_plan,
            consent_phrase,
            userdata.ctx.room.name,
        )
        return {"agreement_id": agreement_id}

    @function_tool
    async def to_negotiator(self, context: RunContext_T) -> Agent:
        await self.session.say("Let's revisit the payment options.")
        # Clear validated_plan so renegotiation starts fresh
        context.userdata.validated_plan = None
        return await self._transfer_to_agent("negotiator", context)

    @function_tool
    async def to_escalation(self, context: RunContext_T) -> Agent:
        await self.session.say("I'll transfer you to our escalation specialist.")
        return await self._transfer_to_agent("escalation", context)


# ═══════════════════════════════════════════════════════════
# SECTION 9: Agent 4 — Escalation (voice: Olivia)
# ═══════════════════════════════════════════════════════════

class EscalationAgent(BaseAgent):
    def __init__(self) -> None:
        super().__init__(
            instructions="""You are Olivia, a Corafone Recovery escalation specialist.
You handle disputes, human requests, bankruptcy/attorney mentions, compliance breaches,
and consumers who have rejected all offers.
You do NOT continue collection activity.
If a dispute was raised, say: 'Your dispute has been noted and collection activity
on this account will stop while we review it.'
End the call politely. Do not transfer elsewhere.""",
            tts=inference.TTS(model="inworld/inworld-tts-2", voice="Olivia"),
        )

    @function_tool
    async def log_compliance_breach(
        self,
        context: RunContext_T,
        rule: Annotated[str, Field(description="Rule name")],
    ) -> str:
        userdata: CallData = context.userdata
        if userdata.db_pool:
            await log_breach(userdata.db_pool, userdata.ctx.room.name, rule)
        return "Breach logged."

    @function_tool
    async def end_call(self, context: RunContext_T) -> None:
        """Signal that the call should end."""
        context.userdata.call_ended = True


# ═══════════════════════════════════════════════════════════
# SECTION 10: Entrypoint (AgentServer + @rtc_session)
# ═══════════════════════════════════════════════════════════

server = AgentServer()


@server.rtc_session(agent_name="corafone-collector")
async def entrypoint(ctx: JobContext):
    logger.info("Starting Corafone agent")
    logger.info(f"Room: {ctx.room.name}")

    # ── Connect to room ──
    await ctx.connect()

    # ── Wait for browser participant ──
    participant = await ctx.wait_for_participant()
    logger.info(f"Participant: {participant.identity}")

    # ── Read debt context from participant ATTRIBUTES ──
    attrs = participant.attributes
    principal = float(attrs.get("principal", "1000.00"))
    days_delinquent = int(attrs.get("days_delinquent", "180"))

    # ── Create Postgres pool ──
    db_pool = None
    if DATABASE_URL:
        try:
            db_pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=2)
            logger.info("DB pool created successfully")
        except Exception as e:
            logger.error(f"DB pool creation FAILED: {e}")
            logger.error(f"DATABASE_URL host: {DATABASE_URL.split('@')[-1].split('/')[0] if '@' in DATABASE_URL else 'unknown'}")
            db_pool = None
    else:
        logger.warning("DATABASE_URL not set — db_pool will be None, identity verification will fail")

    # ── Create shared state ──
    # Identity fields (consumer_name, expected_last_4_ssn, expected_dob) are
    # populated by the verify_identity tool when the consumer speaks their name
    # and last 4 SSN during the call. The tool queries the consumers table.
    userdata = CallData(
        ctx=ctx,
        debt_id=attrs.get("debt_id", ""),
        creditor_name=attrs.get("creditor_name", "Corafone"),
        principal=principal,
        days_delinquent=days_delinquent,
        consumer_name="",      # set by verify_identity tool
        consumer_phone=attrs.get("consumer_phone", ""),
        expected_last_4_ssn="", # set by verify_identity tool
        expected_dob="",      # set by verify_identity tool
        db_pool=db_pool,
    )

    # ── Instantiate all 4 agents ──
    opener = OpenerAgent()
    negotiator = NegotiatorAgent()
    closer = CloserAgent()
    escalation = EscalationAgent()

    userdata.personas.update({
        "opener": opener,
        "negotiator": negotiator,
        "closer": closer,
        "escalation": escalation,
    })

    # ── Create session ──
    # Session provides shared STT + LLM + VAD + turn handling
    # Each agent overrides only TTS (voice)
    session = AgentSession[CallData](
        userdata=userdata,
        stt=inference.STT(model="deepgram/nova-3", language="en"),
        llm=inference.LLM(model="google/gemma-4-31b-it"),
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            preemptive_generation={"enabled": True},
            endpointing={"min_delay": 0.6, "max_delay": 3.0},
        ),
        vad=inference.VAD(),
    )

    # ── Start with Opener ──
    # NOTE: session.start() is non-blocking in the AgentServer API. will add end call logic another time.
    await session.start(
        agent=opener,
        room=ctx.room,
        room_options=room_io.RoomOptions(
            audio_input=room_io.AudioInputOptions(
                noise_cancellation=ai_coustics.audio_enhancement(
                    model=ai_coustics.EnhancerModel.QUAIL_VF_S,
                ),
            ),
        ),
    )


if __name__ == "__main__":
    cli.run_app(server)
