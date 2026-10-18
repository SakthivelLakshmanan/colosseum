"""Synthetic fact generator for Colosseum evidence retrieval scale testing."""
from __future__ import annotations

from datetime import date, timedelta
import random
from typing import Any

from core.schemas import Fact, Reliability

DOMAINS: list[str] = ["finance", "business", "customer", "ops", "infosec", "legal"]

DOMAIN_PREFIXES: dict[str, str] = {
    "finance": "FIN",
    "business": "BUS",
    "customer": "CUS",
    "ops": "OPS",
    "infosec": "SEC",
    "legal": "LEG",
}

# Fictional entities for irrelevant background facts
IRRELEVANT_ENTITIES: list[str] = [
    "Nimbus Stationery Ltd",
    "Aura Office Supplies",
    "Vertex Furnishings",
    "Kite Facility Mgmt",
    "Apex Cleaning Services",
    "Beacon Aircon Systems",
    "Pinnacle Paper Mills",
    "Zenith Office Automation",
    "Solaria Cafeteria Services",
    "BluePeak Water Systems",
    "Swift Express Couriers",
    "PowerGrid Backup GenSets",
    "GreenLeaf Shredding",
    "Titan Elevator Co",
    "Matrix Security Guards",
    "Branch Pune West",
    "Branch Ahmedabad Central",
    "Branch Jaipur North",
    "Branch Lucknow South",
    "Branch Kochi East",
    "Branch Chandigarh Rural",
    "Branch Nagpur Mid-town",
    "Branch Indore Tech Park",
]

# Fictional entities for near-miss payment distractors (never Arvanta, Brevis, or Legion Capital)
NEAR_MISS_ENTITIES: list[str] = [
    "Zephyr POS Terminals",
    "Vanguard Cafeteria Systems",
    "Kite Transit Cards",
    "Nimbus Petrol Subsidies",
    "Helios Canteen Billing",
    "Apex Vending Solutions",
    "Aura Fleet Tolls",
    "Orion Travel Bookings",
    "Echo Metro SmartCard",
    "Pioneer Bus Passes",
    "Quantum Parking Fees",
    "Starlight Food Court Cards",
]

# Domain-specific templates for realistic but irrelevant facts
# Topics: branch_staffing, office_supplies, unrelated_vendors, older_quarters
IRRELEVANT_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "finance": [
        {
            "topic": "office_supplies",
            "metric": "printer_paper_procurement",
            "unit": "INR lakh / year",
            "val_range": (2.5, 9.8),
            "stmt": "{entity} procured ₹{val:.1f} lakh worth of A4 copy paper across regional offices in FY25.",
        },
        {
            "topic": "office_supplies",
            "metric": "furniture_depreciation",
            "unit": "INR lakh / quarter",
            "val_range": (4.0, 15.0),
            "stmt": "Quarterly depreciation on regional office desks and ergonomic chairs from {entity} was ₹{val:.1f} lakh.",
        },
        {
            "topic": "branch_staffing",
            "metric": "intern_stipend_spend",
            "unit": "INR lakh / month",
            "val_range": (1.2, 5.5),
            "stmt": "Monthly stipend payouts for seasonal finance interns at {entity} amounted to ₹{val:.1f} lakh.",
        },
        {
            "topic": "unrelated_vendors",
            "metric": "diesel_generator_fuel_spend",
            "unit": "INR lakh / quarter",
            "val_range": (3.5, 12.0),
            "stmt": "{entity} billed ₹{val:.1f} lakh for quarterly backup diesel generator fuel deliveries.",
        },
        {
            "topic": "older_quarters",
            "metric": "q1_fy24_travel_expenses",
            "unit": "INR lakh",
            "val_range": (8.0, 24.0),
            "stmt": "Total corporate travel and lodging reimbursements across regional branches in Q1 FY24 were ₹{val:.1f} lakh.",
        },
    ],
    "business": [
        {
            "topic": "branch_staffing",
            "metric": "teller_training_capacity",
            "unit": "trainees / batch",
            "val_range": (20, 60),
            "stmt": "{entity} completed induction training for {val:.0f} regional customer service associates in FY25.",
        },
        {
            "topic": "office_supplies",
            "metric": "conference_screen_upgrades",
            "unit": "screens",
            "val_range": (10, 45),
            "stmt": "{entity} supplied {val:.0f} digital display screens for regional branch conference rooms.",
        },
        {
            "topic": "unrelated_vendors",
            "metric": "courier_service_dispatch",
            "unit": "packets / month",
            "val_range": (1200, 4500),
            "stmt": "{entity} handled an average of {val:.0f} physical document parcels per month across tier-2 branches.",
        },
        {
            "topic": "older_quarters",
            "metric": "fy23_radio_campaign_reach",
            "unit": "listeners",
            "val_range": (150000, 450000),
            "stmt": "The regional FM radio promotional campaign during Diwali FY23 reached an estimated {val:.0f} local listeners.",
        },
        {
            "topic": "branch_staffing",
            "metric": "branch_headcount_rural",
            "unit": "staff members",
            "val_range": (15, 80),
            "stmt": "Active permanent branch staff at {entity} reached {val:.0f} full-time employees in September 2025.",
        },
    ],
    "customer": [
        {
            "topic": "branch_staffing",
            "metric": "queue_waiting_time_minutes",
            "unit": "minutes",
            "val_range": (8.0, 26.0),
            "stmt": "Average in-person queue waiting time at {entity} customer service counters was {val:.1f} minutes in FY25.",
        },
        {
            "topic": "office_supplies",
            "metric": "water_dispenser_availability_pct",
            "unit": "%",
            "val_range": (94.0, 99.5),
            "stmt": "Visitor water dispenser availability across regional customer lounges averaged {val:.1f}% uptime.",
        },
        {
            "topic": "unrelated_vendors",
            "metric": "ac_maintenance_ticket_resolution_hours",
            "unit": "hours",
            "val_range": (4.0, 18.0),
            "stmt": "{entity} resolved branch customer area air-conditioning temperature tickets within {val:.1f} hours.",
        },
        {
            "topic": "older_quarters",
            "metric": "q1_fy24_paper_form_defect_rate",
            "unit": "%",
            "val_range": (1.8, 6.2),
            "stmt": "In Q1 FY24, physical paper application forms rejected due to illegible handwriting accounted for {val:.1f}%.",
        },
        {
            "topic": "customer_lounges",
            "metric": "pamphlet_stand_replenishment_days",
            "unit": "days",
            "val_range": (3.0, 7.0),
            "stmt": "Regional language informational brochure racks at {entity} were restocked every {val:.1f} days.",
        },
    ],
    "ops": [
        {
            "topic": "office_supplies",
            "metric": "toner_cartridge_recycling",
            "unit": "cartridges / quarter",
            "val_range": (140, 380),
            "stmt": "{entity} collected and recycled {val:.0f} empty laser printer toner cartridges in Q3 FY25.",
        },
        {
            "topic": "unrelated_vendors",
            "metric": "generator_switchover_seconds",
            "unit": "seconds",
            "val_range": (4.5, 12.0),
            "stmt": "Automatic diesel generator switchover time during grid power fluctuations at {entity} averaged {val:.1f} seconds.",
        },
        {
            "topic": "branch_staffing",
            "metric": "facilities_inspection_compliance_pct",
            "unit": "%",
            "val_range": (95.0, 99.8),
            "stmt": "Quarterly physical facilities maintenance checklist completion across all branches reached {val:.1f}%.",
        },
        {
            "topic": "older_quarters",
            "metric": "fy23_lan_cable_replacements",
            "unit": "metres",
            "val_range": (600, 2500),
            "stmt": "During the FY23 branch network refresh, {val:.0f} metres of Cat6 Ethernet cabling were replaced.",
        },
        {
            "topic": "facilities",
            "metric": "elevator_inspection_score",
            "unit": "points out of 100",
            "val_range": (88.0, 98.0),
            "stmt": "Annual safety inspection by {entity} rated the regional hub elevator system at {val:.1f} points.",
        },
    ],
    "infosec": [
        {
            "topic": "office_supplies",
            "metric": "shredded_paper_kg",
            "unit": "kg / month",
            "val_range": (250, 850),
            "stmt": "{entity} processed {val:.0f} kg of cross-cut confidential paper waste under dual supervision in FY25.",
        },
        {
            "topic": "unrelated_vendors",
            "metric": "cctv_camera_retention_days",
            "unit": "days",
            "val_range": (30, 90),
            "stmt": "Branch entrance and cash vault CCTV recordings managed by {entity} are archived locally for {val:.0f} days.",
        },
        {
            "topic": "branch_staffing",
            "metric": "visitor_badge_return_rate_pct",
            "unit": "%",
            "val_range": (98.5, 100.0),
            "stmt": "Physical visitor badge return compliance at regional reception gates reached {val:.1f}% in Q2 FY25.",
        },
        {
            "topic": "older_quarters",
            "metric": "fy23_keycard_audit_discrepancies",
            "unit": "incidents",
            "val_range": (0, 3),
            "stmt": "Annual physical access control keycard audit for FY23 identified {val:.0f} unreturned contractor passes.",
        },
        {
            "topic": "facility_security",
            "metric": "nightly_clean_desk_violations",
            "unit": "findings / month",
            "val_range": (1, 8),
            "stmt": "Nightly physical security walk-throughs across administrative offices recorded {val:.0f} unlocked drawer notices.",
        },
    ],
    "legal": [
        {
            "topic": "unrelated_vendors",
            "metric": "janitorial_agreement_term_years",
            "unit": "years",
            "val_range": (1, 3),
            "stmt": "The commercial cleaning services agreement with {entity} has a fixed tenure of {val:.0f} years.",
        },
        {
            "topic": "office_supplies",
            "metric": "stationery_vendor_notice_days",
            "unit": "days",
            "val_range": (30, 60),
            "stmt": "The master supply agreement with {entity} requires {val:.0f} days written notice for contract termination.",
        },
        {
            "topic": "branch_staffing",
            "metric": "shops_and_establishments_compliance_pct",
            "unit": "%",
            "val_range": (99.0, 100.0),
            "stmt": "Municipal shops and establishments license renewal compliance for {entity} stood at {val:.1f}%.",
        },
        {
            "topic": "older_quarters",
            "metric": "fy23_commercial_lease_stamp_duty",
            "unit": "INR lakh",
            "val_range": (1.8, 6.5),
            "stmt": "Ad valorem stamp duty registered for the regional administrative lease in FY23 amounted to ₹{val:.1f} lakh.",
        },
        {
            "topic": "facilities_contract",
            "metric": "water_tanker_indemnity_cap",
            "unit": "INR lakh",
            "val_range": (5.0, 20.0),
            "stmt": "Water tanker supply contract with {entity} caps vendor mutual indemnification at ₹{val:.1f} lakh.",
        },
    ],
}

# Near-miss templates sharing payment keywords (payment, gateway, fee, transaction, settlement, dispute, volume, refund)
NEAR_MISS_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "finance": [
        {
            "topic": "canteen_payments",
            "metric": "canteen_payment_volume",
            "unit": "INR lakh / quarter",
            "val_range": (12.0, 35.0),
            "stmt": "{entity} processed ₹{val:.1f} lakh of cafeteria card payments across regional branch canteens in Q2 FY25.",
        },
        {
            "topic": "meal_card_fees",
            "metric": "canteen_transaction_fee_pct",
            "unit": "%",
            "val_range": (1.1, 1.8),
            "stmt": "{entity} charges a payment transaction fee of {val:.2f}% on corporate staff meal card deductions.",
        },
        {
            "topic": "vending_fees",
            "metric": "vending_gateway_monthly_fee",
            "unit": "INR / month",
            "val_range": (3500, 8500),
            "stmt": "{entity} bills a flat monthly gateway maintenance fee of ₹{val:.0f} for office snack dispensers.",
        },
        {
            "topic": "fuel_settlement",
            "metric": "fuel_card_settlement_volume",
            "unit": "INR lakh / month",
            "val_range": (25.0, 65.0),
            "stmt": "{entity} settled ₹{val:.1f} lakh in corporate fuel payment disbursements in September 2024.",
        },
    ],
    "business": [
        {
            "topic": "transit_recharge",
            "metric": "transit_payment_merchants",
            "unit": "merchants",
            "val_range": (250, 750),
            "stmt": "{entity} enrolled {val:.0f} retail merchants for public transit smartcard recharge payments in FY25.",
        },
        {
            "topic": "bus_pass_conversion",
            "metric": "bus_ticket_conversion_pct",
            "unit": "%",
            "val_range": (96.5, 99.2),
            "stmt": "{entity} achieved a {val:.1f}% digital payment conversion rate on commuter bus pass renewals.",
        },
        {
            "topic": "food_court_merchants",
            "metric": "food_court_settlement_merchants",
            "unit": "merchants",
            "val_range": (20, 65),
            "stmt": "{entity} partnered with {val:.0f} food court vendors for automated daily payment settlement.",
        },
        {
            "topic": "canteen_network",
            "metric": "pos_gateway_terminal_count",
            "unit": "terminals",
            "val_range": (40, 120),
            "stmt": "{entity} expanded its payment gateway footprint by deploying {val:.0f} POS terminals across regional cafeterias.",
        },
    ],
    "customer": [
        {
            "topic": "vending_disputes",
            "metric": "vending_payment_dispute_tickets",
            "unit": "tickets / month",
            "val_range": (15, 60),
            "stmt": "Cafeteria visitors logged {val:.0f} payment dispute tickets regarding failed snack vending machine transactions.",
        },
        {
            "topic": "transit_refunds",
            "metric": "transit_refund_turnaround_days",
            "unit": "days",
            "val_range": (4.0, 9.0),
            "stmt": "{entity} reported an average refund turnaround of {val:.1f} business days for failed metro gate payments.",
        },
        {
            "topic": "canteen_satisfaction",
            "metric": "canteen_checkout_satisfaction_pct",
            "unit": "%",
            "val_range": (82.0, 94.0),
            "stmt": "Regional canteen customer surveys recorded {val:.1f}% satisfaction with QR code checkout payment speeds.",
        },
        {
            "topic": "card_support",
            "metric": "payment_failure_enquiry_calls",
            "unit": "calls / month",
            "val_range": (35, 110),
            "stmt": "Internal employee helpdesk received {val:.0f} enquiries regarding transit card payment gateway sync delays.",
        },
    ],
    "ops": [
        {
            "topic": "terminal_uptime",
            "metric": "pos_terminal_payment_uptime_pct",
            "unit": "%",
            "val_range": (99.1, 99.9),
            "stmt": "{entity} reported {val:.2f}% payment gateway uptime across campus POS terminals in Q3 FY25.",
        },
        {
            "topic": "pos_success_rate",
            "metric": "pos_transaction_success_rate_pct",
            "unit": "%",
            "val_range": (97.2, 99.4),
            "stmt": "{entity} achieved a {val:.1f}% transaction success rate across 18,000 corporate campus card payments.",
        },
        {
            "topic": "subsidy_reconciliation",
            "metric": "payment_settlement_reconciliation_pct",
            "unit": "%",
            "val_range": (94.0, 99.5),
            "stmt": "Regional employee fuel subsidy portal completed {val:.1f}% of daily payment settlement reconciliations within 24 hours.",
        },
        {
            "topic": "kiosk_gateway",
            "metric": "kiosk_payment_latency_ms",
            "unit": "ms",
            "val_range": (450, 950),
            "stmt": "Average transaction response time on {entity} payment gateways remained below {val:.0f} ms in peak hours.",
        },
    ],
    "infosec": [
        {
            "topic": "kiosk_security",
            "metric": "kiosk_payment_log_retention_days",
            "unit": "days",
            "val_range": (15, 45),
            "stmt": "{entity} automated payment kiosks purge unencrypted local transaction memory logs every {val:.0f} days.",
        },
        {
            "topic": "pos_patching",
            "metric": "payment_terminal_patch_compliance_pct",
            "unit": "%",
            "val_range": (98.0, 100.0),
            "stmt": "{entity} verified {val:.1f}% firmware patch compliance on all regional cafeteria payment terminals.",
        },
        {
            "topic": "canteen_auth",
            "metric": "canteen_payment_pin_threshold",
            "unit": "INR",
            "val_range": (400, 1000),
            "stmt": "Canteen smartcard payment system requires chip PIN authentication for any transaction exceeding ₹{val:.0f}.",
        },
        {
            "topic": "fuel_card_encryption",
            "metric": "payment_gateway_tls_version",
            "unit": "protocol version",
            "val_range": (1.2, 1.3),
            "stmt": "{entity} enforces TLS {val:.1f} cryptographic protocol for all employee fuel payment claim transmissions.",
        },
    ],
    "legal": [
        {
            "topic": "canteen_liability",
            "metric": "food_payment_liability_cap",
            "unit": "INR",
            "val_range": (5000, 25000),
            "stmt": "Contract with {entity} caps mutual liability for disputed cafeteria food payments at ₹{val:.0f} per event.",
        },
        {
            "topic": "pos_contract",
            "metric": "payment_gateway_notice_days",
            "unit": "days",
            "val_range": (30, 90),
            "stmt": "The merchant services agreement with {entity} mandates {val:.0f} days prior notice for payment gateway maintenance.",
        },
        {
            "topic": "subsidy_arbitration",
            "metric": "fuel_payment_dispute_notice_days",
            "unit": "days",
            "val_range": (15, 45),
            "stmt": "{entity} corporate fuel payment agreement establishes a {val:.0f}-day amicable resolution window for billing disputes.",
        },
        {
            "topic": "kiosk_indemnity",
            "metric": "vending_payment_indemnity_cap_lakh",
            "unit": "INR lakh",
            "val_range": (2.0, 10.0),
            "stmt": "Breakroom vending machine agreement limits {entity} indemnification for payment gateway discrepancies to ₹{val:.1f} lakh.",
        },
    ],
}


def generate_synthetic_facts(n: int = 1200, seed: int = 7) -> list[Fact]:
    """Generate N synthetic, fictional facts with a fixed seed.

    Guarantees:
    - Deterministic output given (n, seed).
    - Even distribution across all 6 adviser domains.
    - Realistic irrelevant background topics (office supplies, branch staffing, vendors, older quarters).
    - ~15% near-miss distractors with payment terminology for non-core entities.
    - Strict adherence to the Fact schema.
    - Unique IDs formatted as `SYN-{DOMAIN_PREFIX}-{INDEX:04d}` (e.g. `SYN-FIN-0001`).
    - No collisions with real fact IDs (FIN-, BUS-, CUS-, OPS-, SEC-, LEG-).
    """
    rng = random.Random(seed)

    base_count = n // len(DOMAINS)
    remainder = n % len(DOMAINS)

    facts: list[Fact] = []
    base_date = date(2023, 1, 1)

    for domain_idx, domain in enumerate(DOMAINS):
        domain_count = base_count + (1 if domain_idx < remainder else 0)
        prefix = DOMAIN_PREFIXES[domain]
        near_miss_count = max(1, round(0.15 * domain_count))
        irrelevant_count = domain_count - near_miss_count

        irrel_templates = IRRELEVANT_TEMPLATES[domain]
        near_templates = NEAR_MISS_TEMPLATES[domain]

        # Generate irrelevant background facts
        for i in range(irrelevant_count):
            fact_num = i + 1
            fact_id = f"SYN-{prefix}-{fact_num:04d}"

            tpl = irrel_templates[i % len(irrel_templates)]
            entity = IRRELEVANT_ENTITIES[rng.randint(0, len(IRRELEVANT_ENTITIES) - 1)]

            low, high = tpl["val_range"]
            val = round(rng.uniform(low, high), 2)
            stmt = tpl["stmt"].format(entity=entity, val=val)

            # Assign occasional related domain (20% chance)
            related: list[str] = []
            if rng.random() < 0.20:
                other_domains = [d for d in DOMAINS if d != domain]
                related.append(rng.choice(other_domains))

            as_of_date = base_date + timedelta(days=rng.randint(0, 1200))
            source_type = "table" if rng.random() < 0.45 else "document"
            source_ref = f"{entity.replace(' ', '_')}_Record_FY{rng.randint(23, 26)}.pdf"
            reliability = rng.choice([Reliability.high, Reliability.high, Reliability.medium])

            fact = Fact(
                fact_id=fact_id,
                domain=domain,
                related_domains=related,
                topic=tpl["topic"],
                statement=stmt,
                entity=entity,
                metric=tpl["metric"],
                value_num=val,
                value_text=None,
                unit=tpl["unit"],
                as_of=as_of_date,
                superseded_by=None,
                source_type=source_type,
                source_ref=source_ref,
                reliability=reliability,
            )
            facts.append(fact)

        # Generate near-miss payment distractors (~15%)
        for j in range(near_miss_count):
            fact_num = irrelevant_count + j + 1
            fact_id = f"SYN-{prefix}-{fact_num:04d}"

            tpl = near_templates[j % len(near_templates)]
            entity = NEAR_MISS_ENTITIES[rng.randint(0, len(NEAR_MISS_ENTITIES) - 1)]

            low, high = tpl["val_range"]
            val = round(rng.uniform(low, high), 2)
            stmt = tpl["stmt"].format(entity=entity, val=val)

            # Assign occasional related domain (25% chance)
            related = []
            if rng.random() < 0.25:
                other_domains = [d for d in DOMAINS if d != domain]
                related.append(rng.choice(other_domains))

            as_of_date = base_date + timedelta(days=rng.randint(0, 1200))
            source_type = "table" if rng.random() < 0.50 else "document"
            source_ref = f"{entity.replace(' ', '_')}_Monthly_Log"
            reliability = rng.choice([Reliability.high, Reliability.medium])

            fact = Fact(
                fact_id=fact_id,
                domain=domain,
                related_domains=related,
                topic=tpl["topic"],
                statement=stmt,
                entity=entity,
                metric=tpl["metric"],
                value_num=val,
                value_text=None,
                unit=tpl["unit"],
                as_of=as_of_date,
                superseded_by=None,
                source_type=source_type,
                source_ref=source_ref,
                reliability=reliability,
            )
            facts.append(fact)

    return facts
