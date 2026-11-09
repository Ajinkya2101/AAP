"""Can this machine train? (docs/PLAN-TRAINING-PIPELINE.md T0)

    python -m aap_train.envcheck [--min-free-gb 18] [--json]

Checks: PyTorch and CUDA, the GPU and its compute capability (Blackwell is sm_120 and needs a CUDA
12.8 build), bf16, a 4-bit (bitsandbytes NF4) layer run on the GPU, the attention backends (SDPA,
flash-attention), free GPU memory and the processes holding it (vLLM is stopped while training), and
free disk for checkpoints. Exit code 1 when a required check fails.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


def _gpu_processes() -> list[str]:
    try:
        out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid,process_name,used_memory",
                              "--format=csv,noheader"], capture_output=True, text=True, timeout=15).stdout
        return [x.strip() for x in out.splitlines() if x.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def run_checks(min_free_gb: float = 18.0, runs_dir: Path = Path("runs")) -> dict[str, Any]:
    r: dict[str, Any] = {"python": sys.version.split()[0], "platform": platform.platform(), "checks": {}}
    checks = r["checks"]

    def check(name: str, ok: bool, detail: Any, required: bool = True) -> None:
        checks[name] = {"ok": bool(ok), "required": required, "detail": detail}

    try:
        import torch
    except (ImportError, OSError) as exc:      # OSError: a DLL blocked (Windows Application Control) or missing
        check("torch", False, f"{exc!r}"[:300] + ("; on a Windows host with Application Control, run in the "
                                                  "training container (training/Dockerfile)"
                                                  if "4551" in str(exc) else ""))
        r["ok"] = False
        return r
    r["torch"] = torch.__version__
    r["cuda_build"] = torch.version.cuda
    check("cuda available", torch.cuda.is_available(), torch.version.cuda)
    if not torch.cuda.is_available():
        return r
    props = torch.cuda.get_device_properties(0)
    cap = f"sm_{props.major}{props.minor}"
    archs = torch.cuda.get_arch_list()
    r["gpu"] = {"name": props.name, "capability": cap, "total_gb": round(props.total_memory / 2**30, 1)}
    check("torch built for this GPU", cap in archs or f"compute_{props.major}{props.minor}" in archs,
          f"{cap}; build archs {archs}")
    check("bf16", torch.cuda.is_bf16_supported(), "bfloat16 compute")
    free, total = torch.cuda.mem_get_info(0)
    r["gpu"]["free_gb"] = round(free / 2**30, 1)
    procs = _gpu_processes()
    check(f"free GPU memory >= {min_free_gb:g} GB", free / 2**30 >= min_free_gb,
          {"free_gb": round(free / 2**30, 1), "processes": procs or "none listed (WSL / Docker GPUs are not listed)"})
    try:
        x = torch.randn(256, 256, device="cuda", dtype=torch.bfloat16)
        (x @ x).sum().item()
        check("bf16 matmul on the GPU", True, "ok")
    except Exception as exc:  # noqa: BLE001
        check("bf16 matmul on the GPU", False, repr(exc))
    try:
        import bitsandbytes as bnb

        layer = bnb.nn.Linear4bit(256, 256, bias=False, compute_dtype=torch.bfloat16, quant_type="nf4").to("cuda")
        y = layer(torch.randn(4, 256, device="cuda", dtype=torch.bfloat16))
        check("bitsandbytes 4-bit (NF4) on the GPU", y.shape == (4, 256), bnb.__version__)
    except Exception as exc:  # noqa: BLE001
        check("bitsandbytes 4-bit (NF4) on the GPU", False, repr(exc)[:300])
    try:
        q = torch.randn(1, 4, 64, 32, device="cuda", dtype=torch.bfloat16)
        torch.nn.functional.scaled_dot_product_attention(q, q, q)
        check("SDPA attention", True, "ok")
    except Exception as exc:  # noqa: BLE001
        check("SDPA attention", False, repr(exc))
    try:
        import flash_attn  # noqa: F401

        check("flash-attention 2", True, "installed", required=False)
    except ImportError:
        check("flash-attention 2", False, "not installed: SDPA is used (model.attn: sdpa)", required=False)
    for mod in ("transformers", "peft", "accelerate"):
        try:
            r[mod] = __import__(mod).__version__
        except ImportError as exc:
            check(mod, False, str(exc))
    runs_dir.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(runs_dir).free / 2**30
    check("free disk for checkpoints >= 40 GB", disk >= 40, f"{disk:.0f} GB at {runs_dir.resolve()}")
    torch.cuda.empty_cache()
    r["ok"] = all(c["ok"] for c in checks.values() if c["required"])
    return r


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m aap_train.envcheck", description=__doc__.split("\n")[0])
    ap.add_argument("--min-free-gb", type=float, default=18.0, help="QLoRA on the 7B base with page images")
    ap.add_argument("--runs-dir", type=Path, default=Path("runs"))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = run_checks(a.min_free_gb, a.runs_dir)
    if a.json:
        print(json.dumps(r, indent=2, default=str))
    else:
        print(f"python {r['python']}, torch {r.get('torch')} (CUDA {r.get('cuda_build')}), "
              f"transformers {r.get('transformers')}, peft {r.get('peft')}")
        if r.get("gpu"):
            g = r["gpu"]
            print(f"GPU {g['name']} {g['capability']}, {g['total_gb']} GB, {g.get('free_gb')} GB free")
        for name, c in r["checks"].items():
            mark = "ok  " if c["ok"] else ("FAIL" if c["required"] else "no  ")
            print(f"  [{mark}] {name}: {c['detail']}")
        print("ready to train" if r.get("ok") else "NOT ready to train (see FAIL above)")
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
