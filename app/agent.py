"""LangGraph Text-to-SQL workflow.

    prepare -> generate -> validate -> execute -> END
                  ^  |        |          |
                  |  v        v          v
                  +-- fix <---+----------+     (feedback to the model, at most MAX_LLM_CALLS calls)

`generate` also ends the run directly when the model refuses or asks to clarify, and
`validate` ends it with a refusal when the AST guard flags the SQL as unsafe.
"""
import datetime as dt
import json
import re
import sqlite3
from dataclasses import asdict
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.graph import END, StateGraph

from . import db, guard, llm
from .errors import ServiceError
from .prompts import SALES_RULES, prompt

MAX_LLM_CALLS = 4  # first try + up to 3 self-corrections

NOT_ALLOWED = "Maaf, permintaan ini tidak diizinkan."
CANNOT_PROCESS = "Maaf, tidak bisa diproses."
BAD_FORMAT = "Format salah. Balas HANYA JSON sesuai instruksi."
INVALID_SQL = "SQL tidak valid (harus satu SELECT). Perbaiki, balas JSON saja."


class State(TypedDict, total=False):
    user: db.User
    question: str
    history: list[tuple[str, str]]
    usage: llm.Usage
    messages: list[BaseMessage]
    reply: AIMessage            # the model's latest reply
    decision: dict              # its parsed JSON verdict
    feedback: str | None        # set when the reply needs another attempt
    error: str                  # error code to fail with if attempts run out
    result: dict                # the final answer


# ------------------------------------------------------------------ nodes
def prepare(state: State) -> State:
    """Dynamic prompt: schema and access scope are read from this user's own database."""
    user = state["user"]
    with db.connect(user) as con:
        schema, scope = db.schema_text(con), db.scope_text(con)
    messages = prompt.format_messages(
        username=user.username, full_name=user.full_name, role=user.role,
        today=dt.date.today().isoformat(), schema=schema, scope=scope,
        role_rules=SALES_RULES if user.is_sales else "",
        history=[("human" if role == "user" else "ai", text) for role, text in state["history"]],
        question=state["question"])
    return {"messages": messages}


def generate(state: State) -> State:
    reply = llm.invoke(state["messages"], state["usage"])
    decision = parse_decision(reply.text)
    if decision is None:
        return {"reply": reply, "feedback": BAD_FORMAT, "error": "llm_bad_output"}
    if decision["status"] != "ok":
        return {"reply": reply, "result": result(decision["status"], decision.get("answer") or CANNOT_PROCESS)}
    return {"reply": reply, "decision": decision}


def validate(state: State) -> State:
    verdict = guard.check(sql_of(state))
    if verdict is guard.Verdict.UNSAFE:
        return {"result": result("refused", NOT_ALLOWED)}
    if verdict is guard.Verdict.INVALID:
        return {"feedback": INVALID_SQL, "error": "sql_failed"}
    return {}


def execute(state: State) -> State:
    sql = sql_of(state)
    try:
        with db.connect(state["user"]) as con:
            columns, rows = db.query(con, sql)
    except sqlite3.Error as e:
        return {"feedback": f"SQL gagal: {str(e)[:300]}. Perbaiki, balas JSON saja.", "error": "sql_failed"}
    answer = render(state["decision"].get("answer") or "", rows)
    return {"result": result("ok", answer, {"columns": columns, "rows": rows}, sql)}


def fix(state: State) -> State:
    """Self-correction: send the problem back to the model as the next user turn."""
    feedback = HumanMessage(content=state["feedback"])
    return {"messages": [*state["messages"], state["reply"], feedback], "feedback": None}


# ------------------------------------------------------------------ edges
def next_step(default: str):
    """Route to the end once there is a result, to `fix` when there is feedback, else to `default`."""
    def route(state: State) -> str:
        if state.get("result"):
            return END
        if state.get("feedback"):
            return "fix"
        return default
    return route


def after_fix(state: State) -> str:
    return "generate" if state["usage"].llm_calls < MAX_LLM_CALLS else END


def build_graph():
    g = StateGraph(State)
    for name, node in [("prepare", prepare), ("generate", generate), ("validate", validate),
                       ("execute", execute), ("fix", fix)]:
        g.add_node(name, node)
    g.set_entry_point("prepare")
    g.add_edge("prepare", "generate")
    g.add_conditional_edges("generate", next_step("validate"), ["validate", "fix", END])
    g.add_conditional_edges("validate", next_step("execute"), ["execute", "fix", END])
    g.add_conditional_edges("execute", next_step(END), ["fix", END])
    g.add_conditional_edges("fix", after_fix, ["generate", END])
    return g.compile()


graph = build_graph()


def run(user: db.User, question: str, history: list[tuple[str, str]]) -> dict:
    usage = llm.Usage()
    final = graph.invoke({"user": user, "question": question, "history": history, "usage": usage})
    if not final.get("result"):
        raise ServiceError(final.get("error", "llm_bad_output"), asdict(usage))
    return {**final["result"], "usage": asdict(usage)}


# ------------------------------------------------------------------ helpers
def sql_of(state: State) -> str:
    return state["decision"]["sql"].strip().rstrip(";")


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


def result(status: str, answer: str, data: dict | None = None, sql: str | None = None) -> dict[str, Any]:
    return {"status": status, "answer": answer, "data": data, "sql": sql}


def render(note: str, rows: list[list]) -> str:
    """Short answer text; the figures themselves are in `data.rows`."""
    if not rows:
        return f"{note} Tidak ada data yang cocok.".strip()
    return f"{note} ({len(rows)} baris)".strip()
