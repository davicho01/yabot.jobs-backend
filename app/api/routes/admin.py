import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user, get_db
from app.models.job_url import JobPostingUrl
from app.schemas.admin import AdminDashboardRead, ScanDayCount
from app.services import admin as admin_service

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin_user)])


@router.get("/dashboard", response_model=AdminDashboardRead)
def get_dashboard(db: Session = Depends(get_db)) -> dict:
    return admin_service.get_dashboard_stats(db)


@router.get("/scans-by-day", response_model=list[ScanDayCount])
def get_scans_by_day(days: int = Query(90, ge=1, le=365), db: Session = Depends(get_db)) -> list[dict]:
    return admin_service.get_scans_by_day(db, days=days)


@router.delete("/listings/{url_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_listing(url_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    url_row = db.get(JobPostingUrl, url_id)
    if url_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found.")
    db.delete(url_row)
