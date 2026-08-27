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


_HR_EMPLOYEE_PROFILES: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("Marieke van Dijk", "People Operations", "VP People", "Amsterdam", "", "full_time"),
    ("Pieter Janssen", "Finance", "Finance Director", "Rotterdam", "EMP1001", "full_time"),
    ("Sofia Rossi", "Sales", "Sales Director", "Milan", "EMP1001", "full_time"),
    ("Lars Müller", "IT", "IT Director", "Berlin", "EMP1001", "full_time"),
    ("Aisha Khan", "People Operations", "HR Business Partner", "Amsterdam", "EMP1001", "full_time"),
    ("Thomas de Boer", "People Operations", "Recruiter", "Utrecht", "EMP1001", "full_time"),
    (
        "Ingrid Bakker",
        "People Operations",
        "Payroll Specialist",
        "Amsterdam",
        "EMP1001",
        "part_time",
    ),
    ("Noa Visser", "People Operations", "Office Manager", "Amsterdam", "EMP1001", "full_time"),
    ("Jamal El Amrani", "IT", "Support Engineer", "Brussels", "EMP1004", "full_time"),
    ("Eva Novak", "IT", "Systems Administrator", "Prague", "EMP1004", "full_time"),
    ("Lukas Schneider", "IT", "Security Analyst", "Berlin", "EMP1004", "full_time"),
    ("Camille Dubois", "IT", "Service Desk Lead", "Paris", "EMP1004", "full_time"),
    ("Emma Williams", "Sales", "Account Executive", "London", "EMP1003", "full_time"),
    ("Miguel Santos", "Sales", "Account Executive", "Madrid", "EMP1003", "full_time"),
    ("Noor Haddad", "Sales", "Customer Success Manager", "Amsterdam", "EMP1003", "full_time"),
    ("Anneke de Groot", "Sales", "Sales Operations Analyst", "Utrecht", "EMP1003", "part_time"),
    ("Jan Kowalski", "Finance", "Financial Controller", "Warsaw", "EMP1002", "full_time"),
    ("Mei Chen", "Finance", "Accounts Payable Specialist", "Amsterdam", "EMP1002", "full_time"),
    ("Oliver Smith", "Finance", "Procurement Manager", "London", "EMP1002", "full_time"),
    ("Sara Lindgren", "Finance", "FP&A Analyst", "Stockholm", "EMP1002", "full_time"),
    ("Koen van Leeuwen", "IT", "Product Manager", "Amsterdam", "EMP1004", "full_time"),
    ("Priya Nair", "IT", "Data Engineer", "Dublin", "EMP1004", "full_time"),
    ("Yuki Tanaka", "IT", "Data Analyst", "Amsterdam", "EMP1004", "contractor"),
    ("Elena Petrova", "Legal", "Legal Counsel", "Sofia", "EMP1001", "full_time"),
    ("Daan de Vries", "Operations", "Operations Lead", "Eindhoven", "EMP1001", "full_time"),
    ("Fatima Khan", "People Operations", "Learning Manager", "Amsterdam", "EMP1001", "full_time"),
    ("Hugo Martins", "Operations", "Warehouse Coordinator", "Porto", "EMP1025", "full_time"),
    ("Leonie Fischer", "Operations", "Facilities Planner", "Hamburg", "EMP1025", "part_time"),
    ("Mateusz Zielinski", "Sales", "Partner Manager", "Krakow", "EMP1003", "full_time"),
    ("Rania Mansour", "Finance", "Credit Analyst", "Cairo", "EMP1002", "contractor"),
)

_HR_TIME_OFF_TYPES = ("vacation", "sick_leave", "parental_leave", "training", "personal")
_HR_TIME_OFF_STATUSES = (
    "approved",
    "pending_manager",
    "approved",
    "declined",
    "cancelled",
    "pending_hr",
)
_HR_TIME_OFF_NOTES = (
    "School holiday overlap",
    "",
    "Doctor appointment",
    "Long weekend request",
    "Conference travel recovery day",
    "Care responsibilities",
)

_IT_SERVICES: tuple[tuple[str, str, str, str], ...] = (
    ("SVC-001", "Modern Workplace", "Digital Workplace", "high"),
    ("SVC-002", "Identity and Access", "Security Operations", "critical"),
    ("SVC-003", "Network Connectivity", "Infrastructure", "critical"),
    ("SVC-004", "ERP Finance", "Business Apps", "high"),
    ("SVC-005", "CRM Platform", "Business Apps", "medium"),
    ("SVC-006", "Data Warehouse", "Analytics", "high"),
    ("SVC-007", "Endpoint Management", "Digital Workplace", "medium"),
    ("SVC-008", "Facilities Systems", "Operations IT", "low"),
)

_IT_ASSET_MODELS: tuple[tuple[str, str, str, str, str, str], ...] = (
    ("LAP-AMS-014", "laptop", "Surface Laptop 6", "Emma Williams", "Amsterdam", "in_use"),
    ("LAP-BER-022", "laptop", "ThinkPad X1 Carbon", "Lukas Schneider", "Berlin", "in_use"),
    ("PHN-AMS-008", "phone", "iPhone 15", "Marieke van Dijk", "Amsterdam", "in_use"),
    ("TAB-LON-011", "tablet", "iPad Air", "Oliver Smith", "London", "in_stock"),
    ("SRV-DUB-003", "server", "Dell PowerEdge R660", "Priya Nair", "Dublin", "in_use"),
    ("RTR-AMS-001", "network", "Meraki MX105", "Network Team", "Amsterdam", "in_use"),
    ("LAP-MAD-019", "laptop", "MacBook Pro 14", "Miguel Santos", "Madrid", "in_use"),
    ("MON-UTC-033", "monitor", "Dell U2724DE", "Anneke de Groot", "Utrecht", "repair"),
    ("LAP-PRG-017", "laptop", "Surface Pro 10", "Eva Novak", "Prague", "in_use"),
    ("PHN-PAR-012", "phone", "Galaxy S25", "Camille Dubois", "Paris", "in_use"),
    (
        "SRV-AMS-005",
        "server",
        "Azure Stack Edge",
        "Data Platform",
        "Amsterdam",
        "planned_retirement",
    ),
    ("PRN-RTD-002", "printer", "HP Color LaserJet", "Finance Shared", "Rotterdam", "in_use"),
    ("LAP-WAW-009", "laptop", "Dell Latitude 7450", "Jan Kowalski", "Warsaw", "in_use"),
    ("KSK-EIN-004", "kiosk", "Zebra TC58", "Hugo Martins", "Eindhoven", "lost"),
    ("LAP-AMS-029", "laptop", "Surface Laptop 5", "Noor Haddad", "Amsterdam", "in_use"),
    ("CAM-HAM-006", "camera", "Logitech Rally Bar", "Leonie Fischer", "Hamburg", "in_stock"),
    ("LAP-STO-015", "laptop", "ThinkPad T14s", "Sara Lindgren", "Stockholm", "in_use"),
    ("LAP-CAI-002", "laptop", "Dell XPS 13", "Rania Mansour", "Cairo", "in_use"),
)

_CRM_ACCOUNT_NAMES = (
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
_CRM_CONTACT_NAMES = (
    "Nina Vos",
    "Oskar Berg",
    "Isabella Ricci",
    "Henrik Larsen",
    "Marta Nowak",
    "Ethan Brown",
    "Leila Haddad",
    "Tom de Wit",
    "Grace O'Connor",
    "Rafael Costa",
    "Mila Popescu",
    "Samira Khan",
    "Jonas Weber",
    "Clara Dupont",
    "Viktor Horvat",
    "Amelie Bernard",
    "Tobias Meier",
    "Zara Ahmed",
    "Finn McCarthy",
    "Lotte Smit",
    "Ibrahim Saleh",
    "Chiara Romano",
    "Bram Peeters",
    "Katarzyna Wójcik",
    "Daniel Wilson",
    "Helena Garcia",
    "Sanne Jansen",
    "Arjun Patel",
    "Maja Nielsen",
    "Nicolas Moreau",
)

_FINANCE_COST_CENTRES: tuple[tuple[str, str, str, int], ...] = (
    ("CC-100", "People Operations", "Marieke van Dijk", 180000),
    ("CC-200", "IT Operations", "Lars Müller", 320000),
    ("CC-300", "Sales EMEA", "Sofia Rossi", 450000),
    ("CC-310", "Customer Success", "Noor Haddad", 210000),
    ("CC-400", "Finance Shared Services", "Pieter Janssen", 160000),
    ("CC-500", "Operations NL", "Daan de Vries", 260000),
    ("CC-600", "Legal and Compliance", "Elena Petrova", 90000),
    ("CC-700", "Data Platform", "Priya Nair", 240000),
)


def _build_hr_data() -> tuple[list[_Row], list[_Row], list[_Row]]:
    employees: list[_Row] = []
    for index, (name, department, role, location, manager_id, employment_type) in enumerate(
        _HR_EMPLOYEE_PROFILES
    ):
        employees.append(
            {
                "employee_id": f"EMP{1001 + index}",
                "name": name,
                "department": department,
                "role": role,
                "location": location,
                "manager_id": manager_id,
                "employment_type": employment_type,
                "start_date": (date(2018, 1, 15) + timedelta(days=index * 73)).isoformat(),
                "status": "on_leave" if index in {6, 23} else "active",
            }
        )

    org_units: list[_Row] = [
        {
            "org_unit_id": "OU-PEOPLE",
            "name": "People Operations",
            "lead_employee_id": "EMP1001",
            "parent_org_unit_id": "",
        },
        {
            "org_unit_id": "OU-FIN",
            "name": "Finance",
            "lead_employee_id": "EMP1002",
            "parent_org_unit_id": "",
        },
        {
            "org_unit_id": "OU-SALES",
            "name": "Sales",
            "lead_employee_id": "EMP1003",
            "parent_org_unit_id": "",
        },
        {
            "org_unit_id": "OU-IT",
            "name": "IT",
            "lead_employee_id": "EMP1004",
            "parent_org_unit_id": "",
        },
        {
            "org_unit_id": "OU-OPS",
            "name": "Operations",
            "lead_employee_id": "EMP1025",
            "parent_org_unit_id": "",
        },
        {
            "org_unit_id": "OU-LEGAL",
            "name": "Legal",
            "lead_employee_id": "EMP1024",
            "parent_org_unit_id": "OU-PEOPLE",
        },
    ]

    time_off_requests: list[_Row] = []
    for index in range(28):
        employee = employees[(index * 7 + 3) % len(employees)]
        manager_id = str(employee["manager_id"] or "EMP1001")
        days = (index % 5) + 1
        start = date(2026, 6, 1) + timedelta(days=index * 4 + index % 3)
        time_off_requests.append(
            {
                "request_id": 7001 + index,
                "employee_id": str(employee["employee_id"]),
                "request_type": _HR_TIME_OFF_TYPES[index % len(_HR_TIME_OFF_TYPES)],
                "start_date": start.isoformat(),
                "end_date": (start + timedelta(days=days - 1)).isoformat(),
                "days": days,
                "status": _HR_TIME_OFF_STATUSES[index % len(_HR_TIME_OFF_STATUSES)],
                "approver_id": manager_id,
                "notes": _HR_TIME_OFF_NOTES[index % len(_HR_TIME_OFF_NOTES)],
            }
        )

    return employees, time_off_requests, org_units


def _build_it_data() -> tuple[list[_Row], list[_Row], list[_Row]]:
    service_catalog = [
        {
            "service_id": service_id,
            "name": name,
            "owner_team": owner_team,
            "criticality": criticality,
            "support_hours": "24x7" if criticality == "critical" else "business_hours",
        }
        for service_id, name, owner_team, criticality in _IT_SERVICES
    ]
    assets = [
        {
            "asset_tag": asset_tag,
            "asset_type": asset_type,
            "model": model,
            "assigned_to": assigned_to,
            "location": location,
            "status": status,
            "service_id": service_catalog[index % len(service_catalog)]["service_id"],
        }
        for index, (asset_tag, asset_type, model, assigned_to, location, status) in enumerate(
            _IT_ASSET_MODELS
        )
    ]

    ticket_titles = (
        "VPN connection drops after password reset",
        "Laptop battery swelling reported",
        "Cannot approve invoice in ERP",
        "CRM sync delay for new contacts",
        "Shared mailbox missing from Outlook",
        "Warehouse scanner not checking in",
        "Guest access request for vendor",
        "Data warehouse refresh exceeded SLA",
        "Conference room camera offline",
        "Printer queue stuck for finance floor",
    )
    statuses = ("new", "in_progress", "waiting_on_user", "resolved", "new", "on_hold")
    priorities = ("P3", "P2", "P4", "P1", "P3")
    callers = (
        "Aisha Khan",
        "Emma Williams",
        "Jan Kowalski",
        "Noor Haddad",
        "Hugo Martins",
        "Sara Lindgren",
    )

    tickets: list[_Row] = []
    for index in range(30):
        asset_tag = "" if index % 7 == 0 else str(assets[(index * 3) % len(assets)]["asset_tag"])
        opened = date(2026, 7, 1) + timedelta(days=index * 2 + index % 4)
        tickets.append(
            {
                "ticket_id": 5001 + index,
                "title": ticket_titles[index % len(ticket_titles)],
                "caller": callers[index % len(callers)],
                "service_id": str(
                    service_catalog[(index * 2) % len(service_catalog)]["service_id"]
                ),
                "asset_tag": asset_tag,
                "status": statuses[index % len(statuses)],
                "priority": priorities[index % len(priorities)],
                "opened_date": opened.isoformat(),
                "due_date": (opened + timedelta(days=(index % 5) + 1)).isoformat(),
                "assignee": ""
                if index % 11 == 0
                else ("Jamal El Amrani" if index % 2 else "Camille Dubois"),
            }
        )

    return tickets, assets, service_catalog


def _build_crm_data() -> tuple[list[_Row], list[_Row], list[_Row]]:
    industries = ("Retail", "Manufacturing", "Healthcare", "Logistics", "Technology", "Hospitality")
    countries = ("Netherlands", "Germany", "United Kingdom", "Spain", "France", "Belgium")
    segments = ("enterprise", "mid_market", "strategic", "commercial")
    owners = ("Emma Williams", "Miguel Santos", "Mateusz Zielinski", "Sofia Rossi")
    accounts = [
        {
            "account_id": f"ACC{1001 + index}",
            "name": name,
            "industry": industries[index % len(industries)],
            "country": countries[(index * 2) % len(countries)],
            "segment": segments[index % len(segments)],
            "owner": owners[(index * 3) % len(owners)],
            "annual_revenue": 750000 + index * 185000,
        }
        for index, name in enumerate(_CRM_ACCOUNT_NAMES)
    ]

    contacts: list[_Row] = []
    account_contact_ids: dict[str, list[int]] = {}
    for index, name in enumerate(_CRM_CONTACT_NAMES):
        account = accounts[(index * 3) % len(accounts)]
        contact_id = 2001 + index
        account_id = str(account["account_id"])
        local_part = name.lower().replace(" ", ".").replace("'", "").replace("ó", "o")
        contacts.append(
            {
                "contact_id": contact_id,
                "account_id": account_id,
                "name": name,
                "role": "Procurement Lead"
                if index % 3 == 0
                else "Operations Sponsor"
                if index % 3 == 1
                else "Finance Stakeholder",
                "email": f"{local_part}@example.com",
                "phone": "" if index % 8 == 0 else f"+31 20 555 {1000 + index}",
                "preferred_language": "Dutch" if index % 4 == 0 else "English",
            }
        )
        account_contact_ids.setdefault(account_id, []).append(contact_id)

    stages = ("qualification", "proposal", "negotiation", "closed_won", "closed_lost", "discovery")
    next_steps = (
        "Send revised quote",
        "",
        "Book security review",
        "Schedule executive sponsor call",
    )
    opportunities: list[_Row] = []
    for index in range(26):
        account = accounts[(index * 5) % len(accounts)]
        account_id = str(account["account_id"])
        contact_ids = account_contact_ids[account_id]
        opportunities.append(
            {
                "opportunity_id": 3001 + index,
                "account_id": account_id,
                "primary_contact_id": contact_ids[index % len(contact_ids)],
                "name": f"{account['name']} renewal and expansion",
                "stage": stages[index % len(stages)],
                "value": 25000 + index * 7250,
                "close_date": (date(2026, 8, 15) + timedelta(days=index * 6)).isoformat(),
                "probability": min(95, 20 + (index % 8) * 10),
                "owner": owners[index % len(owners)],
                "next_step": next_steps[index % len(next_steps)],
            }
        )

    return accounts, contacts, opportunities


def _build_finance_data() -> tuple[list[_Row], list[_Row], list[_Row], list[_Row]]:
    cost_centres = [
        {
            "cost_center_id": cost_center_id,
            "name": name,
            "owner": owner,
            "annual_budget": annual_budget,
        }
        for cost_center_id, name, owner, annual_budget in _FINANCE_COST_CENTRES
    ]
    submitters = (
        "Aisha Khan",
        "Emma Williams",
        "Miguel Santos",
        "Priya Nair",
        "Oliver Smith",
        "Sara Lindgren",
        "Hugo Martins",
    )
    purposes = (
        "Customer workshop in Utrecht",
        "Quarterly planning travel",
        "Replacement laptop accessories",
        "Partner dinner after expo",
        "Training course and exam fee",
        "Warehouse safety supplies",
    )
    statuses = ("draft", "submitted", "approved", "paid", "rejected", "needs_info")

    expense_reports: list[_Row] = []
    for index in range(24):
        submitted = date(2026, 5, 10) + timedelta(days=index * 5)
        expense_reports.append(
            {
                "report_id": f"ER-2026-{index + 1:03d}",
                "submitter": submitters[index % len(submitters)],
                "cost_center_id": str(
                    cost_centres[(index * 3) % len(cost_centres)]["cost_center_id"]
                ),
                "purpose": purposes[index % len(purposes)],
                "status": statuses[index % len(statuses)],
                "submitted_date": "" if index % 6 == 0 else submitted.isoformat(),
                "currency": "EUR" if index % 5 else "GBP",
                "total_amount": round(85.5 + index * 47.35 + (index % 4) * 12.2, 2),
            }
        )

    categories = ("travel", "meal", "software", "office_supplies", "training", "mileage")
    merchants = (
        "NS International",
        "Hotel Jakarta",
        "Microsoft",
        "Coolblue",
        "De Kas",
        "Schiphol Parking",
    )
    expense_lines: list[_Row] = []
    for index in range(60):
        report = expense_reports[(index * 5) % len(expense_reports)]
        expense_lines.append(
            {
                "line_id": 9001 + index,
                "report_id": str(report["report_id"]),
                "cost_center_id": str(report["cost_center_id"]),
                "category": categories[index % len(categories)],
                "merchant": merchants[(index * 2) % len(merchants)],
                "expense_date": (date(2026, 5, 1) + timedelta(days=index * 3)).isoformat(),
                "amount": round(12.75 + (index % 9) * 23.4 + index * 1.15, 2),
                "receipt_attached": index % 7 != 0,
                "notes": "" if index % 5 else "Receipt image is hard to read",
            }
        )

    approvals: list[_Row] = []
    for index, report in enumerate(expense_reports):
        approvals.append(
            {
                "approval_id": 8001 + index,
                "report_id": str(report["report_id"]),
                "approver": str(cost_centres[index % len(cost_centres)]["owner"]),
                "status": "approved"
                if index % 4 in {0, 1}
                else "pending"
                if index % 4 == 2
                else "rejected",
                "decision_date": ""
                if index % 4 == 2
                else (date(2026, 5, 12) + timedelta(days=index * 5)).isoformat(),
                "comment": "Missing hotel invoice" if index % 9 == 0 else "",
            }
        )

    return expense_reports, expense_lines, cost_centres, approvals


_HR_EMPLOYEES, _HR_TIME_OFF_REQUESTS, _HR_ORG_UNITS = _build_hr_data()
_IT_TICKETS, _IT_ASSETS, _IT_SERVICE_CATALOG = _build_it_data()
_CRM_ACCOUNTS, _CRM_CONTACTS, _CRM_OPPORTUNITIES = _build_crm_data()
_FINANCE_EXPENSE_REPORTS, _FINANCE_EXPENSE_LINES, _FINANCE_COST_CENTRES_ROWS, _FINANCE_APPROVALS = (
    _build_finance_data()
)


def _seed_hr_server(conn: sqlite3.Connection) -> None:
    server = service.create_server(
        conn,
        slug="northwind-hris",
        name="Northwind HRIS",
        description=(
            "Human resources system for employee profiles, reporting lines, org units, and "
            "time-off workflows. Makers can build HR agents that answer manager-chain questions, "
            "check leave status, and submit or update realistic absence requests."
        ),
        auth_mode="none",
    )
    server_id = int(server["id"])
    employees_dataset = service.create_dataset(
        conn, server_id, "employees", id_field="employee_id", rows=_HR_EMPLOYEES
    )
    time_off_dataset = service.create_dataset(
        conn,
        server_id,
        "time_off_requests",
        id_field="request_id",
        rows=_HR_TIME_OFF_REQUESTS,
    )
    org_units_dataset = service.create_dataset(
        conn, server_id, "org_units", id_field="org_unit_id", rows=_HR_ORG_UNITS
    )

    service.create_endpoint(
        conn,
        server_id,
        "/employees",
        "GET",
        "list_employees",
        "Use when the agent needs a filtered employee directory with manager and location details.",
        int(employees_dataset["id"]),
        ["employee_id", "name", "department", "role", "manager_id"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/employees/search",
        "GET",
        "search_employees",
        (
            "Use when the agent needs to search employees by name, department, role, "
            "location, or status."
        ),
        int(employees_dataset["id"]),
        ["employee_id", "name", "department", "role", "status"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/employees/{id}",
        "GET",
        "get_employee",
        "Use when the agent knows an employee ID and needs the full HR profile.",
        int(employees_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/time-off-requests",
        "GET",
        "list_time_off_requests",
        "Use when the agent needs leave requests filtered by employee, status, type, or approver.",
        int(time_off_dataset["id"]),
        ["request_id", "employee_id", "request_type", "start_date", "status"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/time-off-requests/search",
        "GET",
        "search_time_off_requests",
        "Use when the agent needs to search time-off notes, statuses, or request types.",
        int(time_off_dataset["id"]),
        ["request_id", "employee_id", "request_type", "status", "approver_id"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/time-off-requests/{id}",
        "GET",
        "get_time_off_request",
        "Use when the agent knows a request ID and needs the full time-off record.",
        int(time_off_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/time-off-requests",
        "POST",
        "create_time_off_request",
        (
            "Use when the agent needs to submit a new time-off request using the supplied "
            "request fields."
        ),
        int(time_off_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/time-off-requests/{id}",
        "PATCH",
        "update_time_off_request",
        (
            "Use when the agent needs to change status, dates, approver, or notes on an "
            "existing request."
        ),
        int(time_off_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/org-units",
        "GET",
        "list_org_units",
        "Use when the agent needs the HR org structure and department leads.",
        int(org_units_dataset["id"]),
        ["org_unit_id", "name", "lead_employee_id", "parent_org_unit_id"],
    )


def _seed_it_server(conn: sqlite3.Connection) -> None:
    server = service.create_server(
        conn,
        slug="fabrikam-it-service",
        name="Fabrikam IT Service Desk",
        description=(
            "IT service management workspace for incidents, requests, assets, and business "
            "services. Makers can demo service desk copilots that triage tickets, inspect impacted "
            "configuration items, and update ticket ownership or status."
        ),
        auth_mode="none",
    )
    server_id = int(server["id"])
    tickets_dataset = service.create_dataset(
        conn, server_id, "tickets", id_field="ticket_id", rows=_IT_TICKETS
    )
    assets_dataset = service.create_dataset(
        conn, server_id, "assets", id_field="asset_tag", rows=_IT_ASSETS
    )
    services_dataset = service.create_dataset(
        conn,
        server_id,
        "service_catalog",
        id_field="service_id",
        rows=_IT_SERVICE_CATALOG,
    )

    service.create_endpoint(
        conn,
        server_id,
        "/tickets",
        "GET",
        "list_tickets",
        (
            "Use when the agent needs service desk tickets filtered by status, service, "
            "priority, or assignee."
        ),
        int(tickets_dataset["id"]),
        ["ticket_id", "title", "status", "priority", "service_id"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/tickets/search",
        "GET",
        "search_tickets",
        "Use when the agent needs to search ticket titles, callers, services, or assignees.",
        int(tickets_dataset["id"]),
        ["ticket_id", "title", "status", "priority", "assignee"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/tickets/{id}",
        "GET",
        "get_ticket",
        "Use when the agent knows a ticket ID and needs the full incident or request record.",
        int(tickets_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/tickets/{id}",
        "PATCH",
        "update_ticket",
        (
            "Use when the agent needs to change ticket status, priority, assignee, due date, "
            "or linked asset."
        ),
        int(tickets_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/assets",
        "GET",
        "list_assets",
        (
            "Use when the agent needs hardware or configuration items filtered by owner, "
            "status, location, or service."
        ),
        int(assets_dataset["id"]),
        ["asset_tag", "asset_type", "model", "assigned_to", "status"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/assets/search",
        "GET",
        "search_assets",
        "Use when the agent needs to search asset tags, models, assigned users, or locations.",
        int(assets_dataset["id"]),
        ["asset_tag", "asset_type", "assigned_to", "location", "status"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/assets/{id}",
        "GET",
        "get_asset",
        "Use when the agent knows an asset tag and needs the full asset details.",
        int(assets_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/service-catalog",
        "GET",
        "list_service_catalog",
        "Use when the agent needs service ownership, criticality, or support-hour context.",
        int(services_dataset["id"]),
        ["service_id", "name", "owner_team", "criticality"],
    )


def _seed_crm_server(conn: sqlite3.Connection) -> None:
    server = service.create_server(
        conn,
        slug="adatum-crm",
        name="Datum Sales CRM",
        description=(
            "Sales CRM for accounts, buyer contacts, and active opportunities across EMEA. "
            "Makers can build sales assistants that find account context, connect contacts "
            "to deals, "
            "summarise pipeline risk, and update opportunity next steps."
        ),
        auth_mode="none",
    )
    server_id = int(server["id"])
    accounts_dataset = service.create_dataset(
        conn, server_id, "accounts", id_field="account_id", rows=_CRM_ACCOUNTS
    )
    contacts_dataset = service.create_dataset(
        conn, server_id, "contacts", id_field="contact_id", rows=_CRM_CONTACTS
    )
    opportunities_dataset = service.create_dataset(
        conn,
        server_id,
        "opportunities",
        id_field="opportunity_id",
        rows=_CRM_OPPORTUNITIES,
    )

    service.create_endpoint(
        conn,
        server_id,
        "/accounts",
        "GET",
        "list_accounts",
        "Use when the agent needs accounts filtered by owner, country, industry, or segment.",
        int(accounts_dataset["id"]),
        ["account_id", "name", "industry", "country", "owner"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/accounts/search",
        "GET",
        "search_accounts",
        "Use when the agent needs to search account names, industries, countries, or owners.",
        int(accounts_dataset["id"]),
        ["account_id", "name", "industry", "segment", "owner"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/accounts/{id}",
        "GET",
        "get_account",
        "Use when the agent knows an account ID and needs complete account details.",
        int(accounts_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/contacts",
        "GET",
        "list_contacts",
        (
            "Use when the agent needs contacts filtered by account, role, language, or "
            "missing phone data."
        ),
        int(contacts_dataset["id"]),
        ["contact_id", "account_id", "name", "role", "email"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/contacts/search",
        "GET",
        "search_contacts",
        "Use when the agent needs to search contact names, roles, emails, or languages.",
        int(contacts_dataset["id"]),
        ["contact_id", "account_id", "name", "role", "preferred_language"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/opportunities",
        "GET",
        "list_opportunities",
        (
            "Use when the agent needs pipeline filtered by account, owner, stage, close date, "
            "or probability."
        ),
        int(opportunities_dataset["id"]),
        ["opportunity_id", "account_id", "name", "stage", "value"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/opportunities/search",
        "GET",
        "search_opportunities",
        "Use when the agent needs to search opportunities by name, stage, owner, or next step.",
        int(opportunities_dataset["id"]),
        ["opportunity_id", "account_id", "stage", "value", "owner"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/opportunities/{id}",
        "GET",
        "get_opportunity",
        "Use when the agent knows an opportunity ID and needs the full opportunity record.",
        int(opportunities_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/opportunities/{id}",
        "PATCH",
        "update_opportunity",
        (
            "Use when the agent needs to update stage, probability, value, close date, owner, "
            "or next step."
        ),
        int(opportunities_dataset["id"]),
    )


def _seed_finance_server(conn: sqlite3.Connection) -> None:
    server = service.create_server(
        conn,
        slug="contoso-expenses",
        name="Contoso Travel Expenses",
        description=(
            "Expense management demo covering reports, receipt line items, cost centres, and "
            "approval decisions. Makers can create finance agents that check reimbursement status, "
            "drill into spend lines, and route approvals by cost centre."
        ),
        auth_mode="none",
    )
    server_id = int(server["id"])
    reports_dataset = service.create_dataset(
        conn,
        server_id,
        "expense_reports",
        id_field="report_id",
        rows=_FINANCE_EXPENSE_REPORTS,
    )
    lines_dataset = service.create_dataset(
        conn,
        server_id,
        "expense_lines",
        id_field="line_id",
        rows=_FINANCE_EXPENSE_LINES,
    )
    cost_centres_dataset = service.create_dataset(
        conn,
        server_id,
        "cost_centres",
        id_field="cost_center_id",
        rows=_FINANCE_COST_CENTRES_ROWS,
    )
    approvals_dataset = service.create_dataset(
        conn, server_id, "approvals", id_field="approval_id", rows=_FINANCE_APPROVALS
    )

    service.create_endpoint(
        conn,
        server_id,
        "/expense-reports",
        "GET",
        "list_expense_reports",
        (
            "Use when the agent needs expense reports filtered by submitter, status, cost "
            "centre, or currency."
        ),
        int(reports_dataset["id"]),
        ["report_id", "submitter", "cost_center_id", "status", "total_amount"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/expense-reports/search",
        "GET",
        "search_expense_reports",
        (
            "Use when the agent needs to search report purpose, submitter, status, or "
            "cost-centre details."
        ),
        int(reports_dataset["id"]),
        ["report_id", "submitter", "purpose", "status", "total_amount"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/expense-reports/{id}",
        "GET",
        "get_expense_report",
        "Use when the agent knows an expense report ID and needs the full report header.",
        int(reports_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/expense-reports/{id}",
        "PATCH",
        "update_expense_report",
        (
            "Use when the agent needs to update report status, submitted date, purpose, or "
            "total amount."
        ),
        int(reports_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/expense-lines",
        "GET",
        "list_expense_lines",
        (
            "Use when the agent needs receipt line items filtered by report, category, "
            "merchant, or cost centre."
        ),
        int(lines_dataset["id"]),
        ["line_id", "report_id", "category", "merchant", "amount"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/expense-lines/search",
        "GET",
        "search_expense_lines",
        "Use when the agent needs to search expense line merchants, categories, or notes.",
        int(lines_dataset["id"]),
        ["line_id", "report_id", "category", "merchant", "amount"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/cost-centres",
        "GET",
        "list_cost_centres",
        "Use when the agent needs cost centre ownership and budget context.",
        int(cost_centres_dataset["id"]),
        ["cost_center_id", "name", "owner", "annual_budget"],
    )
    service.create_endpoint(
        conn,
        server_id,
        "/cost-centres/{id}",
        "GET",
        "get_cost_centre",
        "Use when the agent knows a cost centre ID and needs owner or budget details.",
        int(cost_centres_dataset["id"]),
    )
    service.create_endpoint(
        conn,
        server_id,
        "/approvals",
        "GET",
        "list_approvals",
        "Use when the agent needs approval decisions filtered by report, approver, or status.",
        int(approvals_dataset["id"]),
        ["approval_id", "report_id", "approver", "status", "decision_date"],
    )


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

    _seed_hr_server(conn)
    _seed_it_server(conn)
    _seed_crm_server(conn)
    _seed_finance_server(conn)

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
        "finished demo seeding: 5 servers, 15 datasets, 42 endpoints, "
        f"{len(_ORDERS)} orders, {len(_ORDER_LINES)} order lines, "
        f"{len(_HR_EMPLOYEES)} employees, {len(_IT_TICKETS)} IT tickets, "
        f"{len(_CRM_ACCOUNTS)} CRM accounts, {len(_FINANCE_EXPENSE_REPORTS)} expense reports, "
        "1 mock LLM"
    )
    return True
