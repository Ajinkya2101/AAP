"""Step 3 of the value map: the LLM selects one candidate or "none" (ARCHITECTURE 6.5).

The schema is an enum of candidate ids prepared by code plus "none", so the
LLM cannot invent an Oracle value (R-01, R-02). Every call is recorded in
agent_decision (candidates, prompt, raw output, choice, reason).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from aap.audit import audit
from aap.config import get_settings
from aap.db.models import AgentDecision
from aap.extraction.prompts import load_prompt
from aap.llm import LlmClient, LlmRequest


@dataclass
class Choice:
    id: str | None
    reason: str | None
    raw: str | None


def choose(session: Session, llm: LlmClient, *, document_id: uuid.UUID, node: str, question: str,
           value: str, candidates: list[dict[str, Any]], context: str | None = None,
           agent_run_id: uuid.UUID | None = None, allow_review: bool = False,
           line: int | None = None) -> Choice:
    """`line`: the invoice line the question is about. Not sent to the model; it is stored with the
    decision so the clerk's final value for that line can be found later (aap.finetune.export)."""
    if not candidates:
        return Choice(None, "no candidates", None)
    ids = [c["id"] for c in candidates]
    lines = [f"- {c['id']}: {c['label']}" + (f" (score {c['score']})" if "score" in c else "") for c in candidates]
    prompt = f"{question}\nInvoice value: \"{value}\"\n"
    if context:
        prompt += f"Invoice context: {context}\n"
    escape = "review" if allow_review else "none"
    prompt += "Candidates:\n" + "\n".join(lines) + f"\nAnswer with the candidate id, or \"{escape}\" if unsure."
    schema = {
        "type": "object",
        "properties": {"choice": {"enum": [*ids, escape]}, "reason": {"type": "string"}},
        "required": ["choice", "reason"],
        "additionalProperties": False,
    }
    resp = llm.ask(LlmRequest(task="choose", system=load_prompt("choose_system"),
                              content=[{"type": "text", "text": prompt}], schema=schema,
                              max_tokens=get_settings().choice_max_tokens,
                              context={"candidates": candidates}))
    chosen = resp.data.get("choice")
    reason = resp.data.get("reason")
    session.add(AgentDecision(agent_run_id=agent_run_id, document_id=document_id, node=node,
                              inputs={"value": value, "context": context, "line": line}, candidates=candidates,
                              prompt=prompt,
                              raw_output=resp.raw, chosen={"choice": chosen}, reason=reason, model=resp.model))
    audit(session, node, document_id=document_id, actor="agents" if node.startswith("agent.") else "pipeline",
          chosen=chosen, reason=reason, model=resp.model)
    return Choice(None if chosen in (None, "none", "review") else str(chosen), reason, resp.raw)
