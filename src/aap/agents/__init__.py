"""Agent layer (plan section 8): LangGraph nodes that call typed tools in code.

R-01 holds: the LLM never receives tool definitions. It only answers small
schemas (an enum of candidates prepared by code, or {subject, body} for email
wording, or {po_number} for reply parsing).
"""
