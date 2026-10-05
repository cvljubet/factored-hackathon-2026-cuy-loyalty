from fastapi import APIRouter

router = APIRouter()


# Unauthenticated liveness check for the load balancer and ECS. It touches no
# dependencies (Cognito, data stores, models), so it reflects only that the process serves.
@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
