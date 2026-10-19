"""Tax ID and bank account formats: US EIN, Indian GSTIN / PAN, EU VAT, IBAN.

Used by the mock EBS seeder (to generate valid IDs) and by validation rule C6.
"""

from __future__ import annotations

import random
import re
import string

GSTIN_CHARS = string.digits + string.ascii_uppercase

# IRS campus prefixes that are valid for EINs.
EIN_PREFIXES = [
    "01", "02", "03", "04", "05", "06", "10", "11", "12", "13", "14", "15", "16", "20", "21", "22",
    "23", "24", "25", "26", "27", "30", "31", "32", "33", "34", "35", "36", "37", "38", "39", "40",
    "41", "42", "43", "44", "45", "46", "47", "48", "50", "51", "52", "53", "54", "55", "56", "57",
    "58", "59", "60", "61", "62", "63", "64", "65", "66", "67", "68", "71", "72", "73", "74", "75",
    "76", "77", "80", "81", "82", "83", "84", "85", "86", "87", "88", "90", "91", "92", "93", "94",
    "95", "98", "99",
]

# GST state codes (subset) -> state.
GST_STATE_CODES = {
    "27": "Maharashtra", "29": "Karnataka", "33": "Tamil Nadu", "36": "Telangana",
    "07": "Delhi", "24": "Gujarat", "09": "Uttar Pradesh", "19": "West Bengal",
}

EIN_RE = re.compile(r"^\d{2}-\d{7}$")
GSTIN_RE = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")
EU_VAT_RE = re.compile(r"^(AT|BE|BG|CY|CZ|DE|DK|EE|EL|ES|FI|FR|HR|HU|IE|IT|LT|LU|LV|MT|NL|PL|PT|RO|SE|SI|SK)[0-9A-Z]{8,12}$")
GB_VAT_RE = re.compile(r"^GB(\d{9}|\d{12})$")


# ------------------------------------------------------------------ US EIN
def is_valid_ein(value: str) -> bool:
    value = value.strip()
    return bool(EIN_RE.match(value)) and value[:2] in EIN_PREFIXES


def make_ein(rnd: random.Random) -> str:
    return f"{rnd.choice(EIN_PREFIXES)}-{rnd.randint(1000000, 9999999)}"


# ------------------------------------------------------------- India GSTIN
def gstin_check_char(first14: str) -> str:
    total = 0
    for i, ch in enumerate(first14):
        value = GSTIN_CHARS.index(ch)
        product = value * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    return GSTIN_CHARS[(36 - total % 36) % 36]


def is_valid_gstin(value: str) -> bool:
    value = value.strip().upper()
    if not GSTIN_RE.match(value):
        return False
    return gstin_check_char(value[:14]) == value[14]


def make_pan(rnd: random.Random, name: str, entity_type: str = "C") -> str:
    first_letter = next((c for c in name.upper() if c.isalpha()), "A")
    head = "".join(rnd.choice(string.ascii_uppercase) for _ in range(3))
    return f"{head}{entity_type}{first_letter}{rnd.randint(1000, 9999)}{rnd.choice(string.ascii_uppercase)}"


def make_gstin(rnd: random.Random, name: str, state_code: str | None = None) -> str:
    state = state_code or rnd.choice(list(GST_STATE_CODES))
    body = f"{state}{make_pan(rnd, name)}{rnd.choice('123456789')}Z"
    return body + gstin_check_char(body)


# ------------------------------------------------------------------ EU VAT
def is_valid_vat(value: str) -> bool:
    value = value.replace(" ", "").upper()
    return bool(EU_VAT_RE.match(value) or GB_VAT_RE.match(value))


def make_vat(rnd: random.Random, country: str) -> str:
    digits = {"DE": 9, "FR": 11, "NL": 12, "GB": 9, "IE": 8}.get(country, 9)
    body = "".join(rnd.choice(string.digits) for _ in range(digits))
    if country == "NL":
        body = body[:9] + "B" + body[10:]
    return f"{country}{body}"


# -------------------------------------------------------------------- IBAN
def _iban_numeric(s: str) -> int:
    return int("".join(str(int(c, 36)) for c in s))


def iban_check_digits(country: str, bban: str) -> str:
    return f"{98 - _iban_numeric(bban + country + '00') % 97:02d}"


def make_iban(rnd: random.Random, country: str) -> str:
    lengths = {"DE": 18, "FR": 23, "NL": 14, "GB": 18, "IE": 18}
    n = lengths.get(country, 18)
    if country in ("NL", "GB", "IE"):
        bban = "".join(rnd.choice(string.ascii_uppercase) for _ in range(4)) + "".join(
            rnd.choice(string.digits) for _ in range(n - 4)
        )
    else:
        bban = "".join(rnd.choice(string.digits) for _ in range(n))
    return f"{country}{iban_check_digits(country, bban)}{bban}"


def is_valid_iban(value: str) -> bool:
    value = value.replace(" ", "").upper()
    if not re.match(r"^[A-Z]{2}\d{2}[A-Z0-9]{8,30}$", value):
        return False
    return _iban_numeric(value[4:] + value[:4]) % 97 == 1


# ------------------------------------------------------------- classifier
def classify_tax_id(value: str | None) -> str | None:
    """Return 'EIN', 'GSTIN', 'VAT' or None for an unknown format."""
    if not value:
        return None
    v = value.strip().upper().replace(" ", "")
    if EIN_RE.match(v):
        return "EIN"
    if GSTIN_RE.match(v):
        return "GSTIN"
    if EU_VAT_RE.match(v) or GB_VAT_RE.match(v):
        return "VAT"
    return None


def is_valid_tax_id(value: str) -> bool:
    kind = classify_tax_id(value)
    v = value.strip().upper().replace(" ", "")
    if kind == "EIN":
        return is_valid_ein(v)
    if kind == "GSTIN":
        return is_valid_gstin(v)
    if kind == "VAT":
        return is_valid_vat(v)
    return False


def normalize_tax_id(value: str | None) -> str:
    return (value or "").strip().upper().replace(" ", "")
