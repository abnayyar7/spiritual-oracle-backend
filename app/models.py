from datetime import datetime
from typing import List, Optional
from uuid import UUID

from sqlalchemy import BigInteger, Column, String
from sqlmodel import Field, Relationship, SQLModel


class Source(SQLModel, table=True):
    __tablename__ = "sources"
    id: Optional[int] = Field(default=None, primary_key=True)
    slug: str = Field(sa_column=Column(String, unique=True, nullable=False))
    title: str
    description: Optional[str] = None
    unit_label: str
    total_units: int
    original_language: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)

    sections: List["Section"] = Relationship(back_populates="source")
    entries: List["Entry"] = Relationship(back_populates="source")


class Section(SQLModel, table=True):
    __tablename__ = "sections"
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="sources.id")
    section_number: int
    title: str
    description: Optional[str] = None

    source: Optional[Source] = Relationship(back_populates="sections")
    entries: List["Entry"] = Relationship(back_populates="section")


class Entry(SQLModel, table=True):
    __tablename__ = "entries"
    id: Optional[int] = Field(default=None, primary_key=True)
    source_id: int = Field(foreign_key="sources.id")
    section_id: Optional[int] = Field(default=None, foreign_key="sections.id")
    global_index: int
    chapter_number: int
    verse_number: int
    original_text: str
    transliteration: Optional[str] = None

    source: Optional[Source] = Relationship(back_populates="entries")
    section: Optional[Section] = Relationship(back_populates="entries")
    translations: List["Translation"] = Relationship(back_populates="entry")


class Translation(SQLModel, table=True):
    __tablename__ = "translations"
    id: Optional[int] = Field(default=None, primary_key=True)
    entry_id: int = Field(foreign_key="entries.id")
    author: Optional[str] = None
    language: str
    type: str
    text: str

    entry: Optional[Entry] = Relationship(back_populates="translations")


class AuthUser(SQLModel, table=True):
    __tablename__ = "users"
    __table_args__ = {"schema": "auth"}
    id: UUID = Field(primary_key=True)


class Profile(SQLModel, table=True):
    __tablename__ = "profiles"
    id: UUID = Field(primary_key=True, foreign_key="auth.users.id")
    subscription_tier: str = Field(default="free")
    stripe_customer_id: Optional[str] = None
    questions_used: int = Field(default=0)
    questions_reset_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UserQuery(SQLModel, table=True):
    __tablename__ = "user_queries"
    id: Optional[int] = Field(default=None, sa_column=Column(BigInteger, primary_key=True, autoincrement=True))
    user_id: UUID = Field(foreign_key="auth.users.id")
    source_id: Optional[int] = Field(default=None, foreign_key="sources.id")
    user_question: str
    user_number: int
    resolved_global_index: Optional[int] = None
    generated_takeaway: str
    llm_model_used: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
