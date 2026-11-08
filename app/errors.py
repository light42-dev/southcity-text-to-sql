"""The one exception type the service raises, and how each code is shown to clients."""


class ServiceError(Exception):
    """An upstream failure (LLM or database) that ends the request with a clean error reply."""

    def __init__(self, code: str, usage: dict | None = None):
        super().__init__(code)
        self.code = code
        self.usage = usage


# code -> (HTTP status, user-facing answer)
ERRORS = {
    "llm_timeout": (504, "Layanan sedang sibuk, silakan coba lagi."),
    "llm_rate_limited": (429, "Terlalu banyak permintaan, silakan coba lagi sebentar lagi."),
    "llm_unavailable": (502, "Layanan AI sedang tidak tersedia, silakan coba lagi."),
    "llm_bad_output": (502, "Sistem tidak dapat menyusun jawaban, silakan ulangi pertanyaan."),
    "sql_failed": (502, "Pertanyaan belum bisa dijawab, silakan ubah kalimatnya."),
    "db_unavailable": (503, "Database sedang tidak dapat diakses, silakan coba lagi."),
}
