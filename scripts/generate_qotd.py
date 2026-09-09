#!/usr/bin/env python3
"""Generate Quote of the Day reflections for a 10-day buffer.

Ensures qotd_daily has 'ready' reflections for today through today+9.
Uses deterministic date→verse mapping: (days_since_epoch % 541) picks the
sequence_position in qotd_pool.

Usage:
    python scripts/generate_qotd.py [--dry-run] [--sample-only]

Options:
    --dry-run       Don't write to DB; show what would be generated.
    --sample-only   Generate just one sample reflection for inspection.
"""
import logging
import sys
from datetime import date, timedelta

from sqlalchemy import text
from sqlalchemy.orm import selectinload
from sqlmodel import Session, select

sys.path.insert(0, ".")
from app import models
from app.database import engine
from app.routers import oracle

logger = logging.getLogger("qotd")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler(sys.stdout)
    _handler.setFormatter(logging.Formatter("%(asctime)s [qotd] %(message)s"))
    logger.addHandler(_handler)
    logger.propagate = False

# Fixed epoch for deterministic date→verse mapping. All runs use this date
# to ensure the same verse appears on the same calendar date forever.
EPOCH_DATE = date(2026, 1, 1)
POOL_SIZE = 541


def days_since_epoch(d: date) -> int:
    """Count of days from EPOCH_DATE to d (inclusive of d)."""
    return (d - EPOCH_DATE).days


def get_verse_for_date(session: Session, d: date) -> tuple[int, int] | None:
    """-> (entry_id, sequence_position) for a date, or None if pool is empty."""
    days = days_since_epoch(d)
    seq_pos = (days % POOL_SIZE) + 1  # sequence_position is 1-indexed

    result = session.execute(
        text("SELECT entry_id FROM qotd_pool WHERE sequence_position = :seq_pos"),
        {"seq_pos": seq_pos}
    ).first()

    if result:
        return (result[0], seq_pos)
    return None


def build_qotd_system_instruction(source: models.Source) -> str:
    """System prompt for QOTD reflection (direct, substantive tone)."""
    unit = source.unit_label
    return (
        "Reflect directly on the wisdom in this passage. Write 2-3 concise declarative sentences "
        "that express what the teaching is, not commentary about the teaching. Begin with substance, "
        "not preamble. Do not address the reader ('you', 'your', 'us', 'our', 'dear'). "
        "Do not use meta-framing ('this verse teaches', 'this passage reminds us', 'the teaching reveals'). "
        "Do not mention that this is from a sacred text or refer to it as 'passage' or 'verse' or 'doha'. "
        "Draw only the substance, grounded strictly in what appears in the provided translation. "
        "Speak the truth of the passage in plain, direct language. No lists, no bullet points. "
        "Always respond in English. Do not invent names, characters, or images not in the source. "
        "Write with clarity and depth, not warmth or analysis."
    )


def generate_qotd_reflection(session: Session, entry: models.Entry, source: models.Source) -> str | None:
    """Generate a reflection for an entry. -> reflection text, or None on failure."""
    translation_text, author = oracle.select_translation(entry, source.slug)

    if not translation_text:
        logger.warning(
            "no usable translation | entry_id=%s author=%s",
            entry.id, author
        )
        return None

    system_instruction = build_qotd_system_instruction(source)

    prompt_parts = [f"{source.unit_label.capitalize()}: {entry.original_text}"]
    if entry.transliteration and entry.transliteration.strip():
        prompt_parts.append(f"Transliteration: {entry.transliteration}")
    prompt_parts.append(f"Translation: {translation_text}")
    contents = "\n".join(prompt_parts)

    try:
        logger.info("generating reflection | entry_id=%s", entry.id)
        answer, model_used = oracle.generate_answer(system_instruction, contents)
        logger.info(
            "generated reflection | entry_id=%s model=%s length=%d",
            entry.id, model_used, len(answer)
        )
        return answer
    except Exception as exc:
        logger.error("reflection generation failed | entry_id=%s error=%s", entry.id, exc)
        return None


def get_sources(session: Session) -> dict[int, models.Source]:
    """Fetch all sources by id."""
    return {s.id: s for s in session.exec(select(models.Source)).all()}


def dry_run_schedule(session: Session) -> None:
    """Show what dates and verses would be generated, without writing to DB."""
    print("\n=== DRY RUN: 10-DAY SCHEDULE ===\n")

    sources = get_sources(session)
    today = date.today()

    for offset in range(10):
        d = today + timedelta(days=offset)
        result = get_verse_for_date(session, d)

        if not result:
            print(f"{d.isoformat()}: ERROR - no verse found in pool")
            continue

        entry_id, seq_pos = result

        # Fetch entry to show chapter.verse
        entry = session.exec(
            select(models.Entry).where(models.Entry.id == entry_id)
        ).first()

        if entry:
            source = sources.get(entry.source_id)
            source_name = source.title if source else "unknown"
            print(f"{d.isoformat()}: seq_pos={seq_pos} entry_id={entry_id} "
                  f"({source_name} {entry.chapter_number}.{entry.verse_number})")
        else:
            print(f"{d.isoformat()}: seq_pos={seq_pos} entry_id={entry_id} (entry not found)")
    print()


def generate_one_sample(session: Session, d: date = None) -> None:
    """Generate and display one sample reflection."""
    if d is None:
        d = date.today()

    print(f"\n=== SAMPLE REFLECTION: {d.isoformat()} ===\n")

    result = get_verse_for_date(session, d)
    if not result:
        print("ERROR: no verse found for this date\n")
        return

    entry_id, seq_pos = result
    sources = get_sources(session)

    entry = session.exec(
        select(models.Entry)
        .where(models.Entry.id == entry_id)
        .options(selectinload(models.Entry.translations))
    ).first()

    if not entry:
        print(f"ERROR: entry_id={entry_id} not found\n")
        return

    source = sources.get(entry.source_id)
    if not source:
        print(f"ERROR: source_id={entry.source_id} not found\n")
        return

    print(f"Date: {d.isoformat()}")
    print(f"Sequence position: {seq_pos}")
    print(f"Entry ID: {entry.id}")
    print(f"Source: {source.title}")
    print(f"Reference: {entry.chapter_number}.{entry.verse_number}")
    orig_preview = entry.original_text[:100].encode('utf-8', errors='replace').decode('utf-8')
    print(f"Original text: {orig_preview}")

    translation_text, author = oracle.select_translation(entry, source.slug)
    if translation_text:
        print(f"Translator: {author}")
        trans_preview = translation_text[:150].encode('utf-8', errors='replace').decode('utf-8')
        print(f"Translation: {trans_preview}...")
    else:
        print("Translation: (none)")

    reflection = generate_qotd_reflection(session, entry, source)
    if reflection:
        print(f"\nGenerated Reflection:\n{reflection}")
    else:
        print("\nGenerated Reflection: (failed)\n")


def main():
    """Entry point."""
    dry_run = "--dry-run" in sys.argv
    sample_only = "--sample-only" in sys.argv
    force = "--force" in sys.argv

    with Session(engine) as session:
        if dry_run or sample_only:
            dry_run_schedule(session)
            generate_one_sample(session)
            if dry_run or sample_only:
                print("(dry-run mode: no changes written to DB)\n")
                return

        # Full run: generate and write 10 days worth
        print("\n=== POPULATING 10-DAY BUFFER ===\n")
        today = date.today()
        sources = get_sources(session)

        success_count = 0
        for offset in range(10):
            d = today + timedelta(days=offset)

            # Check if this date already has a 'ready' row
            existing = session.execute(
                text("""
                    SELECT id FROM qotd_daily
                    WHERE display_date = :d AND status = 'ready'
                """),
                {"d": d}
            ).first()

            if existing and not force:
                logger.info("skipping | display_date=%s (already ready)", d)
                continue

            # If --force, delete the existing row so it gets regenerated
            if existing and force:
                session.execute(text("DELETE FROM qotd_daily WHERE display_date = :d"), {"d": d})
                session.commit()
                logger.info("force regenerate | display_date=%s (deleted existing)", d)

            result = get_verse_for_date(session, d)
            if not result:
                logger.warning("no verse found for | display_date=%s", d)
                continue

            entry_id, seq_pos = result

            entry = session.exec(
                select(models.Entry)
                .where(models.Entry.id == entry_id)
                .options(selectinload(models.Entry.translations))
            ).first()

            if not entry:
                logger.warning("entry not found | entry_id=%s", entry_id)
                continue

            source = sources.get(entry.source_id)
            if not source:
                logger.warning("source not found | source_id=%s", entry.source_id)
                continue

            reflection = generate_qotd_reflection(session, entry, source)
            if not reflection:
                logger.warning(
                    "reflection generation failed, marking pending | "
                    "entry_id=%s display_date=%s",
                    entry_id, d
                )
                # Write with status='pending' so it can be retried
                session.execute(
                    text("""
                        INSERT INTO qotd_daily
                        (display_date, entry_id, status)
                        VALUES (:d, :entry_id, 'pending')
                        ON CONFLICT (display_date) DO NOTHING
                    """),
                    {"d": d, "entry_id": entry_id}
                )
                session.commit()
                continue

            # Write to DB
            logger.info(
                "writing ready | display_date=%s entry_id=%s",
                d, entry_id
            )
            session.execute(
                text("""
                    INSERT INTO qotd_daily
                    (display_date, entry_id, reflection_text, reflection_generated_at,
                     reflection_model, status)
                    VALUES (:d, :entry_id, :reflection, now(), :model, 'ready')
                    ON CONFLICT (display_date) DO NOTHING
                """),
                {
                    "d": d,
                    "entry_id": entry_id,
                    "reflection": reflection,
                    "model": oracle.settings.ai_model if oracle.settings.ai_provider == "gemini"
                            else oracle.settings.ollama_model,
                }
            )
            session.commit()
            success_count += 1

        logger.info("buffer population complete | generated=%d", success_count)


if __name__ == "__main__":
    main()
