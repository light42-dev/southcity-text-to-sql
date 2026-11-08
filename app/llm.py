"""The chat model (OpenRouter, OpenAI-compatible API) and per-request usage accounting."""
import os
from dataclasses import asdict, dataclass

import openai
from langchain_core.exceptions import LangChainException
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from .errors import ServiceError

MODEL = os.getenv("LLM_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
# OpenRouter tries these in order if the main model is down or rate-limited
FALLBACK_MODELS = [m.strip() for m in os.getenv("LLM_FALLBACK_MODELS", "nvidia/nemotron-3.5-super-vl:free").split(",")
                   if m.strip()]

REASONING_EFFORT = os.getenv("LLM_REASONING_EFFORT", "low")

chat_model = ChatOpenAI(
    model=MODEL,
    base_url=os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1"),
    api_key=os.getenv("OPENROUTER_API_KEY", "not-set"),
    max_tokens=1500,
    timeout=20,
    max_retries=1,  # the SDK retries rate limits, 5xx and timeouts once by itself
    extra_body={"models": [MODEL, *FALLBACK_MODELS],
                # Hidden reasoning costs latency; lower it (e.g. "minimal") if replies are slow
                "reasoning": {"effort": REASONING_EFFORT, "exclude": True}},
)


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
        cost = (reply.response_metadata.get("token_usage") or {}).get("cost") or 0  # OpenRouter reports cost
        self.llm_calls += 1
        self.prompt_tokens += tokens_in
        self.completion_tokens += tokens_out
        self.total_tokens += tokens_in + tokens_out
        self.cost_usd = round(self.cost_usd + float(cost), 6)


def invoke(messages, usage: Usage) -> AIMessage:
    try:
        reply = chat_model.invoke(messages)
    except (openai.APIError, LangChainException) as e:
        raise ServiceError(error_code(e), asdict(usage)) from e
    usage.record(reply)
    return reply


def error_code(e: Exception) -> str:
    if isinstance(e, openai.APITimeoutError):
        return "llm_timeout"
    if isinstance(e, openai.RateLimitError):
        return "llm_rate_limited"
    return "llm_unavailable"
