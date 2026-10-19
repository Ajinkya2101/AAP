"""Mock Oracle EBS in PostgreSQL (plan section 3).

Treated as an external system: application code reaches it only through
aap.adapters (R-06). The seeder, the import program and the dev CLI are the
only modules that write to it directly.
"""
