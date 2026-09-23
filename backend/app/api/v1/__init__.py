from fastapi import APIRouter

from app.api.v1 import bulk_jobs, documents, health, jobs, journeys, reference

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(journeys.router, tags=["journeys"])
router.include_router(jobs.router, tags=["jobs"])
router.include_router(documents.router, tags=["documents"])
router.include_router(bulk_jobs.router, tags=["bulk"])
router.include_router(reference.router, tags=["reference"])
