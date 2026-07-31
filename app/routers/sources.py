from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from app import models, schemas
from app.database import get_session

router = APIRouter(prefix="/sources", tags=["sources"])


@router.get("", response_model=list[schemas.SourceSummary])
def list_sources(session: Session = Depends(get_session)) -> list[models.Source]:
    """Catalogue of available sources for the source selector.

    Unauthenticated on purpose: this is non-sensitive catalogue metadata that the
    oracle page needs on first paint, and requiring a bearer token here would
    couple rendering the selector to session hydration.
    """
    return list(session.exec(select(models.Source).order_by(models.Source.id)).all())
