"""Synthetic fact generator for Colosseum scale testing.

Generates deterministic, fictional facts matching the Fact schema across all 6 adviser domains.
Topics include realistic but irrelevant operational domains (staffing, office supplies, older quarters)
plus ~15% near-miss distractors sharing payment keywords but referring to fictional entities.
"""
from __future__ import annotations

from datetime import date
import random
from typing import Any

from core.schemas import Fact, Reliability


DOMAINS: list[tuple[str, str]] = [
    ("finance", "FIN"),
    ("business", "BUS"),
    ("customer", "CUST"),
    ("legal", "LEG"),
    ("infosec", "SEC"),
    ("ops", "OPS"),
]

IRRELEVANT_ENTITIES = [
    "Apex Retail",
    "Novus Supplies",
    "Zenith Facilities",
    "Crest Hardware",
    "Orion Logistics",
    "Solaria Media",
    "Acuity BPO",
    "Vanguard Stationery",
    "Starlight Travel",
    "Pinnacle Corp",
    "Falcon Catering",
    "BluePeak Security",
    "Terraform Facilities",
    "AeroSoft Tools",
    "Hyperion Services",
    "Kalyan Printing",
    "Saffron Hospitality",
    "Trident Warehousing",
    "Prism Transport",
    "Indus Office Solutions",
]

NEAR_MISS_ENTITIES = [
    "Nexus Pay",
    "Vortex Payments",
    "SwiftSettle Ltd",
    "EchoTransact",
    "HarborPay",
    "StrataCard",
    "ZephyrPay",
    "OmniGateway",
    "ClearRemit",
    "AuraTransact",
    "PulsePayments",
    "QuasarCheckout",
    "NovaSwitch",
    "KitePay",
    "AtlasSettlement",
]


IRRELEVANT_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "finance": [
        {
            "topic": "branch_staffing",
            "metric": "teller_monthly_payroll",
            "unit": "INR lakh / month",
            "stat_fn": lambda e, v, u, d: f"{e} branch in Pune employs tellers with monthly payroll of ₹{v} lakh as of {d}.",
            "val_range": (8.0, 25.0),
            "sources": ["payroll_register_q2.csv", "branch_staffing_report.pdf §3"],
        },
        {
            "topic": "office_supplies",
            "metric": "stationery_and_paper_spend",
            "unit": "INR lakh / quarter",
            "stat_fn": lambda e, v, u, d: f"{e} billed ₹{v} lakh for recycled printer paper and desk supplies in {d.strftime('%B %Y')}.",
            "val_range": (1.2, 5.5),
            "sources": ["procurement_ledger_2023.csv", "office_supplies_summary.pdf §2"],
        },
        {
            "topic": "unrelated_vendors",
            "metric": "cafeteria_catering_subsidy",
            "unit": "INR lakh / month",
            "stat_fn": lambda e, v, u, d: f"Subsidised corporate cafeteria services from {e} cost ₹{v} lakh a month across regional offices.",
            "val_range": (3.0, 9.0),
            "sources": ["vendor_contracts_register.xlsx", "facilities_spend_review.pdf §4"],
        },
        {
            "topic": "historical_utilities",
            "metric": "grid_electricity_expense",
            "unit": "INR lakh / month",
            "stat_fn": lambda e, v, u, d: f"Electricity utility charges for {e} commercial facility were ₹{v} lakh for {d.strftime('%B %Y')}.",
            "val_range": (4.5, 14.0),
            "sources": ["utility_metering_logs.csv", "environmental_audit_2022.pdf §1"],
        },
    ],
    "business": [
        {
            "topic": "branch_expansion",
            "metric": "kiosks_opened_count",
            "unit": "kiosks",
            "stat_fn": lambda e, v, u, d: f"{e} established {int(v)} micro-format customer service kiosks in rural districts during {d.year}.",
            "val_range": (5.0, 30.0),
            "sources": ["branch_expansion_log.csv", "rural_outreach_strategy.pdf §2"],
        },
        {
            "topic": "corporate_events",
            "metric": "annual_summit_budget",
            "unit": "INR lakh",
            "stat_fn": lambda e, v, u, d: f"{e} organised the regional distributor conference with total venue spend of ₹{v} lakh.",
            "val_range": (15.0, 45.0),
            "sources": ["corporate_events_ledger.xlsx", "marketing_calendar_2023.pdf §6"],
        },
        {
            "topic": "retail_footprint",
            "metric": "warehouse_storage_sqft",
            "unit": "sqft",
            "stat_fn": lambda e, v, u, d: f"{e} leased {int(v)} square feet of logistics shelving in Nagpur as of {d}.",
            "val_range": (2000.0, 15000.0),
            "sources": ["real_estate_portfolio.csv", "logistics_footprint_memo.pdf §2"],
        },
        {
            "topic": "promotional_merchandise",
            "metric": "branded_apparel_units",
            "unit": "units",
            "stat_fn": lambda e, v, u, d: f"{e} distributed {int(v)} branded staff uniforms and jackets across branches in {d.year}.",
            "val_range": (1000.0, 8000.0),
            "sources": ["merchandise_inventory.csv", "brand_compliance_review.pdf §1"],
        },
    ],
    "customer": [
        {
            "topic": "branch_queues",
            "metric": "counter_wait_time_minutes",
            "unit": "minutes",
            "stat_fn": lambda e, v, u, d: f"Customer queue wait time at {e} service counter averaged {v:.1f} minutes during peak morning hours.",
            "val_range": (8.0, 22.0),
            "sources": ["queue_management_system.csv", "branch_experience_audit.pdf §3"],
        },
        {
            "topic": "call_center_staffing",
            "metric": "inbound_support_agents",
            "unit": "agents",
            "stat_fn": lambda e, v, u, d: f"{e} maintained {int(v)} full-time customer care phone agents for account queries in {d.year}.",
            "val_range": (25.0, 90.0),
            "sources": ["contact_center_roster.csv", "customer_operations_q3.pdf §2"],
        },
        {
            "topic": "brochure_distribution",
            "metric": "printed_brochure_volume",
            "unit": "copies",
            "stat_fn": lambda e, v, u, d: f"{e} distributed {int(v)} informational product pamphlets across retail branches in {d.strftime('%B %Y')}.",
            "val_range": (5000.0, 35000.0),
            "sources": ["print_collateral_log.csv", "marketing_audit_2022.pdf §4"],
        },
        {
            "topic": "customer_surveys",
            "metric": "branch_cleanliness_rating",
            "unit": "/5.0",
            "stat_fn": lambda e, v, u, d: f"Physical branch cleanliness rating for {e} was recorded at {v:.1f} out of 5.0 in annual survey.",
            "val_range": (3.6, 4.9),
            "sources": ["customer_feedback_survey.csv", "nps_field_report.pdf §5"],
        },
    ],
    "legal": [
        {
            "topic": "office_leases",
            "metric": "lease_term_years",
            "unit": "years",
            "stat_fn": lambda e, v, u, d: f"Commercial facility lease agreement with {e} has a fixed duration of {int(v)} years expiring after {d.year + int(v)}.",
            "val_range": (3.0, 9.0),
            "sources": ["lease_agreements_master.xlsx", "legal_property_register.pdf §2"],
        },
        {
            "topic": "trademark_filings",
            "metric": "registered_service_marks",
            "unit": "marks",
            "stat_fn": lambda e, v, u, d: f"Legal council filed {int(v)} trademark applications for {e} brand slogans in {d.year}.",
            "val_range": (2.0, 8.0),
            "sources": ["ip_filings_database.csv", "trademark_status_update.pdf §1"],
        },
        {
            "topic": "equipment_contracts",
            "metric": "cancellation_notice_days",
            "unit": "days",
            "stat_fn": lambda e, v, u, d: f"Photocopier equipment rental contract with {e} mandates {int(v)} days advance termination notice.",
            "val_range": (30.0, 90.0),
            "sources": ["equipment_vendor_contracts.pdf §7", "procurement_terms_log.csv"],
        },
        {
            "topic": "employment_agreements",
            "metric": "non_solicit_period_months",
            "unit": "months",
            "stat_fn": lambda e, v, u, d: f"Employment covenant for regional managers at {e} specifies a {int(v)}-month non-solicitation period.",
            "val_range": (6.0, 18.0),
            "sources": ["hr_standard_agreements.pdf §4", "legal_employment_guidelines.xlsx"],
        },
    ],
    "infosec": [
        {
            "topic": "building_access_badges",
            "metric": "rfid_badges_issued",
            "unit": "badges",
            "stat_fn": lambda e, v, u, d: f"{e} issued {int(v)} smart RFID access cards for building turnstiles in {d.year}.",
            "val_range": (150.0, 750.0),
            "sources": ["physical_security_inventory.csv", "badge_audit_memo.pdf §2"],
        },
        {
            "topic": "cctv_maintenance",
            "metric": "cctv_archive_days",
            "unit": "days",
            "stat_fn": lambda e, v, u, d: f"Vault surveillance video records managed by {e} are archived for {int(v)} days per internal policy.",
            "val_range": (60.0, 180.0),
            "sources": ["surveillance_system_specs.csv", "physical_infosec_review.pdf §3"],
        },
        {
            "topic": "desktop_antivirus",
            "metric": "antivirus_endpoints_managed",
            "unit": "endpoints",
            "stat_fn": lambda e, v, u, d: f"Central antivirus policy managed by {e} covers {int(v)} administrative workstations across offices.",
            "val_range": (500.0, 2200.0),
            "sources": ["endpoint_management_console.csv", "it_security_dashboard.pdf §1"],
        },
        {
            "topic": "visitor_logs",
            "metric": "visitor_kiosks_active",
            "unit": "kiosks",
            "stat_fn": lambda e, v, u, d: f"{e} deployed {int(v)} automated check-in kiosks to capture visitor photo identification.",
            "val_range": (4.0, 16.0),
            "sources": ["visitor_management_system.csv", "facility_access_audit.pdf §4"],
        },
    ],
    "ops": [
        {
            "topic": "generator_fuel",
            "metric": "diesel_generator_litres",
            "unit": "litres",
            "stat_fn": lambda e, v, u, d: f"Standby generator at {e} regional distribution hub consumed {int(v)} litres of fuel during quarterly load tests.",
            "val_range": (400.0, 2400.0),
            "sources": ["generator_log_book.csv", "engineering_operations_report.pdf §4"],
        },
        {
            "topic": "courier_services",
            "metric": "parcels_shipped_monthly",
            "unit": "parcels",
            "stat_fn": lambda e, v, u, d: f"{e} handled {int(v)} courier shipments of internal bank documents in {d.strftime('%B %Y')}.",
            "val_range": (1200.0, 6500.0),
            "sources": ["dispatch_logistics_sheet.csv", "ops_courier_invoices.pdf §2"],
        },
        {
            "topic": "warehouse_shelving",
            "metric": "pallet_racks_installed",
            "unit": "bays",
            "stat_fn": lambda e, v, u, d: f"{e} erected {int(v)} heavy-duty steel pallet bays at the centralized archive warehouse.",
            "val_range": (20.0, 110.0),
            "sources": ["warehouse_engineering_plans.pdf §1", "asset_register_2022.csv"],
        },
        {
            "topic": "janitorial_services",
            "metric": "cleaning_contract_monthly",
            "unit": "INR lakh / month",
            "stat_fn": lambda e, v, u, d: f"Nightly commercial cleaning and waste management contract with {e} is ₹{v} lakh a month.",
            "val_range": (1.8, 6.2),
            "sources": ["facility_maintenance_master.xlsx", "operations_procurement.pdf §3"],
        },
    ],
}


NEAR_MISS_TEMPLATES: dict[str, list[dict[str, Any]]] = {
    "finance": [
        {
            "topic": "payments_near_miss",
            "metric": "interchange_fee_pct",
            "unit": "% of volume",
            "stat_fn": lambda e, v, u, d: f"{e} levies a merchant interchange fee of {v:.2f}% on debit card transaction volumes as of {d}.",
            "val_range": (0.15, 0.45),
            "sources": ["regional_pricing_schedules.pdf §2", "merchant_statement_sample.csv"],
        },
        {
            "topic": "payments_near_miss",
            "metric": "annual_payment_volume",
            "unit": "INR crore / year",
            "stat_fn": lambda e, v, u, d: f"{e} recorded annual processed payment transaction volume of ₹{v:.0f} crore in {d.year}.",
            "val_range": (120.0, 850.0),
            "sources": ["payment_volume_report_2023.csv", "industry_gateway_brief.pdf §1"],
        },
        {
            "topic": "payments_near_miss",
            "metric": "checkout_platform_fee",
            "unit": "INR lakh / month",
            "stat_fn": lambda e, v, u, d: f"{e} charges a recurring gateway platform fee of ₹{v:.1f} lakh for checkout integration.",
            "val_range": (2.0, 7.5),
            "sources": ["gateway_fee_schedule.pdf §3", "merchant_billing_q4.csv"],
        },
    ],
    "business": [
        {
            "topic": "payments_near_miss",
            "metric": "merchant_routing_partnerships",
            "unit": "partnerships",
            "stat_fn": lambda e, v, u, d: f"{e} established {int(v)} multi-gateway routing integrations across retail merchant groups in {d.year}.",
            "val_range": (4.0, 18.0),
            "sources": ["merchant_partnerships_log.csv", "fintech_expansion_q2.pdf §3"],
        },
        {
            "topic": "payments_near_miss",
            "metric": "e_commerce_merchant_share_pct",
            "unit": "%",
            "stat_fn": lambda e, v, u, d: f"{e} captured a {v:.1f}% market share of regional online grocery checkout transactions.",
            "val_range": (5.0, 22.0),
            "sources": ["market_share_benchmarking.pdf §2", "digital_commerce_2023.csv"],
        },
    ],
    "customer": [
        {
            "topic": "payments_near_miss",
            "metric": "payment_dispute_count",
            "unit": "disputes / month",
            "stat_fn": lambda e, v, u, d: f"{e} processed an average of {int(v)} customer chargeback disputes per month across checkout channels.",
            "val_range": (120.0, 680.0),
            "sources": ["chargeback_resolution_log.csv", "customer_disputes_q3.pdf §1"],
        },
        {
            "topic": "payments_near_miss",
            "metric": "payment_refund_tat_days",
            "unit": "days",
            "stat_fn": lambda e, v, u, d: f"Customer payment refund processing turnaround time on {e} averaged {v:.1f} days in {d.year}.",
            "val_range": (2.0, 5.5),
            "sources": ["refund_monitoring_stats.csv", "user_satisfaction_review.pdf §4"],
        },
    ],
    "legal": [
        {
            "topic": "payments_near_miss",
            "metric": "gateway_exit_fee",
            "unit": "INR lakh",
            "stat_fn": lambda e, v, u, d: f"Merchant acquiring contract with {e} specifies an early exit termination charge of ₹{v:.1f} lakh.",
            "val_range": (10.0, 35.0),
            "sources": ["merchant_msa_contracts.pdf §9", "legal_risk_schedule.xlsx"],
        },
        {
            "topic": "payments_near_miss",
            "metric": "dispute_arbitration_days",
            "unit": "days",
            "stat_fn": lambda e, v, u, d: f"{e} terms of service allow merchants {int(v)} days to file formal dispute arbitration claims.",
            "val_range": (30.0, 90.0),
            "sources": ["gateway_terms_of_service.pdf §14", "merchant_contract_templates.csv"],
        },
    ],
    "infosec": [
        {
            "topic": "payments_near_miss",
            "metric": "payment_tokenisation_compliance",
            "unit": "",
            "stat_fn": lambda e, v, u, d: f"{e} payment card tokenisation vault underwent third-party security verification in {d.strftime('%B %Y')}.",
            "val_range": (1.0, 1.0),
            "sources": ["card_tokenisation_audit.pdf §2", "cert_in_compliance_log.csv"],
        },
        {
            "topic": "payments_near_miss",
            "metric": "pci_dss_findings_count",
            "unit": "findings",
            "stat_fn": lambda e, v, u, d: f"Annual PCI DSS assessment for {e} payment gateway identified {int(v)} minor procedural observations.",
            "val_range": (0.0, 4.0),
            "sources": ["pci_dss_attestation_2023.pdf §5", "infosec_vendor_scoring.xlsx"],
        },
    ],
    "ops": [
        {
            "topic": "payments_near_miss",
            "metric": "payment_gateway_uptime_pct",
            "unit": "%",
            "stat_fn": lambda e, v, u, d: f"{e} core payment processing gateway maintained {v:.2f}% operational uptime over the quarter.",
            "val_range": (99.60, 99.98),
            "sources": ["gateway_uptime_telemetry.csv", "engineering_reliability_review.pdf §2"],
        },
        {
            "topic": "payments_near_miss",
            "metric": "transaction_latency_p95_ms",
            "unit": "ms",
            "stat_fn": lambda e, v, u, d: f"Peak transaction authorization latency on {e} payment routing API was {int(v)} ms during festive sales.",
            "val_range": (280.0, 720.0),
            "sources": ["api_performance_logs.csv", "payment_latency_benchmarks.pdf §3"],
        },
    ],
}


def generate_synthetic_facts(count: int = 1200, seed: int = 7) -> list[Fact]:
    """Generate N deterministic synthetic facts spread across the 6 adviser domains.
    
    Includes ~15% near-miss distractors with payment keywords but fictional entities.
    Uses IDs like SYN-FIN-0001 to prevent any collision with real fact IDs.
    """
    rng = random.Random(seed)
    facts: list[Fact] = []

    # Calculate facts per domain
    num_domains = len(DOMAINS)
    base_per_domain = count // num_domains
    remainder = count % num_domains

    all_domain_facts: list[Fact] = []

    for dom_idx, (domain, prefix) in enumerate(DOMAINS):
        domain_count = base_per_domain + (1 if dom_idx < remainder else 0)
        near_miss_count = int(round(domain_count * 0.15))
        irrelevant_count = domain_count - near_miss_count

        domain_facts: list[Fact] = []

        # 1. Generate irrelevant facts
        irrelevant_templates = IRRELEVANT_TEMPLATES.get(domain, IRRELEVANT_TEMPLATES["finance"])
        for i in range(irrelevant_count):
            tpl = irrelevant_templates[i % len(irrelevant_templates)]
            entity = rng.choice(IRRELEVANT_ENTITIES)
            min_v, max_v = tpl["val_range"]
            val_num = round(rng.uniform(min_v, max_v), 2)
            as_of_year = rng.choice([2021, 2022, 2023, 2024])
            as_of_month = rng.choice([3, 6, 9, 12])
            as_of_day = rng.choice([15, 28, 30])
            as_of = date(as_of_year, as_of_month, as_of_day)

            statement = tpl["stat_fn"](entity, val_num, tpl["unit"], as_of)
            source_ref = rng.choice(tpl["sources"])
            source_type = "table" if source_ref.endswith(".csv") else "document"
            reliability = rng.choices(
                [Reliability.high, Reliability.medium, Reliability.low],
                weights=[0.70, 0.20, 0.10],
            )[0]

            # 20% chance of a related domain (different from current)
            other_domains = [d for d, _ in DOMAINS if d != domain]
            related = [rng.choice(other_domains)] if rng.random() < 0.20 else []

            fact_idx = len(domain_facts) + 1
            fact_id = f"SYN-{prefix}-{fact_idx:04d}"

            f = Fact(
                fact_id=fact_id,
                domain=domain,
                related_domains=related,
                topic=tpl["topic"],
                statement=statement,
                entity=entity,
                metric=tpl["metric"],
                value_num=val_num,
                value_text=None,
                unit=tpl["unit"],
                as_of=as_of,
                superseded_by=None,
                source_type=source_type,
                source_ref=source_ref,
                reliability=reliability,
            )
            domain_facts.append(f)

        # 2. Generate near-miss distractors
        near_miss_templates = NEAR_MISS_TEMPLATES.get(domain, NEAR_MISS_TEMPLATES["finance"])
        for i in range(near_miss_count):
            tpl = near_miss_templates[i % len(near_miss_templates)]
            entity = rng.choice(NEAR_MISS_ENTITIES)
            min_v, max_v = tpl["val_range"]
            val_num = round(rng.uniform(min_v, max_v), 2)
            as_of_year = rng.choice([2023, 2024])
            as_of_month = rng.choice([3, 6, 9, 12])
            as_of_day = rng.choice([15, 28, 30])
            as_of = date(as_of_year, as_of_month, as_of_day)

            statement = tpl["stat_fn"](entity, val_num, tpl["unit"], as_of)
            source_ref = rng.choice(tpl["sources"])
            source_type = "table" if source_ref.endswith(".csv") else "document"
            reliability = rng.choices(
                [Reliability.high, Reliability.medium, Reliability.low],
                weights=[0.75, 0.20, 0.05],
            )[0]

            other_domains = [d for d, _ in DOMAINS if d != domain]
            related = [rng.choice(other_domains)] if rng.random() < 0.25 else []

            fact_idx = len(domain_facts) + 1
            fact_id = f"SYN-{prefix}-{fact_idx:04d}"

            f = Fact(
                fact_id=fact_id,
                domain=domain,
                related_domains=related,
                topic=tpl["topic"],
                statement=statement,
                entity=entity,
                metric=tpl["metric"],
                value_num=val_num,
                value_text=None,
                unit=tpl["unit"],
                as_of=as_of,
                superseded_by=None,
                source_type=source_type,
                source_ref=source_ref,
                reliability=reliability,
            )
            domain_facts.append(f)

        all_domain_facts.extend(domain_facts)

    return all_domain_facts
