"""Client data deliveries (extracts and invoice attachments) as files, without access to any EBS.

    client-data/kk/<delivery>/      one folder per delivery, kept as received (git-ignored)
        *.csv | *.xlsx              EBS table extracts (tables.py: which tables, which columns)
        attachments/                invoice files named by INVOICE_ID (4783348.pdf, 4783348_1.pdf)
        manifest.json               written by the checker: files, SHA-256, rows per table

`python -m aap.clientdata.check <delivery>` reads a delivery and reports what does not join or add
up (IMPLEMENTATION-PLAN-CLIENT-DATA P1). `python -m aap.clientdata.fixtures <dir>` writes a made-up
delivery with the same layout, for building and testing before the real one arrives.
"""
