import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user, get_db
from app.models.crawl_source import CrawlSource
from app.models.job_url import JobPostingUrl
from app.schemas.admin import AdminDashboardRead, CrawlSourceStatsRead
from app.services import admin as admin_service

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(get_current_admin_user)])


@router.get("/dashboard", response_model=AdminDashboardRead)
def get_dashboard(db: Session = Depends(get_db)) -> dict:
    return admin_service.get_dashboard_stats(db)


@router.get("/crawl-sources/{source_id}/stats", response_model=CrawlSourceStatsRead)
def get_crawl_source_stats(source_id: uuid.UUID, db: Session = Depends(get_db)) -> dict:
    source = db.get(CrawlSource, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Crawl source not found.")
    return admin_service.get_crawl_source_stats(db, source)


@router.delete("/listings/{url_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_listing(url_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    url_row = db.get(JobPostingUrl, url_id)
    if url_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Listing not found.")
    db.delete(url_row)
