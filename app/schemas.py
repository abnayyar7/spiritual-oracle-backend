from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class AskRequest(BaseModel):
    question: str
    number: int
    source_slug: str = "bhagavad_gita"


class AskResponse(BaseModel):
    answer: str
    model: str
    free_queries_used: int
    entry: "EntryOut"
    selected_translation: str
    selected_translation_author: str | None


class TranslationOut(BaseModel):
    id: int
    entry_id: int
    author: str | None
    language: str
    type: str
    text: str

    model_config = ConfigDict(from_attributes=True)


class EntryOut(BaseModel):
    id: int
    source_id: int
    section_id: int | None
    global_index: int
    chapter_number: int
    verse_number: int
    original_text: str
    transliteration: str | None
    translations: list[TranslationOut] = []

    model_config = ConfigDict(from_attributes=True)


class SectionOut(BaseModel):
    id: int
    source_id: int
    section_number: int
    title: str
    description: str | None
    entries: list[EntryOut] = []

    model_config = ConfigDict(from_attributes=True)


class SourceOut(BaseModel):
    id: int
    slug: str
    title: str
    description: str | None
    unit_label: str
    total_units: int
    original_language: str | None
    created_at: datetime
    sections: list[SectionOut] = []
    entries: list[EntryOut] = []

    model_config = ConfigDict(from_attributes=True)


class UserQueryOut(BaseModel):
    id: int
    user_id: UUID
    source_id: int | None
    user_question: str
    user_number: int
    resolved_global_index: int | None
    generated_takeaway: str
    llm_model_used: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProfileOut(BaseModel):
    id: UUID
    subscription_tier: str
    stripe_customer_id: str | None
    questions_used: int
    questions_reset_at: datetime | None
    created_at: datetime
    queries: list[UserQueryOut] = []

    model_config = ConfigDict(from_attributes=True)
