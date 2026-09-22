import logging
import sys
import time
import traceback
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from google import genai
from google.genai import errors, types
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

from app import auth, database, models, schemas
from app.config import settings
from app.database import get_session

router = APIRouter(prefix="/ask", tags=["ask"])

# Preferred English translators per source, best first. A source that is not
# listed still works: selection falls back to any usable English translation,
# so adding a source needs no change here.
TRANSLATION_PREFERENCES: dict[str, list[str]] = {
    "bhagavad_gita": [
        "Swami Sivananda",
        "Swami Adidevananda",
        "Shri Purohit Swami",
        "Swami Gambirananda",
        "Dr.S.Sankaranarayan",
        "A.C. Bhaktivedanta Swami Prabhupada",
    ],
    "ramcharitmanas": [
        "F.S. Growse",
    ],
}

# Returned verbatim when an entry has no English translation. No model is called,
# so the wording is fixed rather than generated.
NO_TRANSLATION_TEMPLATE = (
    "I hear that you're going through something meaningful, and I want to be "
    "honest with you: an English translation isn't available yet for this "
    "particular {unit} from the {title}. Please try "
    "drawing again to receive guidance from a verse we can fully share with you."
)

# Recorded in user_queries.llm_model_used so templated draws stay distinguishable
# from real model calls when querying usage.
NO_TRANSLATION_MODEL_LABEL = "template"

def _is_placeholder_translation(text: str) -> bool:
    stripped = text.strip()
    return len(stripped) < 15 or "see comment under" in stripped.lower()


def select_translation(
    entry: models.Entry, source_slug: str
) -> tuple[str | None, str | None]:
    """Pick the best English translation for an entry, for any source.

    Returns (text, author), or (None, None) when the entry has no usable English
    translation. None means "none exists" — callers must not substitute the
    original text, which the model would read as a translation and paraphrase.
    """
    usable = [
        t
        for t in entry.translations
        if t.language == "en"
        and t.type == "translation"
        and t.text
        and not _is_placeholder_translation(t.text)
    ]
    if not usable:
        return None, None

    for preferred_author in TRANSLATION_PREFERENCES.get(source_slug, []):
        for candidate in usable:
            if candidate.author == preferred_author:
                return candidate.text, candidate.author

    return usable[0].text, usable[0].author


def build_system_instruction(source: models.Source) -> str:
    """Source-aware guide prompt for entries that have an English translation.

    Only called on that path: entries without a translation never reach the model,
    they get NO_TRANSLATION_TEMPLATE instead.
    """
    unit = source.unit_label
    return (
        "You are a wise spiritual guide offering personal counsel, speaking directly and warmly to someone "
        f"who has come to you with a question and received a {unit} from the {source.title} as their "
        "guidance. Speak to them directly, as a teacher would to a student they care about. "
        f"In 3-4 sentences, connect the wisdom of the {unit} to their question — even if the connection "
        "requires interpretation, always find genuine relevance. Do not use bullet points or lists. Do not "
        f"cite other {unit}s or chapters by number. Do not say the {unit} \"doesn't address\" their question. "
        "Write with warmth, not analysis. Always respond in English, regardless of the language of the "
        f"{unit} or translation provided. You are not a translator. Do not create your own translation of "
        f"the original {source.original_language or 'source-language'} text. Only comment on the meaning "
        "using the translation text provided to you. "
        "If the user's question asks for a specific prediction about timing, dates, or future events (such as "
        "when they will get married, when they will get a job, exam results, lottery numbers, or any yes/no "
        "prediction about the future), do not attempt to answer it as a prediction. Instead, gently acknowledge "
        "what they're seeking, then redirect toward the verse's wisdom about patience, trust, acceptance, or "
        "finding peace with uncertainty. Never say you cannot help — always offer the verse's perspective on "
        "their underlying emotion rather than the specific prediction they asked for."
    )

logger = logging.getLogger("oracle.ask")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s [ask] %(message)s"))
    logger.addHandler(_handler)
    logger.propagate = False


# Transient upstream failures worth a second go: 503 when Gemini is briefly
# overloaded, 429 when the rate limit is hit. Note these arrive as DIFFERENT
# exception classes -- the SDK raises ServerError for 5xx and ClientError for
# 4xx -- so both are caught via their shared APIError base and separated by
# .code. Catching ServerError alone would never retry a 429.
GEMINI_RETRY_CODES = (429, 503)
# Ollama Cloud sits behind a proxy, so an overloaded backend can surface as a
# gateway code rather than 503. Transport errors are retried too: the sweep runs
# hit bare connection resets (WinError 10054) that never became an HTTP status.
OLLAMA_RETRY_CODES = (429, 502, 503, 504)
# Waits BEFORE retry 1, 2 and 3; len() is therefore the retry budget. Shared so
# both providers behave identically from the caller's point of view.
RETRY_BACKOFF_SECONDS = (2, 4, 8)
GEMINI_BACKOFF_SECONDS = RETRY_BACKOFF_SECONDS   # kept for existing references
# Bound every request so a hung socket fails fast instead of holding a worker.
# HttpOptions.timeout is in MILLISECONDS.
GEMINI_TIMEOUT_MS = 30_000


def create_gemini_client() -> genai.Client:
    if not settings.gemini_api_key:
        raise RuntimeError("GEMINI_API_KEY must be set to use Gemini.")
    return genai.Client(
        api_key=settings.gemini_api_key,
        http_options=types.HttpOptions(timeout=GEMINI_TIMEOUT_MS),
    )


def _generate_gemini(system_instruction: str, contents: str) -> tuple[str, str]:
    client = create_gemini_client()
    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.9,
    )
    # Gemini 2.5 thinks by default and bills the thought tokens. A negative
    # budget means "leave it to the model" (the SDK default); anything >= 0 is
    # passed through, with 0 disabling thinking outright.
    if settings.gemini_thinking_budget >= 0:
        config.thinking_config = types.ThinkingConfig(
            thinking_budget=settings.gemini_thinking_budget
        )

    attempts = len(GEMINI_BACKOFF_SECONDS) + 1        # 1 initial + 3 retries
    for attempt in range(1, attempts + 1):
        try:
            response = client.models.generate_content(
                model=settings.ai_model,
                contents=contents,
                config=config,
            )
            break
        except errors.APIError as exc:
            code = getattr(exc, "code", None)
            retryable = code in GEMINI_RETRY_CODES
            if not retryable or attempt == attempts:
                logger.warning(
                    "gemini call failed | attempt=%s/%s code=%s retryable=%s "
                    "giving_up=True error=%s",
                    attempt, attempts, code, retryable, exc,
                )
                raise
            wait = GEMINI_BACKOFF_SECONDS[attempt - 1]
            logger.warning(
                "gemini %s, retrying | attempt=%s/%s waiting=%ss error=%s",
                code, attempt, attempts, wait, exc,
            )
            time.sleep(wait)
    usage = getattr(response, "usage_metadata", None)
    if usage is not None:
        logger.info(
            "gemini usage | prompt=%s output=%s thoughts=%s total=%s budget=%s",
            usage.prompt_token_count, usage.candidates_token_count,
            usage.thoughts_token_count, usage.total_token_count,
            settings.gemini_thinking_budget,
        )
    return (response.text or ""), settings.ai_model


def _generate_ollama(system_instruction: str, contents: str) -> tuple[str, str]:
    """Ollama Cloud speaks the OpenAI chat-completions shape, so the system
    instruction becomes a system message rather than a separate config field."""
    if not settings.ollama_api_key:
        raise RuntimeError("OLLAMA_API_KEY must be set to use AI_PROVIDER=ollama.")
    url = settings.ollama_base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": settings.ollama_model,
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": contents},
        ],
        "temperature": 0.9,
        "stream": False,
    }
    attempts = len(RETRY_BACKOFF_SECONDS) + 1        # 1 initial + 3 retries
    for attempt in range(1, attempts + 1):
        try:
            with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0)) as client:
                resp = client.post(
                    url,
                    json=payload,
                    headers={"Authorization": f"Bearer {settings.ollama_api_key}",
                             "Content-Type": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
            break
        except (httpx.HTTPStatusError, httpx.TransportError) as exc:
            # HTTPStatusError carries a status; TransportError (reset, timeout,
            # DNS) has none but is transient by nature, so it is always retried.
            code = (exc.response.status_code
                    if isinstance(exc, httpx.HTTPStatusError) else None)
            retryable = code in OLLAMA_RETRY_CODES if code is not None else True
            if not retryable or attempt == attempts:
                logger.warning(
                    "ollama call failed | attempt=%s/%s code=%s retryable=%s "
                    "giving_up=True error=%s",
                    attempt, attempts, code, retryable, exc,
                )
                raise
            wait = RETRY_BACKOFF_SECONDS[attempt - 1]
            logger.warning(
                "ollama %s, retrying | attempt=%s/%s waiting=%ss error=%s",
                code or type(exc).__name__, attempt, attempts, wait, exc,
            )
            time.sleep(wait)
    choices = data.get("choices") or []
    text_out = ""
    if choices:
        text_out = (choices[0].get("message") or {}).get("content") or ""
    return text_out.strip(), settings.ollama_model


def generate_answer(system_instruction: str, contents: str) -> tuple[str, str]:
    """-> (answer, model_label). Dispatches on settings.ai_provider.

    Default stays 'gemini' so existing behaviour is unchanged; set
    AI_PROVIDER=ollama in .env to route generation to Ollama Cloud.
    """
    provider = (settings.ai_provider or "gemini").strip().lower()
    if provider == "ollama":
        return _generate_ollama(system_instruction, contents)
    if provider != "gemini":
        raise RuntimeError(f"Unknown AI_PROVIDER {provider!r}; expected "
                           f"'gemini' or 'ollama'.")
    return _generate_gemini(system_instruction, contents)


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
        "enforce_free_tier | questions_used=%s max_free_queries=%s",
        profile.questions_used, settings.max_free_queries,
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
    # Catch-all error handler: logs unexpected exceptions and returns a clean 500
    # instead of a raw traceback.
    try:
        profile = get_or_create_profile(session, current_user)
        enforce_free_tier(profile)

        requested_slug = (request.source_slug or "").strip()
        source = session.exec(
            select(models.Source).where(models.Source.slug == requested_slug)
        ).first()
        if not source:
            # Any slug present in `sources` is valid, so report the real list
            # rather than a hardcoded one.
            available = sorted(session.exec(select(models.Source.slug)).all())
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=(
                    f"Unknown source_slug {requested_slug!r}. "
                    f"Available: {', '.join(available) or 'none loaded'}."
                ),
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

        translation_text, selected_translation_author = select_translation(entry, source.slug)
        has_translation = translation_text is not None

        logger.info(
            "translation debug | source=%s entry_id=%s selected_author=%s "
            "has_translation=%s translation_text_preview=%r",
            source.slug, entry.id, selected_translation_author,
            has_translation, (translation_text or "")[:60],
        )

        if not has_translation:
            # Nothing for the model to interpret, so skip the round-trip entirely:
            # a fixed message is faster, free, and can't drift into paraphrasing
            # the untranslated original. Still counts as a draw (logged below).
            answer = NO_TRANSLATION_TEMPLATE.format(
                unit=source.unit_label, title=source.title
            )
            model_used = NO_TRANSLATION_MODEL_LABEL
            logger.info(
                "no translation | entry_id=%s -> template response, Gemini not called",
                entry.id,
            )
        else:
            system_instruction = build_system_instruction(source)

            prompt_parts = [f"{source.unit_label.capitalize()}: {entry.original_text}"]
            if entry.transliteration and entry.transliteration.strip():
                prompt_parts.append(f"Transliteration: {entry.transliteration}")
            prompt_parts.append(f"Translation: {translation_text}")
            prompt_parts.append(f"Their question: {request.question}")
            contents = "\n".join(prompt_parts)

            provider = (settings.ai_provider or "gemini").strip().lower()
            model_label = (settings.ollama_model if provider == "ollama"
                           else settings.ai_model)
            logger.info("calling %s | model=%s", provider, model_label)
            try:
                answer, model_used = generate_answer(system_instruction, contents)
            except Exception as exc:
                logger.info("provider=%s model=%s call FAILED | error=%s",
                            provider, model_label, exc)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="The AI service is temporarily unavailable. Please try again.",
                )

            logger.info("model=%s response=%r", model_used, answer)
            if not answer:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="The AI service returned no answer.",
                )

        profile.questions_used += 1
        user_query = models.UserQuery(
            user_id=UUID(current_user),
            source_id=source.id,
            user_question=request.question,
            user_number=request.number,
            resolved_global_index=global_index,
            generated_takeaway=answer,
            llm_model_used=model_used,
        )
        session.add(user_query)
        session.add(profile)

        try:
            session.commit()
            session.refresh(user_query)
        except SQLAlchemyError as exc:
            session.rollback()
            # The HTTPException below is re-raised by `except HTTPException` and so
            # never reaches traceback.print_exc(); log here or the error is lost.
            logger.error("commit FAILED | entry_id=%s error=%s", entry.id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred while saving your request. Please try again.",
            )

        return schemas.AskResponse(
            answer=answer,
            model=model_used,
            free_queries_used=profile.questions_used,
            entry=entry,
            selected_translation=translation_text,
            selected_translation_author=selected_translation_author,
        )
    except HTTPException:
        raise
    except Exception as exc:
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred. Please try again.",
        )
