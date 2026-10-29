"""List every route a FastAPI app serves, including routers added with include_router.

FastAPI up to about 0.141 copies each included route into ``app.routes``. From
0.142 on, ``include_router`` adds one lazy entry per router instead, and that
entry has no ``path``, so ``{r.path for r in app.routes}`` stops seeing every
included route (the routes still work). The Mac Studio runs FastAPI 0.129.0 and
the buyer lock pins 0.162.0; ``route_methods`` gives the same table on both.

On FastAPI 0.142+ it reads ``fastapi.routing.iter_route_contexts``, the listing
FastAPI's own OpenAPI generator uses (it also covers websocket and
``include_in_schema=False`` routes). Then it checks the table against
``app.openapi()``: if a future FastAPI hides routes again, the tests fail here
instead of passing without checking anything.
"""
from __future__ import annotations

import fastapi.routing
from starlette.routing import Mount


def _add(table: dict[str, set[str]], route, path: str | None) -> None:
    if not path:
        return
    table.setdefault(path, set()).update(getattr(route, "methods", None) or ())
    if isinstance(route, Mount):
        for child in route.routes:
            _add(table, child, path.rstrip("/") + getattr(child, "path", ""))


def route_methods(app) -> dict[str, set[str]]:
    """Map each route path the app serves to its HTTP methods.

    Websocket routes and mounts map to an empty set. Paths are the route
    templates, e.g. "/api/schedules/{sched_id}".
    """
    table: dict[str, set[str]] = {}
    iter_contexts = getattr(fastapi.routing, "iter_route_contexts", None)
    if iter_contexts is not None:
        for ctx in iter_contexts(app.routes):
            _add(table, ctx.original_route, ctx.path)
    else:
        for route in app.routes:
            _add(table, route, getattr(route, "path", None))
    opaque = [type(r).__name__ for r in app.routes if not getattr(r, "path", None)]
    if opaque and iter_contexts is None:
        raise AssertionError(
            f"app.routes holds {len(opaque)} entries without a path ({sorted(set(opaque))}) and "
            "this FastAPI has no fastapi.routing.iter_route_contexts: update tests/route_table.py")
    missing = sorted(set(app.openapi().get("paths", {})) - set(table))
    if missing:
        raise AssertionError(
            f"tests/route_table.py misses {len(missing)} routes that app.openapi() lists, "
            f"e.g. {missing[:5]}: FastAPI changed how it stores included routes")
    return table
