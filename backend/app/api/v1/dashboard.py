from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any, Optional
from app.services.cache import get_revenue_summary
from app.services.reservations import to_cents
from app.core.auth import authenticate_request as get_current_user

router = APIRouter()

@router.get("/dashboard/summary")
async def get_dashboard_summary(
    property_id: str,
    month: Optional[int] = None,
    year: Optional[int] = None,
    current_user: dict = Depends(get_current_user)
) -> Dict[str, Any]:

    # Never fall back to a shared/default tenant: that would mix clients' data.
    tenant_id = getattr(current_user, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=403, detail="No tenant associated with this user")
    if (month is None) != (year is None) or (month is not None and not 1 <= month <= 12):
        raise HTTPException(status_code=400, detail="Provide both a valid month (1-12) and year")

    revenue_data = await get_revenue_summary(property_id, tenant_id, month, year)

    # Exact 2-decimal value from the Decimal string (no binary float arithmetic on money)
    total_revenue = float(to_cents(revenue_data['total']))

    return {
        "property_id": revenue_data['property_id'],
        "total_revenue": total_revenue,
        "currency": revenue_data['currency'],
        "reservations_count": revenue_data['count']
    }
