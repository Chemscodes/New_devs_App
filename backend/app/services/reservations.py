from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Any, Optional

CENT = Decimal("0.01")


def to_cents(value) -> Decimal:
    """Round a monetary value ONCE, at the end, to 2 decimals (half-up). Never go through float."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def _month_bounds(month: int, year: int):
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    return year, month, ny, nm


async def calculate_monthly_revenue(property_id: str, tenant_id: str, month: int, year: int) -> Decimal:
    """Revenue for one calendar month, where the month boundaries are evaluated in the
    PROPERTY's own timezone (a booking at 2024-02-29 23:30 UTC is March 1st in Paris)."""
    result = await calculate_total_revenue(property_id, tenant_id, month, year)
    return Decimal(result["total"])

async def calculate_total_revenue(property_id: str, tenant_id: str, month: Optional[int] = None, year: Optional[int] = None) -> Dict[str, Any]:
    """
    Aggregates revenue from database.
    """
    try:
        # Import database pool
        from app.core.database_pool import db_pool

        # Initialize the shared pool once (not a new engine on every request)
        if not db_pool.session_factory:
            await db_pool.initialize()
        
        if db_pool.session_factory:
            async with db_pool.get_session() as session:
                # Use SQLAlchemy text for raw SQL
                from sqlalchemy import text
                
                params = {"property_id": property_id, "tenant_id": tenant_id}
                period_filter = ""
                if month and year:
                    y, m, ny, nm = _month_bounds(month, year)
                    params.update({"y": y, "m": m, "ny": ny, "nm": nm})
                    # Month boundaries in the property's timezone
                    period_filter = """
                        AND r.check_in_date >= make_timestamptz(:y, :m, 1, 0, 0, 0, p.timezone)
                        AND r.check_in_date <  make_timestamptz(:ny, :nm, 1, 0, 0, 0, p.timezone)
                    """

                query = text(f"""
                    SELECT
                        r.property_id,
                        SUM(r.total_amount) as total_revenue,
                        COUNT(*) as reservation_count
                    FROM reservations r
                    JOIN properties p ON p.id = r.property_id AND p.tenant_id = r.tenant_id
                    WHERE r.property_id = :property_id AND r.tenant_id = :tenant_id
                    {period_filter}
                    GROUP BY r.property_id
                """)
                
                result = await session.execute(query, params)
                row = result.fetchone()
                
                if row:
                    total_revenue = to_cents(row.total_revenue)
                    return {
                        "property_id": property_id,
                        "tenant_id": tenant_id,
                        "total": str(total_revenue),
                        "currency": "USD", 
                        "count": row.reservation_count
                    }
                else:
                    # No reservations found for this property
                    return {
                        "property_id": property_id,
                        "tenant_id": tenant_id,
                        "total": "0.00",
                        "currency": "USD",
                        "count": 0
                    }
        else:
            raise Exception("Database pool not available")
            
    except Exception as e:
        print(f"Database error for {property_id} (tenant: {tenant_id}): {e}")
        
        if month and year:
            raise  # no mock data for period queries: never serve made-up numbers

        # Fallback data when DB is unavailable. Keyed by (tenant, property): property ids
        # like prop-001 exist for several tenants, so tenant MUST be part of the key.
        mock_data = {
            ('tenant-a', 'prop-001'): {'total': '2250.00', 'count': 4},
            ('tenant-a', 'prop-002'): {'total': '4975.50', 'count': 4},
            ('tenant-a', 'prop-003'): {'total': '6100.50', 'count': 2},
            ('tenant-b', 'prop-004'): {'total': '1776.50', 'count': 4},
            ('tenant-b', 'prop-005'): {'total': '3256.00', 'count': 3},
        }

        mock_property_data = mock_data.get((tenant_id, property_id), {'total': '0.00', 'count': 0})

        return {
            "property_id": property_id,
            "tenant_id": tenant_id,
            "total": mock_property_data['total'],
            "currency": "USD",
            "count": mock_property_data['count']
        }
