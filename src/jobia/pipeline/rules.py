"""L2: filtros deterministas de config/rules.yaml. Sin LLM, sin red.

Descarta por titulo/empresa antes de gastar nada en embeddings o LLM.
Las yellow_flag_regex no descartan: quedan anotadas en Job.flags para que
L4 las tenga en cuenta al puntuar.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from jobia.models import Job


def load_rules(path: str | Path = "config/rules.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def apply(jobs: list[Job], rules: dict) -> list[Job]:
    exclude_title = [re.compile(p) for p in rules.get("exclude_title_regex", [])]
    exclude_companies = {c.strip().lower() for c in rules.get("exclude_companies", [])}
    require_any = [t.lower() for t in rules.get("require_any_in_title", [])]
    yellow_flag = [re.compile(p) for p in rules.get("yellow_flag_regex", [])]

    out: list[Job] = []
    for j in jobs:
        title = j.title or ""
        company = (j.company or "").strip().lower()

        if any(p.search(title) for p in exclude_title):
            continue
        if company in exclude_companies:
            continue
        if require_any and not any(t in title.lower() for t in require_any):
            continue

        for p in yellow_flag:
            if p.search(title):
                j.flags.append(f"L2 yellow_flag: coincide '{p.pattern}' en titulo")

        out.append(j)
    return out
