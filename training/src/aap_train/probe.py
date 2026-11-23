"""A made-up dataset at a real dataset's sequence lengths, to measure memory and speed without client data.

    python -m aap_train.probe --src chat-data/smoke-b --out chat-data/probe-b [--median 2800] [--max 4240]

Takes a made-up decide dataset (aap.finetune.chat decide-smoke) and pads each user message with made-up
history rows until it is about as long as a real context pack (lengths drawn around --median, the first
example at --max). Train with configs/probe-b-qlora.yaml: its scores mean nothing, its peak GPU memory
and seconds per example are what the real run will see. Measured 2026-11-23: real adapter B packs have a
median of 2.8k and a maximum of 4.2k tokens.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from pathlib import Path

CHARS_PER_TOKEN = 2.05          # of the filler below (numbers and account strings tokenise densely)
BASE_TOKENS = 520               # a smoke-b example before padding


def filler(n_chars: int, rnd: random.Random) -> str:
    rows: list[str] = []
    while sum(len(r) for r in rows) < n_chars:
        rows.append(json.dumps({
            "invoice_num": f"INV-{rnd.randint(1000, 9999)}", "gl_date": f"2025-0{rnd.randint(1, 9)}-1{rnd.randint(0, 9)}",
            "amount": f"{rnd.randint(50, 5000)}.{rnd.randint(10, 99)}",
            "lines": [{"account": f"10.20.00{rnd.randint(100, 999)}.6{rnd.randint(1000, 9999)}.00000.000.00.000",
                       "amount": f"{rnd.randint(10, 900)}.00", "typical_text": "made-up probe filler"}]}))
    return "\n\nMade-up history (length probe):\n" + "\n".join(rows)


def build(src: Path, out: Path, *, median: int = 2800, maximum: int = 4240, train: int = 64, dev: int = 4,
          seed: int = 7) -> dict:
    meta = json.loads((src / "dataset.json").read_text(encoding="utf-8"))
    rnd = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    for split, n, source in (("train", train, "train"), ("dev", dev, "dev"), ("test", 0, "dev")):
        rows = [json.loads(x) for x in (src / f"{source}.jsonl").read_text(encoding="utf-8").splitlines()]
        made = []
        for i in range(n):
            r = json.loads(json.dumps(rows[i % len(rows)]))
            tokens = maximum if i == 0 else min(maximum, max(1500, int(rnd.gauss(median, 600))))
            r["id"] = f"probe-{i}"
            r["messages"][1]["content"][0]["text"] += filler(int(max(0, tokens - BASE_TOKENS) * CHARS_PER_TOKEN), rnd)
            r["meta"] = {**r.get("meta", {}), "label": "probe"}
            made.append(r)
        p = out / f"{split}.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in made), encoding="utf-8")
        files[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()
    meta.update({"files": files, "counts": {"train": train, "dev": dev, "test": 0},
                 "source": f"probe: {src.name} padded with made-up filler (median {median}, max {maximum} tokens)"})
    (out / "dataset.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap_train.probe", description=__doc__.split("\n")[0])
    ap.add_argument("--src", type=Path, default=Path("chat-data/smoke-b"))
    ap.add_argument("--out", type=Path, default=Path("chat-data/probe-b"))
    ap.add_argument("--median", type=int, default=2800)
    ap.add_argument("--max", dest="maximum", type=int, default=4240)
    a = ap.parse_args(argv)
    m = build(a.src, a.out, median=a.median, maximum=a.maximum)
    print(f"[probe] {a.out}: {m['counts']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
