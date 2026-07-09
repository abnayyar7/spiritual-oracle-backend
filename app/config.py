from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    supabase_project_url: str
    gemini_api_key: str
    ai_model: str = "gemini-2.5-flash"
    max_free_queries: int = 1000

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
