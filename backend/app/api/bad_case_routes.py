from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.auth import AuthSubject, get_auth_subject
from app.db.session import get_db_session
from app.models.schemas import BadCase, BadCaseType, BadCaseStatus, BadCaseSeverity
from app.services.bad_case import bad_case_manager

router = APIRouter(prefix="/api/bad-cases", tags=["bad-cases"])


@router.post("/", response_model=BadCase)
async def create_bad_case(
    type: BadCaseType,
    description: str,
    subject: AuthSubject = Depends(get_auth_subject),
    session: AsyncSession = Depends(get_db_session),
    task_id: str = None,
    severity: BadCaseSeverity = BadCaseSeverity.medium,
    context: dict = None,
    metrics: dict = None,
):
    bad_case = await bad_case_manager.create_bad_case(
        type=type,
        description=description,
        session=session,
        task_id=task_id,
        severity=severity,
        context=context,
        metrics=metrics,
    )
    return bad_case


@router.get("/summary")
def get_summary(subject: AuthSubject = Depends(get_auth_subject)):
    return bad_case_manager.get_summary()


@router.get("/{bad_case_id}", response_model=BadCase)
async def get_bad_case(
    bad_case_id: str,
    subject: AuthSubject = Depends(get_auth_subject),
    session: AsyncSession = Depends(get_db_session),
):
    bad_case = await bad_case_manager.get_bad_case(bad_case_id, session)
    if not bad_case:
        raise HTTPException(status_code=404, detail="Bad case not found")
    return bad_case
