import logging
import sys
import traceback
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from google import genai
from google.genai import types
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app import auth, database, models, schemas
from app.config import settings
from app.database import get_session

router = APIRouter(prefix="/ask", tags=["ask"])

PRIORITY_TRANSLATION_AUTHORS = [
    "Swami Sivananda",
    "Swami Adidevananda",
    "Shri Purohit Swami",
    "Swami Gambirananda",
    "Dr.S.Sankaranarayan",
    "A.C. Bhaktivedanta Swami Prabhupada",
]


def _is_placeholder_translation(text: str) -> bool:
    stripped = text.strip()
    return len(stripped) < 15 or "see comment under" in stripped.lower()

logger = logging.getLogger("oracle.ask")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s [ask] %(message)s"))
    logger.addHandler(_handler)
    logger.propagate = False


def create_gemini_client() -> genai.Client:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY must be set to use Gemini.")
    return genai.Client(api_key=settings.gemini_api_key)


def get_or_create_profile(session: Session, user_id: str) -> models.Profile:
    profile = session.exec(select(models.Profile).where(models.Profile.id == UUID(user_id))).first()
    if profile:
        return profile

    profile = models.Profile(
        id=UUID(user_id),
        subscription_tier="free",
        questions_used=0,
    )
    session.add(profile)
    session.commit()
    session.refresh(profile)
    return profile


def enforce_free_tier(profile: models.Profile) -> None:
    logger.info(
        "enforce_free_tier | questions_used=%s max_free_queries=%s env_file=%s cwd=%s",
        profile.questions_used, settings.max_free_queries, settings.Config.env_file, __import__("os").getcwd(),
    )
    if profile.questions_used >= settings.max_free_queries:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="Free query limit reached. Upgrade your plan to continue.",
        )


@router.post("", response_model=schemas.AskResponse)
def ask_question(
    request: schemas.AskRequest,
    current_user: str = Depends(auth.get_current_user),
    session: Session = Depends(get_session),
) -> schemas.AskResponse:
    # TEMPORARY DEBUG WRAPPER: remove once the /ask 500s are diagnosed.
    try:
        profile = get_or_create_profile(session, current_user)
        enforce_free_tier(profile)

        source = session.exec(
            select(models.Source).where(models.Source.slug == request.source_slug)
        ).first()
        if not source:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Source slug not found.",
            )

        if source.total_units <= 0:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Source total_units must be a positive integer.",
            )

        if request.number < 1 or request.number > source.total_units:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Number must be between 1 and {source.total_units}.",
            )

        global_index = request.number
        entry = session.exec(
            select(models.Entry)
            .where(
                models.Entry.source_id == source.id,
                models.Entry.global_index == global_index,
            )
            .options(selectinload(models.Entry.translations))
        ).first()

        if not entry:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Entry not found for the requested number.",
            )

        translation_text = entry.original_text
        for priority_author in PRIORITY_TRANSLATION_AUTHORS:
            candidate = next(
                (
                    t
                    for t in entry.translations
                    if t.author == priority_author and t.language == "en" and t.type == "translation"
                ),
                None,
            )
            if candidate is not None and not _is_placeholder_translation(candidate.text):
                translation_text = candidate.text
                break
        else:
            fallback_translation = next(
                (
                    t
                    for t in entry.translations
                    if t.language == "en" and t.type == "translation" and not _is_placeholder_translation(t.text)
                ),
                None,
            )
            translation_text = fallback_translation.text if fallback_translation else entry.original_text

        system_instruction = (
            "You are a wise spiritual guide offering personal counsel, speaking directly and warmly to someone "
            "who has come to you with a question and received a verse from the Bhagavad Gita as their guidance. "
            "Speak to them directly, as a teacher would to a student they care about. "
            "In 3-4 sentences, connect the wisdom of the verse to their question — even if the connection requires "
            "interpretation, always find genuine relevance. Do not use bullet points or lists. Do not cite other "
            "verses or chapters by number. Do not say the verse \"doesn't address\" their question. Write with "
            "warmth, not analysis. Always respond in English, regardless of the language of the verse or "
            "translation provided. You are not a translator. Do not create your own translation of the "
            "Sanskrit verse. Only comment on the meaning using the translation text provided to you. If no "
            "English translation is available and you are only given the original Sanskrit, acknowledge that "
            "plainly rather than inventing a translation."
        )
        contents = (
            f"Verse: {entry.original_text}\n"
            f"Translation: {translation_text}\n"
            f"Their question: {request.question}"
        )

        client = create_gemini_client()
        logger.info("calling Gemini | model=%s", settings.ai_model)
        try:
            response = client.models.generate_content(
                model=settings.ai_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_instruction,
                    temperature=0.9,
                ),
            )
        except Exception as exc:
            logger.info("model=%s call FAILED | error=%s", settings.ai_model, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Gemini generation failed: {exc}",
            )

        answer = response.text or ""
        logger.info("model=%s response=%r", settings.ai_model, answer)
        if not answer:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Gemini returned no answer.",
            )

        profile.questions_used += 1
        user_query = models.UserQuery(
            user_id=UUID(current_user),
            source_id=source.id,
            user_question=request.question,
            user_number=request.number,
            resolved_global_index=global_index,
            generated_takeaway=answer,
            llm_model_used=settings.ai_model,
        )
        session.add(user_query)
        session.add(profile)

        try:
            session.commit()
            session.refresh(user_query)
        except SQLAlchemyError as exc:
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Database error: {exc}",
            )

        return schemas.AskResponse(
            answer=answer,
            model=settings.ai_model,
            free_queries_used=profile.questions_used,
            entry=entry,
        )
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unhandled /ask error: {exc}",
        )
