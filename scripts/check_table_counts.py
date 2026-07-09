import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from app.database import engine

queries = [
    ('source', 'SELECT COUNT(*) FROM public.source'),
    ('sources', 'SELECT COUNT(*) FROM public.sources'),
    ('section', 'SELECT COUNT(*) FROM public.section'),
    ('sections', 'SELECT COUNT(*) FROM public.sections'),
    ('entry', 'SELECT COUNT(*) FROM public.entry'),
    ('entries', 'SELECT COUNT(*) FROM public.entries'),
    ('translation', 'SELECT COUNT(*) FROM public.translation'),
    ('translations', 'SELECT COUNT(*) FROM public.translations'),
    ('profile', 'SELECT COUNT(*) FROM public.profile'),
    ('profiles', 'SELECT COUNT(*) FROM public.profiles'),
    ('userquery', 'SELECT COUNT(*) FROM public.userquery'),
    ('user_queries', 'SELECT COUNT(*) FROM public.user_queries'),
]

with engine.connect() as conn:
    for name, sql in queries:
        try:
            result = conn.execute(text(sql)).scalar_one()
            print(f"{name}: {result}")
        except Exception as exc:
            print(f"{name}: ERROR {exc}")
