import random

from aap.common.taxid import (
    classify_tax_id,
    is_valid_gstin,
    is_valid_iban,
    is_valid_tax_id,
    make_gstin,
    make_iban,
)


def test_gstin_generation_is_valid():
    rnd = random.Random(1)
    for _ in range(200):
        g = make_gstin(rnd, "Acme Traders")
        assert is_valid_gstin(g), g
        assert classify_tax_id(g) == "GSTIN"


def test_gstin_checksum_detects_typo():
    g = make_gstin(random.Random(2), "Acme")
    bad = g[:5] + ("A" if g[5] != "A" else "B") + g[6:]
    assert not is_valid_gstin(bad)


def test_iban():
    rnd = random.Random(3)
    for country in ("DE", "NL", "GB", "IE", "FR"):
        iban = make_iban(rnd, country)
        assert is_valid_iban(iban), iban
    assert is_valid_iban("DE89370400440532013000")
    assert not is_valid_iban("DE89370400440532013001")


def test_ein_and_vat():
    assert is_valid_tax_id("84-1234567")
    assert not is_valid_tax_id("00-1234567")      # 00 is not an IRS prefix
    assert is_valid_tax_id("DE287445193")
    assert classify_tax_id("hello") is None
