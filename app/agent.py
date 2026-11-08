"""LangChain Text-to-SQL pipeline: dynamic prompt -> LLM -> AST guard -> execute -> self-correct."""
import datetime as dt
import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass

import anthropic
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage

from . import db, guard
from .errors import ServiceError
from .prompts import SALES_RULES, prompt

MODEL = os.getenv("LLM_MODEL", "claude-sonnet-4-6")
PRICE_IN = float(os.getenv("LLM_PRICE_IN_PER_MTOK", "3")) / 1e6
PRICE_OUT = float(os.getenv("LLM_PRICE_OUT_PER_MTOK", "15")) / 1e6
MAX_LLM_CALLS = 4  # first try + up to 3 self-corrections
ANSWER_ROWS = 20   # rows spelled out in the answer text

# The SDK retries rate limits, 5xx and timeouts once by itself.
llm = ChatAnthropic(model=MODEL, max_tokens=1200, timeout=15, max_retries=1)

NOT_ALLOWED = "Maaf, permintaan ini tidak diizinkan."
CANNOT_PROCESS = "Maaf, tidak bisa diproses."
BAD_FORMAT = "Format salah. Balas HANYA JSON sesuai instruksi."


@dataclass
class Usage:
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0

    def record(self, reply: AIMessage) -> None:
        meta = reply.usage_metadata or {}
        tokens_in, tokens_out = meta.get("input_tokens", 0), meta.get("output_tokens", 0)
        self.llm_calls += 1
        self.prompt_tokens += tokens_in
        self.completion_tokens += tokens_out
        self.total_tokens += tokens_in + tokens_out
        self.cost_usd = round(self.cost_usd + tokens_in * PRICE_IN + tokens_out * PRICE_OUT, 6)


@dataclass
class Retry:
    """Feed `feedback` back to the model; if attempts run out, fail with `code`."""
    feedback: str
    code: str


def run(user: db.User, question: str, history: list[tuple[str, str]]) -> dict:
    usage = Usage()
    with db.connect(user) as con:
        messages = build_messages(user, con, question, history)
        last_error = "llm_bad_output"
        while usage.llm_calls < MAX_LLM_CALLS:
            reply = call_llm(messages, usage)
            outcome = handle(reply, con)
            if not isinstance(outcome, Retry):
                return {**outcome, "usage": asdict(usage)}
            messages += [reply, HumanMessage(content=outcome.feedback)]
            last_error = outcome.code
    raise ServiceError(last_error, asdict(usage))


def build_messages(user: db.User, con: sqlite3.Connection, question: str, history: list[tuple[str, str]]):
    return prompt.format_messages(
        username=user.username, full_name=user.full_name, role=user.role,
        today=dt.date.today().isoformat(), schema=db.schema_text(con), scope=db.scope_text(con),
        role_rules=SALES_RULES if user.is_sales else "",
        history=[("human" if role == "user" else "ai", content) for role, content in history],
        question=question)


def call_llm(messages, usage: Usage) -> AIMessage:
    try:
        reply = llm.invoke(messages)
    except anthropic.APIError as e:
        raise ServiceError(llm_error_code(e), asdict(usage)) from e
    usage.record(reply)
    return reply


def llm_error_code(e: anthropic.APIError) -> str:
    if isinstance(e, anthropic.APITimeoutError):
        return "llm_timeout"
    if isinstance(e, anthropic.RateLimitError):
        return "llm_rate_limited"
    return "llm_unavailable"


def handle(reply: AIMessage, con: sqlite3.Connection) -> dict | Retry:
    """Turn one model reply into a final result, or into feedback for another attempt."""
    decision = parse_decision(reply.text)
    if decision is None:
        return Retry(BAD_FORMAT, "llm_bad_output")
    if decision["status"] != "ok":
        return result(decision["status"], decision.get("answer") or CANNOT_PROCESS)

    sql = decision["sql"].strip().rstrip(";")
    verdict = guard.check(sql)
    if verdict is guard.Verdict.UNSAFE:
        return result("refused", NOT_ALLOWED)
    if verdict is guard.Verdict.INVALID:
        return Retry("SQL tidak valid (harus satu SELECT). Perbaiki, balas JSON saja.", "sql_failed")
    try:
        columns, rows = db.query(con, sql)
    except sqlite3.Error as e:
        return Retry(f"SQL gagal: {str(e)[:300]}. Perbaiki, balas JSON saja.", "sql_failed")
    answer = render(decision.get("answer") or "", columns, rows)
    return result("ok", answer, {"columns": columns, "rows": rows}, sql)


def parse_decision(text: str) -> dict | None:
    """The model's JSON verdict {status, sql, answer}, or None if it is unusable."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        return None
    try:
        decision = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(decision, dict) or decision.get("status") not in {"ok", "refused", "clarify"}:
        return None
    if decision["status"] == "ok" and not isinstance(decision.get("sql"), str):
        return None
    return decision


def result(status: str, answer: str, data: dict | None = None, sql: str | None = None) -> dict:
    return {"status": status, "answer": answer, "data": data, "sql": sql}


def render(note: str, columns: list[str], rows: list[list]) -> str:
    if not rows:
        return f"{note} Tidak ada data yang cocok.".strip()
    lines = [f"{note} ({len(rows)} baris)"]
    lines += ["- " + ", ".join(f"{c}: {v}" for c, v in zip(columns, row)) for row in rows[:ANSWER_ROWS]]
    if len(rows) > ANSWER_ROWS:
        lines.append(f"... dan {len(rows) - ANSWER_ROWS} baris lainnya.")
    return "\n".join(lines)
