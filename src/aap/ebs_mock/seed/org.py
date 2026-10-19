"""HR / organization: business group, operating units, inventory orgs, ledgers,
locations, people and buyers (IP-03: US + India assumed)."""

from __future__ import annotations

from datetime import datetime

from .context import SeedContext

START = datetime(2015, 1, 1)
END_OF_TIME = datetime(4712, 12, 31)

BUSINESS_GROUP_ID = 81
COA_ID = 50328

# ledger_id, name, short, currency, company segment(s)
LEDGERS = [
    (2021, "ACSE US Primary", "ACSE_US", "USD"),
    (2022, "ACSE India Primary", "ACSE_IN", "INR"),
]

# org_id, name, ledger_id, short code, legal entity id, default company segment, country
OPERATING_UNITS = [
    (204, "ACSE US OU", 2021, "US", 23273, "01", "US"),
    (458, "ACSE India OU", 2022, "IN", 23274, "11", "IN"),
]

# inventory org id, name, operating unit
INVENTORY_ORGS = [
    (207, "ACSE US Main Warehouse", 204),
    (609, "ACSE Pune Warehouse", 458),
]

# code, line1, city, region_2 (state), postal, country, org, ship_to, bill_to
LOCATIONS = [
    ("ACSE-DALLAS-HQ", "2500 Ross Ave", "Dallas", "TX", "75201", "US", 204, "Y", "Y"),
    ("ACSE-AUSTIN", "600 Congress Ave", "Austin", "TX", "78701", "US", 204, "Y", "N"),
    ("ACSE-CHICAGO-DC", "4800 S Kildare Ave", "Chicago", "IL", "60632", "US", 204, "Y", "N"),
    ("ACSE-NEW-YORK", "350 5th Ave", "New York", "NY", "10118", "US", 204, "Y", "N"),
    ("ACSE-SAN-JOSE", "170 W Tasman Dr", "San Jose", "CA", "95134", "US", 204, "Y", "N"),
    ("ACSE-SEATTLE", "1201 3rd Ave", "Seattle", "WA", "98101", "US", 204, "Y", "N"),
    ("ACSE-PUNE-HQ", "Survey No 12, Hinjewadi Phase 1", "Pune", "Maharashtra", "411057", "IN", 458, "Y", "Y"),
    ("ACSE-MUMBAI", "Bandra Kurla Complex, G Block", "Mumbai", "Maharashtra", "400051", "IN", 458, "Y", "N"),
    ("ACSE-BENGALURU", "Outer Ring Rd, Bellandur", "Bengaluru", "Karnataka", "560103", "IN", 458, "Y", "N"),
    ("ACSE-HYDERABAD", "HITEC City, Madhapur", "Hyderabad", "Telangana", "500081", "IN", 458, "Y", "N"),
    ("ACSE-CHENNAI", "Old Mahabalipuram Rd, Thoraipakkam", "Chennai", "Tamil Nadu", "600097", "IN", 458, "Y", "N"),
    ("ACSE-DELHI", "Connaught Place, Block A", "New Delhi", "Delhi", "110001", "IN", 458, "Y", "N"),
]


def generate(ctx: SeedContext) -> None:
    loc_ids: dict[str, int] = {}
    locations_by_org: dict[int, list[int]] = {204: [], 458: []}
    for code, line1, city, state, postal, country, org, ship, bill in LOCATIONS:
        loc_id = ctx.next_id("hr.hr_locations_s")
        loc_ids[code] = loc_id
        locations_by_org[org].append(loc_id)
        inv_org = 207 if org == 204 else 609
        ctx.add(
            "hr.hr_locations_all",
            location_id=loc_id, location_code=code, description=f"ACSE {city}",
            address_line_1=line1, town_or_city=city, region_2=state, postal_code=postal,
            country=country, ship_to_site_flag=ship, bill_to_site_flag=bill,
            receiving_site_flag=ship, office_site_flag="Y", inventory_organization_id=inv_org,
            business_group_id=BUSINESS_GROUP_ID,
        )
    ctx.ref["location_ids"] = loc_ids
    ctx.ref["locations_by_org"] = locations_by_org
    ctx.ref["location_rows"] = {r["location_id"]: r for r in ctx.rows["hr.hr_locations_all"]}

    ctx.add("hr.hr_all_organization_units", organization_id=BUSINESS_GROUP_ID,
            business_group_id=BUSINESS_GROUP_ID, name="ACSE Business Group", type="BG",
            date_from=START, location_id=loc_ids["ACSE-DALLAS-HQ"])

    for ledger_id, name, short, currency in LEDGERS:
        ctx.reserve("gl.gl_ledgers_s", ledger_id)
        ctx.add("gl.gl_ledgers", ledger_id=ledger_id, name=name, short_name=short,
                description=f"{name} ledger", chart_of_accounts_id=COA_ID, currency_code=currency,
                period_set_name="ACSE Calendar", accounted_period_type="Month")

    for org_id, name, ledger_id, short, le_id, _company, country in OPERATING_UNITS:
        hq = "ACSE-DALLAS-HQ" if country == "US" else "ACSE-PUNE-HQ"
        ctx.add("hr.hr_all_organization_units", organization_id=org_id,
                business_group_id=BUSINESS_GROUP_ID, name=name, type="OU", date_from=START,
                location_id=loc_ids[hq])
        ctx.add("hr.hr_organization_information",
                org_information_id=ctx.next_id("hr.hr_organization_information_s"),
                organization_id=org_id, org_information_context="CLASS",
                org_information1="OPERATING_UNIT", org_information2="Y")
        ctx.add("hr.hr_organization_information",
                org_information_id=ctx.next_id("hr.hr_organization_information_s"),
                organization_id=org_id, org_information_context="Operating Unit Information",
                org_information2=str(le_id), org_information3=str(ledger_id),
                org_information5=short)

    for inv_org, name, ou in INVENTORY_ORGS:
        hq = "ACSE-DALLAS-HQ" if ou == 204 else "ACSE-PUNE-HQ"
        ctx.add("hr.hr_all_organization_units", organization_id=inv_org,
                business_group_id=BUSINESS_GROUP_ID, name=name, type="INV", date_from=START,
                location_id=loc_ids[hq])
        ctx.add("hr.hr_organization_information",
                org_information_id=ctx.next_id("hr.hr_organization_information_s"),
                organization_id=inv_org, org_information_context="CLASS",
                org_information1="INV", org_information2="Y")
        ctx.add("hr.hr_organization_information",
                org_information_id=ctx.next_id("hr.hr_organization_information_s"),
                organization_id=inv_org, org_information_context="Accounting Information",
                org_information3=str(ou))

    ctx.ref["ou"] = {ou[0]: ou for ou in OPERATING_UNITS}
    ctx.ref["ledger_by_org"] = {ou[0]: ou[2] for ou in OPERATING_UNITS}
    ctx.ref["inv_org_by_ou"] = {ou: inv for inv, _, ou in INVENTORY_ORGS}
    ctx.ref["functional_currency"] = {204: "USD", 458: "INR"}

    # People: 15 buyers + 20 requesters, split US / India.
    buyers: dict[int, list[int]] = {204: [], 458: []}
    requesters: dict[int, list[int]] = {204: [], 458: []}
    for i in range(35):
        org = 204 if i % 2 == 0 else 458
        fake = ctx.fake if org == 204 else ctx.fake_in
        first, last = fake.first_name(), fake.last_name()
        person_id = ctx.next_id("hr.per_people_s")
        ctx.add("hr.per_all_people_f", person_id=person_id, effective_start_date=START,
                effective_end_date=END_OF_TIME, business_group_id=BUSINESS_GROUP_ID,
                person_type_id=6, employee_number=str(1000 + i), full_name=f"{last}, {first}",
                first_name=first, last_name=last,
                email_address=f"{first}.{last}@acse.local".lower().replace(" ", ""))
        if i < 15:
            ctx.add("po.po_agents", agent_id=person_id,
                    location_id=ctx.rnd.choice(locations_by_org[org]),
                    authorization_limit=ctx.rnd.choice([50000, 100000, 250000, 1000000]),
                    start_date_active=START)
            buyers[org].append(person_id)
        else:
            requesters[org].append(person_id)
    ctx.ref["buyers"] = buyers
    ctx.ref["requesters"] = requesters
