import sqlite3
from datetime import date, timedelta

from loguru import logger

import service

__all__ = ["seed_if_empty"]

_JsonScalar = str | int | float | bool | None
_Row = dict[str, _JsonScalar]

_CUSTOMERS = (
    "Northwind Office Supply",
    "Fabrikam Facilities",
    "Blue Yonder Logistics",
    "A. Datum Hospitality",
    "Tailspin Industrial",
    "Woodgrove Medical Group",
    "Fourth Coffee Roasters",
    "Contoso Retail Group",
    "Litware Hardware",
    "Proseware Labs",
    "Adventure Works Distribution",
    "Alpine Ski House",
    "Graphic Design Institute",
    "City Power & Light",
    "Coho Vineyard Supply",
    "Wide World Importers",
    "Humongous Insurance",
    "Lucerne Publishing",
    "Margie's Travel",
    "VanArsdel Manufacturing",
)

_REGIONS = ("Northeast", "Southeast", "Midwest", "Southwest", "West", "Canada")
_SALES_REPS = (
    "Amara Singh",
    "Ben Torres",
    "Chloe Martin",
    "Daan de Vries",
    "Elena Petrova",
    "Fatima Khan",
    "Noah Williams",
    "Sofia Rossi",
)
_STATUSES = (
    "open",
    "shipped",
    "delivered",
    "open",
    "delivered",
    "cancelled",
    "shipped",
    "open",
)

_PRODUCTS: tuple[tuple[str, str, float], ...] = (
    ("PKG-100", "Corrugated shipping cartons, medium", 1.85),
    ("PKG-210", "Padded mailers, case of 250", 54.0),
    ("LBL-330", "Thermal barcode labels, 4x6 rolls", 18.5),
    ("TAP-115", "Heavy-duty packing tape, 36-pack", 72.0),
    ("PPE-480", "Nitrile gloves, 10-box carton", 96.0),
    ("JAN-225", "Disinfecting wipes, 12-canister case", 64.75),
    ("OFC-710", "Copy paper, 10-ream carton", 52.5),
    ("OFC-755", "Laser printer toner, black", 119.0),
    ("ITC-440", "USB-C docking station", 142.0),
    ("ITC-455", "Wireless keyboard and mouse bundle", 48.0),
    ("FAC-610", "LED warehouse work light", 89.5),
    ("FAC-635", "Anti-fatigue floor mat, 3x5", 74.25),
    ("SAF-520", "High-visibility safety vests, 25-pack", 132.0),
    ("SAF-545", "First aid refill kit, 100-person", 68.0),
    ("MRO-300", "Stainless steel utility cart", 315.0),
    ("MRO-335", "Boltless shelving unit, 48 inch", 228.0),
    ("CAF-260", "Breakroom coffee, 80-count case", 41.5),
    ("CAF-275", "Compostable cups, 1000-count", 86.0),
)


def _build_demo_data() -> tuple[list[_Row], list[_Row]]:
    orders: list[_Row] = []
    order_lines: list[_Row] = []
    start_date = date(2026, 5, 4)
    line_id = 1

    for index in range(40):
        order_id = 1001 + index
        order_total = 0.0
        for line_index in range(3):
            sku, product, unit_price = _PRODUCTS[(index * 5 + line_index * 7) % len(_PRODUCTS)]
            quantity = ((index + line_index * 3) % 6 + 1) * (1 + line_index)
            order_lines.append(
                {
                    "id": line_id,
                    "order_id": order_id,
                    "sku": sku,
                    "product": product,
                    "quantity": quantity,
                    "unit_price": unit_price,
                }
            )
            order_total += quantity * unit_price
            line_id += 1

        orders.append(
            {
                "id": order_id,
                "customer": _CUSTOMERS[(index * 3) % len(_CUSTOMERS)],
                "status": _STATUSES[index % len(_STATUSES)],
                "total": round(order_total, 2),
                "order_date": (start_date + timedelta(days=index * 3 + index % 4)).isoformat(),
                "region": _REGIONS[(index * 2) % len(_REGIONS)],
                "sales_rep": _SALES_REPS[(index * 5) % len(_SALES_REPS)],
            }
        )

    return orders, order_lines


_ORDERS, _ORDER_LINES = _build_demo_data()


def seed_if_empty(conn: sqlite3.Connection) -> bool:
    """Seed demo content only when the database has no servers. Returns True if seeded."""
    if service.list_servers(conn):
        logger.info("skipped demo seeding because the database already has servers")
        return False

    logger.info("seeding first-boot Contoso Orders demo content")
    server = service.create_server(
        conn,
        slug="contoso-orders",
        name="Contoso Orders",
        description=(
            "Order management for a mid-market distributor. You could build an agent that "
            "looks up an order's status for a customer, flags overdue shipments, or drills "
            "into line items to answer 'what exactly did they buy?'"
        ),
        auth_mode="none",
    )
    server_id = int(server["id"])

    orders_dataset = service.create_dataset(conn, server_id, "orders", id_field="id", rows=_ORDERS)
    lines_dataset = service.create_dataset(
        conn,
        server_id,
        "order_lines",
        id_field="id",
        rows=_ORDER_LINES,
    )

    service.create_endpoint(
        conn,
        server_id,
        "/orders",
        "GET",
        "list_orders",
        "Use when the agent needs a concise list of orders with optional field filters.",
        int(orders_dataset["id"]),
        ["id", "customer", "status", "total"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/orders/search",
        "GET",
        "search_orders",
        "Use when the agent needs to search orders by customer, status, region, or sales rep.",
        int(orders_dataset["id"]),
        ["id", "customer", "status", "total"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/orders/{id}",
        "GET",
        "get_order",
        "Use when the agent knows an order ID and needs the full order record.",
        int(orders_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/orders",
        "POST",
        "create_order",
        "Use when the agent needs to create a new order from supplied order fields.",
        int(orders_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/orders/{id}",
        "PATCH",
        "update_order",
        "Use when the agent needs to change fields for an existing order ID.",
        int(orders_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/order-lines",
        "GET",
        "list_order_lines",
        "Use when the agent needs SKU, product, quantity, and price details for order lines.",
        int(lines_dataset["id"]),
        ["id", "order_id", "sku", "product", "quantity"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/orders/{id}",
        "DELETE",
        "delete_order",
        "Use when the agent needs to remove an order that was cancelled or created in error.",
        int(orders_dataset["id"]),
    )

    llm = service.create_llm_endpoint(
        conn,
        slug="demo-llm",
        name="Demo LLM",
        description="Mock chat endpoint for demonstrating prompt routing without external calls.",
        mode="mock",
        model_name="gpt-4o-mini",
        auth_mode="none",
    )
    service.set_llm_responses(
        conn,
        int(llm["id"]),
        [
            {
                "match_type": "contains",
                "match_value": "order",
                "response": (
                    "I can help with Contoso order questions. Try asking for open orders, a "
                    "specific order ID, or the line items behind an order total."
                ),
            },
            {
                "match_type": "contains",
                "match_value": "shipment",
                "response": (
                    "For shipment questions, use order status and dates first, then drill into "
                    "order lines if the customer asks what was purchased."
                ),
            },
            {
                "match_type": "always",
                "match_value": "",
                "response": (
                    "This is the demo mock model. Ask about orders, customers, status, totals, "
                    "or order lines to exercise the seeded tools."
                ),
            },
        ],
    )

    logger.info(
        "finished demo seeding: 1 server, 2 datasets, 7 endpoints, "
        f"{len(_ORDERS)} orders, {len(_ORDER_LINES)} order lines, 1 mock LLM"
    )
    return True
