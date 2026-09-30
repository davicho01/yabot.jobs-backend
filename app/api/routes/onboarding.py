from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.onboarding import AiAccessRead, OnboardingRead
from app.services.onboarding import dismiss_onboarding, get_ai_access, get_onboarding, mark_ai_access_seen

router = APIRouter(tags=["onboarding"])


@router.get("/onboarding", response_model=OnboardingRead)
def read_onboarding(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> OnboardingRead:
    return get_onboarding(db, current_user)


@router.post("/onboarding/dismiss", response_model=OnboardingRead)
def dismiss(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> OnboardingRead:
    return dismiss_onboarding(db, current_user)


@router.post("/onboarding/ai-access-seen", response_model=OnboardingRead)
def ai_access_seen(
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> OnboardingRead:
    """Called when the AI access page opens — seeing the options is what
    completes the getting-started "Set up AI access" step for someone on the
    free trial. Idempotent."""
    return mark_ai_access_seen(db, current_user)


@router.get("/ai-access", response_model=AiAccessRead)
def read_ai_access(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> AiAccessRead:
    return get_ai_access(db, current_user)
