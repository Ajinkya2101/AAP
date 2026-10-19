"""INV: units of measure, purchasing categories ("internal codes") and items.

Every item stores the vendor's catalogue wording in MTL_SYSTEM_ITEMS_B.ATTRIBUTE1
(mock-only DFF, see ebs_mock/README.md). The invoice generator prints that wording,
so invoice descriptions differ from PO descriptions the way they do in real life.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .context import SeedContext, money

START = datetime(2012, 1, 1)
CATEGORY_STRUCTURE_ID = 201
PURCHASING_CATEGORY_SET_ID = 1100000041

# unit_of_measure, uom_code, class
UOMS = [
    ("Each", "Ea", "Quantity"), ("Box", "BX", "Quantity"), ("Dozen", "DZ", "Quantity"),
    ("Pack", "PK", "Quantity"), ("Set", "SET", "Quantity"), ("Roll", "RL", "Quantity"),
    ("Lot", "LOT", "Quantity"), ("License", "LIC", "Quantity"), ("Hour", "HR", "Time"),
    ("Day", "DAY", "Time"), ("Month", "MTH", "Time"), ("Kilogram", "KG", "Weight"),
    ("Gram", "G", "Weight"), ("Liter", "L", "Volume"), ("Meter", "M", "Length"),
]

# code, description, natural account, uom, base price, G/S, internal noun, vendor noun, brands
CATEGORIES = [
    ("IT-HW-LAP-001", "Laptops", "1650", "Each", 1150, "G", "LAPTOP", "Notebook", ["Dell Latitude", "Lenovo ThinkPad", "HP EliteBook"]),
    ("IT-HW-DSK-002", "Desktops and workstations", "1650", "Each", 950, "G", "DESKTOP PC", "Desktop", ["Dell OptiPlex", "HP ProDesk", "Lenovo ThinkCentre"]),
    ("IT-HW-MON-003", "Monitors", "6410", "Each", 250, "G", "MONITOR", "Monitor", ["Dell P-Series", "LG UltraFine", "Samsung ViewFinity"]),
    ("IT-HW-DCK-004", "Docking stations", "6410", "Each", 180, "G", "DOCKING STATION, USB-C", "Dock", ["Dell", "Lenovo", "CalDigit"]),
    ("IT-HW-PER-005", "Keyboards and mice", "6410", "Each", 60, "G", "KEYBOARD AND MOUSE SET", "Keyboard/Mouse Combo", ["Logitech", "Microsoft", "Dell"]),
    ("IT-HW-HDS-006", "Headsets", "6410", "Each", 120, "G", "HEADSET, NOISE CANCELLING", "Headset", ["Jabra Evolve2", "Poly Voyager", "Logitech Zone"]),
    ("IT-HW-MOB-007", "Mobile phones", "6410", "Each", 700, "G", "MOBILE PHONE", "Smartphone", ["Apple iPhone", "Samsung Galaxy", "Google Pixel"]),
    ("IT-HW-TAB-008", "Tablets", "6410", "Each", 550, "G", "TABLET", "Tablet", ["Apple iPad", "Samsung Galaxy Tab", "Microsoft Surface Go"]),
    ("IT-NET-SWT-001", "Network switches", "1660", "Each", 2400, "G", "NETWORK SWITCH, 48 PORT", "Switch", ["Cisco Catalyst", "Aruba CX", "Juniper EX"]),
    ("IT-NET-WAP-002", "Wireless access points", "1660", "Each", 650, "G", "WIRELESS ACCESS POINT", "Access Point", ["Cisco Meraki MR", "Aruba AP", "Ubiquiti UniFi"]),
    ("IT-NET-CBL-003", "Network cabling", "6410", "Box", 140, "G", "CABLE, CAT6, 305M", "Cat6 Cable Box", ["Belden", "CommScope", "Panduit"]),
    ("IT-SRV-HW-001", "Servers", "1650", "Each", 8500, "G", "SERVER, RACK 2U", "Rack Server", ["Dell PowerEdge", "HPE ProLiant", "Lenovo ThinkSystem"]),
    ("IT-SRV-STO-002", "Storage arrays", "1650", "Each", 18000, "G", "STORAGE ARRAY", "Storage Array", ["NetApp AFF", "Pure Storage FlashArray", "Dell PowerStore"]),
    ("IT-CLD-INF-001", "Cloud compute", "6420", "Month", 2300, "S", "CLOUD COMPUTE SERVICE", "Compute", ["Elastic Compute", "Virtual Machines", "Compute Engine"]),
    ("IT-CLD-STO-002", "Cloud storage", "6430", "Month", 400, "S", "CLOUD STORAGE SERVICE", "Storage", ["Object Storage", "Blob Storage", "Cloud Storage"]),
    ("IT-CLD-NET-003", "Cloud networking", "6420", "Month", 300, "S", "CLOUD NETWORK SERVICE", "Data Transfer", ["Data Transfer", "Bandwidth", "Load Balancing"]),
    ("IT-CLD-DB-004", "Cloud databases", "6420", "Month", 900, "S", "CLOUD DATABASE SERVICE", "Managed Database", ["Relational Database", "SQL Database", "Cloud SQL"]),
    ("IT-CLD-BKP-005", "Cloud backup", "6430", "Month", 350, "S", "CLOUD BACKUP SERVICE", "Backup", ["Backup Vault", "Archive Storage", "Cold Storage"]),
    ("IT-SW-LIC-001", "Software licences", "6440", "License", 240, "S", "SOFTWARE LICENCE, ANNUAL", "Annual Subscription", ["Microsoft 365", "Adobe Creative Cloud", "Atlassian Jira"]),
    ("IT-SW-SAS-002", "SaaS subscriptions", "6440", "Month", 1200, "S", "SAAS SUBSCRIPTION", "Subscription", ["Salesforce Sales Cloud", "Zoom Business", "Slack Business+"]),
    ("IT-SW-MNT-003", "Software maintenance", "6480", "Each", 5000, "S", "SOFTWARE SUPPORT RENEWAL", "Support Renewal", ["Premier Support", "Enterprise Support", "Standard Support"]),
    ("IT-SVC-IMG-001", "Device imaging and setup", "6450", "Each", 500, "S", "DEVICE IMAGING SERVICE", "Imaging & Setup", ["On-site", "Remote", "Depot"]),
    ("IT-SVC-MSP-002", "Managed IT services", "6450", "Month", 4500, "S", "MANAGED SERVICE FEE", "Managed Services", ["Managed Desktop", "Managed Network", "Service Desk"]),
    ("IT-SVC-SEC-003", "Security services", "6450", "Each", 7500, "S", "SECURITY ASSESSMENT", "Security Assessment", ["Penetration Test", "Vulnerability Scan", "SOC Monitoring"]),
    ("IT-SVC-HWM-004", "Hardware maintenance", "6490", "Each", 1200, "S", "HARDWARE MAINTENANCE CONTRACT", "Maintenance", ["NBD Onsite", "4-hour Onsite", "Depot Repair"]),
    ("IT-TEL-MOB-001", "Mobile plans", "6460", "Month", 45, "S", "MOBILE SERVICE PLAN", "Mobile Plan", ["Unlimited", "Business Data", "International"]),
    ("IT-TEL-NET-002", "Internet connectivity", "6470", "Month", 1100, "S", "INTERNET LEASED LINE", "Dedicated Internet", ["1 Gbps", "500 Mbps", "10 Gbps"]),
    ("OFF-SUP-PAP-001", "Paper products", "6210", "Box", 42, "G", "PAPER, COPY, A4/LETTER", "Copy Paper", ["Hammermill", "HP Office", "Navigator"]),
    ("OFF-SUP-WRT-002", "Writing instruments", "6210", "Box", 18, "G", "PENS, BALLPOINT, BLUE", "Ballpoint Pens", ["BIC", "Pilot", "Uni-ball"]),
    ("OFF-SUP-TON-003", "Toner and ink", "6220", "Each", 110, "G", "TONER CARTRIDGE, BLACK", "Toner", ["HP LaserJet", "Brother TN", "Canon"]),
    ("OFF-SUP-FIL-004", "Filing and storage", "6210", "Pack", 25, "G", "FILE FOLDERS, LETTER", "File Folders", ["Smead", "Pendaflex", "Esselte"]),
    ("OFF-FUR-CHR-001", "Office chairs", "1610", "Each", 480, "G", "CHAIR, ERGONOMIC TASK", "Task Chair", ["Herman Miller Aeron", "Steelcase Leap", "HON Ignition"]),
    ("OFF-FUR-DSK-002", "Desks", "1610", "Each", 650, "G", "DESK, HEIGHT ADJUSTABLE", "Standing Desk", ["Uplift V2", "Steelcase Migration", "Fully Jarvis"]),
    ("OFF-FUR-STO-003", "Storage furniture", "1610", "Each", 320, "G", "CABINET, LATERAL FILE", "Lateral File", ["HON Brigade", "Steelcase", "Hirsh"]),
    ("OFF-PAN-SNK-001", "Pantry supplies", "6240", "Box", 35, "G", "PANTRY SUPPLIES, ASSORTED", "Snack Box", ["Costco Kirkland", "Nature Valley", "Haldiram"]),
    ("OFF-PAN-BEV-002", "Beverages", "6240", "Box", 28, "G", "COFFEE, GROUND, 1KG", "Coffee Beans", ["Starbucks", "Lavazza", "Blue Tokai"]),
    ("FAC-CLN-SVC-001", "Cleaning services", "6150", "Month", 3200, "S", "JANITORIAL SERVICE", "Janitorial Services", ["Nightly", "Day Porter", "Deep Clean"]),
    ("FAC-CLN-SUP-002", "Cleaning supplies", "6150", "Box", 55, "G", "CLEANING SUPPLIES, ASSORTED", "Cleaning Kit", ["Clorox", "Lysol", "3M"]),
    ("FAC-SEC-GRD-001", "Security guarding", "6160", "Month", 5400, "S", "SECURITY GUARD SERVICE", "Guard Services", ["24x7", "Business Hours", "Event"]),
    ("FAC-MNT-HVC-001", "HVAC maintenance", "6140", "Each", 850, "S", "HVAC PREVENTIVE MAINTENANCE", "HVAC Service", ["Quarterly PM", "Emergency Call-out", "Filter Change"]),
    ("FAC-MNT-ELC-002", "Electrical works", "6140", "Hour", 95, "S", "ELECTRICIAN LABOUR", "Electrical Labor", ["Standard Rate", "Overtime Rate", "Weekend Rate"]),
    ("FAC-UTL-ELE-001", "Electricity", "6120", "Month", 7800, "S", "ELECTRICITY SUPPLY", "Electric Service", ["Commercial", "Industrial", "Green Tariff"]),
    ("FAC-WST-DSP-001", "Waste disposal", "6170", "Month", 600, "S", "WASTE COLLECTION SERVICE", "Waste Pickup", ["Weekly", "Recycling", "E-waste"]),
    ("LAB-EQP-INS-001", "Lab instruments", "1670", "Each", 6400, "G", "LAB INSTRUMENT", "Instrument", ["Thermo Fisher", "Agilent", "Mettler Toledo"]),
    ("LAB-CON-GLS-002", "Lab glassware", "6920", "Pack", 85, "G", "GLASSWARE, BEAKER SET", "Beaker Set", ["Pyrex", "Kimble", "DWK Duran"]),
    ("LAB-CON-CHM-003", "Lab chemicals", "6920", "Liter", 64, "G", "REAGENT, ANALYTICAL GRADE", "Reagent", ["Sigma-Aldrich", "Merck", "Fisher Chemical"]),
    ("LAB-CON-PPE-004", "Protective equipment", "6930", "Box", 38, "G", "GLOVES, NITRILE", "Nitrile Gloves", ["Kimtech", "Ansell", "Microflex"]),
    ("MRO-TLS-HND-001", "Hand tools", "6950", "Each", 45, "G", "HAND TOOL", "Tool", ["Stanley", "Bosch", "DeWalt"]),
    ("MRO-TLS-PWR-002", "Power tools", "6950", "Each", 210, "G", "POWER DRILL, CORDLESS", "Cordless Drill", ["DeWalt", "Makita", "Bosch"]),
    ("MRO-SAF-WRK-003", "Workwear", "6940", "Each", 55, "G", "SAFETY VEST, HI-VIS", "Hi-Vis Vest", ["Ergodyne", "Radians", "3M"]),
    ("PRO-CON-MGT-001", "Management consulting", "6530", "Hour", 250, "S", "CONSULTING SERVICES", "Consulting", ["Strategy", "Operations", "Transformation"]),
    ("PRO-LEG-FEE-002", "Legal services", "6510", "Hour", 420, "S", "LEGAL SERVICES", "Legal Services", ["Corporate", "Employment", "IP"]),
    ("PRO-AUD-FEE-003", "Audit services", "6520", "Each", 25000, "S", "AUDIT FEE", "Audit Fee", ["Statutory Audit", "Tax Audit", "Internal Audit"]),
    ("PRO-REC-FEE-004", "Recruitment services", "6540", "Each", 9000, "S", "RECRUITMENT FEE", "Placement Fee", ["Permanent", "Executive Search", "Campus"]),
    ("PRO-TRN-CRS-005", "Training courses", "6710", "Each", 1500, "S", "TRAINING COURSE", "Training", ["Instructor-led", "Virtual", "Certification"]),
    ("MKT-EVT-BTH-001", "Event services", "6620", "Each", 12000, "S", "TRADE SHOW BOOTH", "Booth Package", ["10x10", "10x20", "Island"]),
    ("MKT-PRN-COL-002", "Marketing print", "6220", "Each", 1.2, "G", "BROCHURE, PRINTED", "Printed Brochure", ["Tri-fold", "Bi-fold", "Booklet"]),
    ("MKT-AGY-SVC-003", "Agency services", "6630", "Month", 8500, "S", "MARKETING AGENCY RETAINER", "Agency Retainer", ["Digital", "Creative", "PR"]),
    ("LOG-FRT-SVC-001", "Freight services", "7610", "Each", 380, "S", "FREIGHT CHARGE", "Freight", ["LTL", "Parcel", "Express"]),
    ("LOG-PKG-MAT-002", "Packaging materials", "6210", "Roll", 22, "G", "PACKING TAPE", "Packing Tape", ["Scotch", "Tesa", "Uline"]),
]

SPEC_VARIANTS_GOODS = [
    ("STANDARD", "Standard", 1.0), ("14 IN", "14\"", 1.0), ("16 IN", "16\"", 1.25),
    ("LARGE", "Large", 1.2), ("SMALL", "Small", 0.8), ("PREMIUM", "Premium", 1.5),
    ("COMPACT", "Compact", 0.9), ("HEAVY DUTY", "Heavy Duty", 1.35), ("BASIC", "Basic", 0.7),
    ("PRO", "Pro", 1.4), ("ECO", "Eco", 0.85), ("ENTERPRISE", "Enterprise", 1.6),
]
SPEC_VARIANTS_SERVICES = [
    ("STANDARD", "Standard", 1.0), ("PREMIUM", "Premium", 1.4), ("BASIC", "Basic", 0.7),
    ("US EAST", "US East", 1.0), ("EU WEST", "EU (Ireland)", 1.05), ("APAC", "Asia Pacific (Mumbai)", 0.95),
    ("TIER 1", "Tier 1", 1.0), ("TIER 2", "Tier 2", 1.3), ("ON DEMAND", "On-Demand", 1.1),
    ("RESERVED", "Reserved", 0.75), ("ANNUAL", "Annual", 11.0), ("QUARTERLY", "Quarterly", 3.0),
]


def generate(ctx: SeedContext) -> None:
    for uom, code, cls in UOMS:
        ctx.add("inv.mtl_units_of_measure_tl", unit_of_measure=uom, uom_code=code, uom_class=cls,
                base_uom_flag="Y" if uom in ("Each", "Hour", "Kilogram", "Liter", "Meter") else "N",
                unit_of_measure_tl=uom, description=uom, language="US", source_lang="US")
    ctx.ref["uom_code"] = {u: c for u, c, _ in UOMS}

    ctx.ref["categories"] = {}      # code -> dict
    ctx.ref["items"] = []           # list of dicts
    ctx.ref["items_by_category"] = {}
    for code, desc, acct, uom, price, kind, noun, vnoun, brands in CATEGORIES:
        cat_id = ctx.next_id("inv.mtl_categories_s")
        s1, s2, s3, s4 = code.split("-")
        ctx.add("inv.mtl_categories_b", category_id=cat_id, structure_id=CATEGORY_STRUCTURE_ID,
                segment1=s1, segment2=s2, segment3=s3, segment4=s4, enabled_flag="Y",
                summary_flag="N", start_date_active=START)
        ctx.add("inv.mtl_categories_tl", category_id=cat_id, language="US", source_lang="US",
                description=desc)
        cat = dict(category_id=cat_id, code=code, description=desc, account=acct, uom=uom,
                   price=price, kind=kind, noun=noun, vendor_noun=vnoun, brands=brands, family=s1 + "-" + s2)
        ctx.ref["categories"][code] = cat
        ctx.ref["items_by_category"][code] = []
        variants = SPEC_VARIANTS_GOODS if kind == "G" else SPEC_VARIANTS_SERVICES
        chosen = ctx.rnd.sample(variants, min(ctx.scale.items_per_category, len(variants)))
        for i, (spec, vspec, mult) in enumerate(chosen):
            brand = brands[i % len(brands)]
            model = f"{ctx.rnd.randint(1, 9)}{ctx.rnd.randint(100, 990)}" if kind == "G" else ""
            internal = f"{noun}, {spec}"[:240]
            vendor_text = " ".join(p for p in (brand, model, vnoun, vspec) if p)
            add_item(ctx, cat, internal, vendor_text,
                     money(Decimal(str(price)) * Decimal(str(mult)) * Decimal(str(ctx.rnd.uniform(0.9, 1.1)))))


def add_item(ctx: SeedContext, cat: dict, internal: str, vendor_text: str, price: Decimal,
             item_number: str | None = None) -> dict:
    item_id = ctx.next_id("inv.mtl_system_items_b_s")
    number = item_number or f"{cat['code'][:6]}-{item_id % 100000:05d}"
    uom = cat["uom"]
    item = dict(inventory_item_id=item_id, segment1=number, description=internal,
                vendor_text=vendor_text, price=price, uom=uom, category=cat["code"],
                category_id=cat["category_id"], account=cat["account"], kind=cat["kind"])
    for org in (207, 609):
        ctx.add("inv.mtl_system_items_b", inventory_item_id=item_id, organization_id=org,
                segment1=number, description=internal, primary_uom_code=ctx.ref["uom_code"][uom],
                primary_unit_of_measure=uom, item_type="P" if cat["kind"] == "G" else "SERVICE",
                purchasing_item_flag="Y", inventory_item_flag="N",
                list_price_per_unit=price, enabled_flag="Y", attribute1=vendor_text[:150])
        ctx.add("inv.mtl_item_categories", inventory_item_id=item_id, organization_id=org,
                category_set_id=PURCHASING_CATEGORY_SET_ID, category_id=cat["category_id"])
    ctx.ref["items"].append(item)
    ctx.ref["items_by_category"][cat["code"]].append(item)
    return item
