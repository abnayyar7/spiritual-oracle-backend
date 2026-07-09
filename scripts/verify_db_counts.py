import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text
from app.database import engine

queries = [
    ('sources', 'SELECT COUNT(*) FROM public.sources'),
    ('sections', 'SELECT COUNT(*) FROM public.sections'),
    ('entries', 'SELECT COUNT(*) FROM public.entries'),
    ('translations', 'SELECT COUNT(*) FROM public.translations'),
]

with engine.connect() as conn:
    for name, sql in queries:
        result = conn.execute(text(sql)).scalar_one()
        print(f'{name}: {result}')
