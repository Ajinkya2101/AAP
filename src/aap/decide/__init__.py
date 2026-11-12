"""Deciding the Oracle fields of an invoice with the model (PLAN-MODEL-JSON, adapter B).

- `history`: the supplier facts and the keyed AP history the context pack is built from, read from a
  client delivery (datasets, offline evaluation) or from the application database (production).
- `context`: the context pack, "as of" the invoice's GL date (§4).
- `schema`: the per-invoice answer schema whose Oracle values are restricted to the candidates (§2, §5).
- `request`: the request production sends and the training dataset stores, built by one function.
- `node`: the step after the agents that asks the model and compares its answer with the code's.
"""
