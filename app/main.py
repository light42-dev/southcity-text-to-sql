"""FastAPI entry point: POST /api/chat, GET /api/health."""
import unicodedata
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictStr, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import agent, db
from .errors import ERRORS, ServiceError

MAX_BODY_BYTES = 256_000
MAX_MESSAGE_CHARS = 4_000
MAX_HISTORY_TURNS = 6

# Cyrillic look-alikes -> Latin, so "Sіti" is read as "Siti"
CONFUSABLES = str.maketrans("аеорсухіјѕАВЕКМНОРСТХІЈЅ", "aeopcyxijsABEKMHOPCTXIJS")
EMPTY_USAGE = {"llm_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    content: StrictStr = Field(max_length=MAX_MESSAGE_CHARS)
    username: StrictStr | None = None


class ChatRequest(BaseModel):
    username: StrictStr
    message: StrictStr = Field(max_length=MAX_MESSAGE_CHARS)
    history: list[Turn] | None = None

    @field_validator("username")
    @classmethod
    def known_user(cls, v: str) -> str:
        if v not in db.USERS:
            raise ValueError("unknown username")
        return v

    @field_validator("message")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message is empty")
        return v


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.build()
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def error_body(code: str, answer: str, message: str, usage: dict | None = None) -> dict:
    return {"status": "error", "answer": answer, "error": {"code": code, "message": message},
            "data": None, "sql": None, "usage": usage or EMPTY_USAGE}


@app.middleware("http")
async def limit_body_size(request: Request, call_next):
    if int(request.headers.get("content-length") or 0) > MAX_BODY_BYTES:
        return JSONResponse(error_body("bad_request", "Permintaan tidak valid.", "Body terlalu besar."), 413)
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def invalid_request(_: Request, exc: RequestValidationError):
    fields = sorted({".".join(str(p) for p in e["loc"][1:] if isinstance(p, str)) or "body" for e in exc.errors()})
    message = "Field tidak valid: " + ", ".join(fields)
    return JSONResponse(error_body("bad_request", "Permintaan tidak valid.", message), 422)


@app.exception_handler(StarletteHTTPException)
async def http_error(_: Request, exc: StarletteHTTPException):
    return JSONResponse(error_body("bad_request", "Permintaan tidak valid.", str(exc.detail)), exc.status_code,
                        headers=exc.headers)


@app.exception_handler(ServiceError)
async def service_error(_: Request, exc: ServiceError):
    status, answer = ERRORS[exc.code]
    headers = {"Retry-After": "5"} if exc.code == "llm_rate_limited" else None
    return JSONResponse(error_body(exc.code, answer, answer, exc.usage), status, headers=headers)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/chat")
def chat(req: ChatRequest):
    # Hidden format characters (e.g. Unicode tag smuggling) are an attack, not a question
    if any(unicodedata.category(ch) == "Cf" for ch in req.message):
        return {"status": "refused", "answer": "Maaf, pesan berisi karakter tersembunyi dan tidak dapat diproses.",
                "data": None, "sql": None, "usage": EMPTY_USAGE}
    message = unicodedata.normalize("NFKC", req.message).translate(CONFUSABLES).strip()
    # Only this badge's own turns count; anything from another account is dropped
    history = [(t.role, t.content) for t in req.history or [] if t.username == req.username]
    return agent.run(db.USERS[req.username], message, history[-MAX_HISTORY_TURNS:])
