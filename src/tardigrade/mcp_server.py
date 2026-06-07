"""MCP server exposing the e-commerce tools the agent uses. Registered with
the TrueFoundry MCP Gateway as a backend. Tools intentionally have terse
descriptions because the gateway adds the auth/guardrail layer on top.

In-memory order store — for the demo we seed it with a handful of orders
covering shipped / in-transit / delivered / refunded states. Real deployments
would back this with a DB."""

from __future__ import annotations

from datetime import date, timedelta

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("tardigrade-tools")


_TODAY = date(2026, 6, 7)


def _d(days_ago: int) -> str:
    return (_TODAY - timedelta(days=days_ago)).isoformat()


ORDERS: dict[str, dict] = {
    "TGS-1001": {
        "customer": "alex@example.com",
        "items": [{"sku": "TSHIRT-BLK-L", "name": "Black tee (L)", "qty": 1, "price_usd": 24.00}],
        "total_usd": 24.00,
        "placed_on": _d(8),
        "status": "delivered",
        "delivered_on": _d(2),
        "carrier": "UPS",
        "tracking": "1Z999AA10123456784",
    },
    "TGS-1002": {
        "customer": "sam@example.com",
        "items": [{"sku": "SNEAKER-WHT-10", "name": "Court sneakers (10)", "qty": 1, "price_usd": 89.00}],
        "total_usd": 89.00,
        "placed_on": _d(3),
        "status": "in_transit",
        "carrier": "FedEx",
        "tracking": "612345678901",
        "eta": _d(-2),
    },
    "TGS-1003": {
        "customer": "jamie@example.com",
        "items": [{"sku": "HOODIE-GRY-M", "name": "Grey hoodie (M)", "qty": 2, "price_usd": 49.00}],
        "total_usd": 98.00,
        "placed_on": _d(1),
        "status": "processing",
        "carrier": None,
        "tracking": None,
    },
    "TGS-1004": {
        "customer": "robin@example.com",
        "items": [{"sku": "BAG-CANVAS-OS", "name": "Canvas tote", "qty": 1, "price_usd": 32.00}],
        "total_usd": 32.00,
        "placed_on": _d(20),
        "status": "refunded",
        "refunded_on": _d(10),
        "carrier": "USPS",
        "tracking": "9400110200881234567890",
    },
}


@mcp.tool()
def order_lookup(order_id: str) -> dict:
    """Look up an order by ID. Returns the full order record with items,
    customer email, status, and tracking info if shipped."""
    order = ORDERS.get(order_id.upper())
    if not order:
        return {"error": "not_found", "order_id": order_id}
    return {"order_id": order_id.upper(), **order}


@mcp.tool()
def initiate_refund(order_id: str, amount_usd: float, reason: str = "") -> dict:
    """Initiate a refund for an order. Amount must be ≤ order total.
    Returns a refund ticket id."""
    order = ORDERS.get(order_id.upper())
    if not order:
        return {"error": "not_found", "order_id": order_id}
    if amount_usd <= 0 or amount_usd > order["total_usd"]:
        return {"error": "invalid_amount",
                "max_allowed_usd": order["total_usd"], "requested_usd": amount_usd}
    if order["status"] == "refunded":
        return {"error": "already_refunded", "order_id": order_id.upper()}
    return {
        "refund_ticket": f"RF-{order_id.upper()}-{int(amount_usd * 100)}",
        "order_id": order_id.upper(),
        "amount_usd": round(amount_usd, 2),
        "estimated_clearance_days": 7,
        "method": "original_payment",
        "reason": reason or "customer_request",
    }


@mcp.tool()
def track_shipment(order_id: str) -> dict:
    """Get shipment tracking info for an order. Returns carrier, tracking
    number, and current status."""
    order = ORDERS.get(order_id.upper())
    if not order:
        return {"error": "not_found", "order_id": order_id}
    if not order.get("tracking"):
        return {"order_id": order_id.upper(), "status": order["status"],
                "tracking": None, "message": "order has not shipped yet"}
    return {
        "order_id": order_id.upper(),
        "status": order["status"],
        "carrier": order["carrier"],
        "tracking": order["tracking"],
        "eta": order.get("eta"),
        "delivered_on": order.get("delivered_on"),
    }


if __name__ == "__main__":
    # Run as a stdio MCP server for local testing. In production this is
    # exposed via Streamable-HTTP and registered with TF MCP Gateway.
    mcp.run()
