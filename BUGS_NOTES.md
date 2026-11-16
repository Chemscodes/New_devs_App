# Debug report — Property Revenue Dashboard

Three bugs, all in the revenue path:

```
RevenueSummary.tsx -> GET /api/v1/dashboard/summary -> cache.py (Redis) -> reservations.py -> PostgreSQL
```

---

## Bug 1 — Cross-tenant data leak (Ocean Rentals seeing another company's revenue)

**File:** `backend/app/services/cache.py`

```python
cache_key = f"revenue:{property_id}"   # no tenant in the key
```

`prop-001` exists for **both** tenants — it is `Beach House Alpha` for tenant-a and
`Mountain Lodge Beta` for tenant-b. The `properties` primary key is `(id, tenant_id)`,
so a property id alone does not identify a property.

Whichever tenant queried first populated Redis; the other one read that entry for the
rest of the 5-minute TTL. That is why it was intermittent.

**Fix:** `revenue:{tenant_id}:{property_id}:{period}`.

Two related holes closed at the same time:
- `dashboard.py` fell back to a literal `"default_tenant"` when the user had no tenant,
  putting unrelated users in one shared bucket. It now returns **403**.
- The offline fallback table was keyed by `property_id` alone — the same collision.
  It is now keyed by `(tenant_id, property_id)`.

## Bug 2 — Wrong March totals (Sunset Properties)

**Files:** `backend/app/core/database_pool.py`, `backend/app/services/reservations.py`

**a) The connection pool never initialized.** It built its URL from
`settings.supabase_db_user` / `_password` / `_host` / `_port` / `_name`, none of which
exist in `config.py` (only `database_url` does). Every request therefore raised, hit the
`except`, and silently returned the **hard-coded mock values** further down the file.
The API never talked to Postgres at all.

Also wrong in that file: `poolclass=QueuePool` is incompatible with an async engine,
`get_session` was `async def` but used as `async with`, and a fresh `DatabasePool()` —
a new engine — was constructed on every single request.

**b) Month boundaries were computed in UTC, not in the property's timezone.**
`res-tz-1` is stored at `2024-02-29 23:30:00+00`. Beach House Alpha is in `Europe/Paris`,
where that instant is **2024-03-01 00:30** — a March booking. Naive UTC bounds pushed it
into February and dropped it from the March total.

Verified against the seeded database:

| March 2024, prop-001, tenant-a | Total | Reservations |
|---|---|---|
| UTC bounds (buggy) | 1000.000 | 3 |
| Property-timezone bounds (fixed) | **2250.000** | **4** |

Note the mock table said `prop-001: {'total': '1000.00', 'count': 3}` — exactly the buggy
figure, which is why the discrepancy looked plausible instead of obviously broken.

**Fix:** join `properties` and derive the bounds with
`make_timestamptz(:y, :m, 1, 0, 0, 0, p.timezone)`. `calculate_monthly_revenue` was a stub
returning `Decimal('0')` and taking no `tenant_id`; it is now real, and
`/dashboard/summary` accepts optional `month` / `year`.

## Bug 3 — Totals off by a few cents (finance team)

**Files:** `backend/app/api/v1/dashboard.py`, `backend/app/services/reservations.py`

`total_amount` is `NUMERIC(10, 3)` — deliberately sub-cent (`333.333`, `333.333`, `333.334`).
The backend cast it to `float` and let the **browser** round:
`Math.round(data.total_revenue * 100) / 100`.

Two mistakes: money through binary floating point, and rounding in the wrong place.

```
SUM(total_amount)        = 1000.000   <- exact
SUM(ROUND(amount, 2))    =  999.99    <- rounded per row
ROUND(SUM(amount), 2)    = 1000.00    <- rounded once, correct
```

**Fix:** sum exactly, then `to_cents()` — `Decimal` with `ROUND_HALF_UP` — **once**,
server-side. No float arithmetic on money anywhere in the path.

---

## Verifying

```bash
docker-compose up --build
```

- `sunset@propertyflow.com` / `client_a_2024` → Beach House Alpha: **2250.00**, 4 bookings
- `ocean@propertyflow.com` / `client_b_2024` → `prop-001` no longer shows Sunset's figures
  (Mountain Lodge Beta has no reservations in the seed); Lakeside **1776.50**, Urban Loft **3256.00**
- `GET /api/v1/dashboard/summary?property_id=prop-001&month=3&year=2024` as Sunset → 2250.00
- Redis now holds two distinct keys: `revenue:tenant-a:prop-001:all` and `revenue:tenant-b:prop-001:all`

The figures above were verified by loading `database/schema.sql` + `database/seed.sql` into a
real PostgreSQL instance and running both the old and the new query.
