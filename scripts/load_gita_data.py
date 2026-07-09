from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import engine
from app.models import Entry, Section, Source, Translation

DATA_PATH = Path(r"C:\Users\nayya\OneDrive\Documents\Practice Code\Geeta\Get All Sloks\bhagavad_gita_full.json")
SOURCE_SLUG = "bhagavad_gita"
IGNORED_KEYS = {"_id", "chapter", "verse", "slok", "transliteration"}


def load_json(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, list):
        raise ValueError("Expected the JSON file to contain a top-level list of verses")
    if not data:
        raise ValueError("The JSON file does not contain any verses")
    return data


def infer_language_and_type(field_name: str) -> tuple[str, str] | None:
    key = field_name.lower()
    mapping = {
        "et": ("en", "translation"),
        "ht": ("hi", "translation"),
        "ec": ("en", "commentary"),
        "hc": ("hi", "commentary"),
        "sc": ("sa", "commentary"),
    }
    return mapping.get(key)


def extract_translation_rows(verse: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    for field_name, value in verse.items():
        if field_name in IGNORED_KEYS or not isinstance(value, dict):
            continue
        if not isinstance(value, dict) or "author" not in value:
            continue

        author = value.get("author")
        if not isinstance(author, str) or not author.strip():
            continue

        for subfield_name, subvalue in value.items():
            if subfield_name == "author":
                continue
            if not isinstance(subvalue, str):
                continue
            if not subvalue.strip():
                continue

            decoded = infer_language_and_type(subfield_name)
            if decoded is None:
                warnings.append(f"{verse.get('_id', '?')}:{field_name}:{subfield_name}")
                continue

            language, translation_type = decoded
            rows.append(
                {
                    "author": author,
                    "language": language,
                    "type": translation_type,
                    "text": subvalue,
                }
            )
    return rows, warnings


def print_structure_sample(records: list[dict[str, Any]]) -> None:
    first_item = records[0]
    print("JSON structure inspection")
    print("First item keys:")
    print("  " + ", ".join(first_item.keys()))
    sample = {
        "_id": first_item.get("_id"),
        "chapter": first_item.get("chapter"),
        "verse": first_item.get("verse"),
        "slok": first_item.get("slok"),
        "transliteration": first_item.get("transliteration"),
        "translator_keys": [
            key for key in first_item.keys() if key not in IGNORED_KEYS and isinstance(first_item.get(key), dict)
        ],
    }
    print("Sample entry:")
    print(json.dumps(sample, ensure_ascii=False, indent=2))


def build_counts(records: list[dict[str, Any]]) -> tuple[int, int, int, int]:
    sections = len({int(verse["chapter"]) for verse in records if isinstance(verse.get("chapter"), int)})
    entries = len(records)
    translations = 0
    for verse in records:
        rows, _ = extract_translation_rows(verse)
        translations += len(rows)
    return 1, sections, entries, translations


def main() -> None:
    parser = argparse.ArgumentParser(description="Load Bhagavad Gita data into the existing SQLModel schema")
    parser.add_argument("--dry-run", action="store_true", help="Parse and validate the JSON without writing to the database")
    args = parser.parse_args()

    records = load_json(DATA_PATH)
    print_structure_sample(records)
    print(f"Loaded {len(records)} verses from {DATA_PATH}")

    if args.dry_run:
        source_count, section_count, entry_count, translation_count = build_counts(records)
        sample_rows: list[dict[str, Any]] = []
        warnings: list[str] = []
        for verse in records:
            rows, verse_warnings = extract_translation_rows(verse)
            warnings.extend(verse_warnings)
            for row in rows:
                if len(sample_rows) < 3:
                    sample_rows.append(row)

        print("Dry run: would insert")
        print(f"  Sources: {source_count}")
        print(f"  Sections: {section_count}")
        print(f"  Entries: {entry_count}")
        print(f"  Translations: {translation_count}")
        if warnings:
            print("Unrecognized translator field names:")
            for warning in warnings[:10]:
                print(f"  - {warning}")
        print("Sample parsed rows:")
        for row in sample_rows:
            print(
                f"  - author={row['author']}, language={row['language']}, type={row['type']}, text_length={len(row['text'])}"
            )
        return

    with Session(engine) as session:
        try:
            existing_source = session.exec(select(Source).where(Source.slug == SOURCE_SLUG)).first()
            if existing_source is not None:
                print(f"Source '{SOURCE_SLUG}' already exists; exiting without inserting anything.")
                return

            chapter_to_section_id: dict[int, int] = {}
            source = Source(
                slug=SOURCE_SLUG,
                title="Bhagavad Gita",
                description="701 verses across 18 chapters",
                unit_label="verse",
                total_units=701,
                original_language="sanskrit",
            )
            session.add(source)
            session.flush()

            chapters = sorted({int(verse["chapter"]) for verse in records if isinstance(verse.get("chapter"), int)})
            for chapter_number in chapters:
                section = Section(
                    source_id=source.id,
                    section_number=chapter_number,
                    title=f"Chapter {chapter_number}",
                    description=None,
                )
                session.add(section)
                session.flush()
                chapter_to_section_id[chapter_number] = section.id

            global_index = 0
            for verse_number, verse in enumerate(records, start=1):
                global_index += 1
                chapter_number = int(verse["chapter"])
                entry = Entry(
                    source_id=source.id,
                    section_id=chapter_to_section_id[chapter_number],
                    global_index=global_index,
                    chapter_number=chapter_number,
                    verse_number=int(verse["verse"]),
                    original_text=str(verse["slok"]),
                    transliteration=str(verse.get("transliteration") or ""),
                )
                session.add(entry)
                session.flush()

                translation_rows, _ = extract_translation_rows(verse)
                for translation_data in translation_rows:
                    translation = Translation(
                        entry_id=entry.id,
                        author=translation_data["author"],
                        language=translation_data["language"],
                        type=translation_data["type"],
                        text=translation_data["text"],
                    )
                    session.add(translation)

                if verse_number % 50 == 0:
                    print(f"Loaded {verse_number}/{len(records)} verses")

            session.commit()
            print("Insert completed successfully")
            print("Final counts:")
            print(f"  Sources inserted: 1")
            print(f"  Sections inserted: {len(chapter_to_section_id)}")
            print(f"  Entries inserted: {len(records)}")
            print(f"  Translations inserted: {sum(len(extract_translation_rows(verse)[0]) for verse in records)}")
        except Exception as exc:  # pragma: no cover - CLI safety
            print(f"Insert failed: {exc}")
            raise


if __name__ == "__main__":
    main()
