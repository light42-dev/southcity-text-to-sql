"""FastAPI entry point: POST /api/chat, GET /api/health."""
import json
import os
import unicodedata

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from . import agent, db

FAULTS_ON = os.getenv("ENABLE_FAULT_INJECTION") == "1"
MAX_BODY = 256_000
MAX_MESSAGE = 4_000
MAX_HISTORY = 6

ERRORS = {  # code -> (http status, user-facing answer)
    "llm_timeout": (504, "Layanan sedang sibuk, silakan coba lagi."),
    "llm_rate_limited": (429, "Terlalu banyak permintaan, silakan coba lagi sebentar lagi."),
    "llm_unavailable": (502, "Layanan AI sedang tidak tersedia, silakan coba lagi."),
    "llm_bad_output": (502, "Sistem tidak dapat menyusun jawaban, silakan ulangi pertanyaan."),
    "sql_failed": (502, "Pertanyaan belum bisa dijawab, silakan ubah kalimatnya."),
    "db_unavailable": (503, "Database sedang tidak dapat diakses, silakan coba lagi."),
}

# Cyrillic/Greek look-alikes -> Latin, so "Sіti" is read as "Siti"
CONFUSABLES = str.maketrans("аеорсухіјѕАВЕКМНОРСТХІЈЅ", "aeopcyxijsABEKMHOPCTXIJS")

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
db.build()


def zero_usage():
    return agent.Usage()


def bad_request(message: str, status: int = 400):
    return JSONResponse({"status": "error", "answer": "Permintaan tidak valid.",
                         "error": {"code": "bad_request", "message": message},
                         "usage": zero_usage()}, status_code=status)


def with_fault(resp: JSONResponse, fault: str | None) -> JSONResponse:
    if fault:
        resp.headers["X-Fault-Injected"] = fault
    return resp


@app.get("/api/health")
def health():
    return {"status": "ok", "fault_injection": FAULTS_ON}


@app.post("/api/chat")
async def chat(request: Request):
    fault = request.headers.get("x-test-fault") if FAULTS_ON else None
    if "application/json" not in request.headers.get("content-type", ""):
        return bad_request("Content-Type harus application/json.", 415)
    raw = await request.body()
    if len(raw) > MAX_BODY:
        return bad_request("Body terlalu besar.", 413)
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return bad_request("Body harus JSON UTF-8 yang valid.")
    if not isinstance(body, dict):
        return bad_request("Body harus berupa objek JSON.")
    username, message, history = body.get("username"), body.get("message"), body.get("history", [])
    if not isinstance(username, str) or username not in db.USERS:
        return bad_request("Username tidak dikenal.")
    if not isinstance(message, str) or not message.strip():
        return bad_request("Pesan tidak boleh kosong.")
    if len(message) > MAX_MESSAGE:
        return bad_request("Pesan terlalu panjang.", 413)
    if history is None:
        history = []
    if not isinstance(history, list) or not all(
            isinstance(t, dict) and t.get("role") in {"user", "assistant"} and isinstance(t.get("content"), str)
            for t in history):
        return bad_request("Format history tidak valid.")
    # Only this badge's own turns; drop everything else, keep the tail
    history = [t for t in history if t.get("username") == username][-MAX_HISTORY:]
    history = [{"role": t["role"], "content": t["content"][:MAX_MESSAGE]} for t in history]

    # Hidden/format characters (e.g. Unicode tag smuggling) are an attack, not a question
    if any(unicodedata.category(ch) == "Cf" for ch in message):
        return with_fault(JSONResponse({"status": "refused", "answer": "Maaf, pesan berisi karakter tersembunyi "
                                        "dan tidak dapat diproses.", "data": None, "sql": None,
                                        "usage": zero_usage()}), fault)
    message = unicodedata.normalize("NFKC", message).translate(CONFUSABLES).strip()

    try:
        result = await run_in_threadpool(agent.run, username, message, history, fault)
        return with_fault(JSONResponse(result), fault)
    except agent.PipelineError as e:
        status, answer = ERRORS[e.code]
        resp = JSONResponse({"status": "error", "answer": answer,
                             "error": {"code": e.code, "message": answer},
                             "data": None, "sql": None,
                             "usage": getattr(e, "usage", None) or zero_usage()}, status_code=status)
        if e.code == "llm_rate_limited":
            resp.headers["Retry-After"] = "5"
        return with_fault(resp, fault)
