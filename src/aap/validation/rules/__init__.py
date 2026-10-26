"""Rule modules. Importing this package registers every rule, in this order."""

from . import (  # noqa: F401
    r01_required,
    r02_maths,
    r03_taxid,
    r04_supplier,
    r05_dates_period,
    r06_duplicate,
    r07_bank,
    r08_source,
    r09_mapping,
    r10_po,
    r11_fx,
    r12_reference,
    r13_invoice_type,
)
