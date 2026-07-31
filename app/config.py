from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    supabase_project_url: str
    gemini_api_key: str
    ai_model: str = "gemini-2.5-flash"
    max_free_queries: int = 1000

    # Provider selection. "gemini" (default) keeps existing behaviour untouched;
    # "ollama" routes generation to Ollama Cloud's OpenAI-compatible endpoint.
    # All Ollama fields are optional so the app still starts without them.
    # Gemini 2.5 models think by default (dynamic budget), which bills thought
    # tokens on top of the visible answer. This task is a 3-4 sentence reflection,
    # so thinking is off by default.
    #   0  = disabled       >0 = capped at that many thought tokens
    #  -1  = dynamic (model decides, i.e. the SDK's own default)
    gemini_thinking_budget: int = 0

    ai_provider: str = "gemini"
    # Left as None rather than a placeholder string on purpose: _generate_ollama
    # guards with `if not settings.ollama_api_key: raise RuntimeError(...)`, so
    # None fails fast with a clear message naming the missing variable. A dummy
    # string would pass that guard and instead surface as a 401 from the API
    # mid-request. Set OLLAMA_API_KEY in .env to use AI_PROVIDER=ollama.
    ollama_api_key: str | None = None
    ollama_model: str = "gpt-oss:20b"
    ollama_base_url: str = "https://ollama.com/v1"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
