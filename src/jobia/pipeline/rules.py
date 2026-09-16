"""L2: filtros deterministas de config/rules.yaml. Sin LLM, sin red.

title_rules (R0-R6) descarta por titulo/empresa antes de gastar nada en
embeddings o LLM: R0 excepciones -> R1 kill por credencial -> R2 kill por
nivel excesivo (solo si abre el titulo) -> R3 relevancia (dominio+seniority
o arquetipo) -> R4 clase de empresa -> R5 puntuacion -> R6 banda
(PASS/REVIEW/KILL). Solo KILL se descarta; PASS y REVIEW siguen hacia L3/L4
con la banda y el score anotados en Job.flags.

Todo match usa limite de palabra (\\b), no solo substring: "cto" no puede
disparar dentro de "victory" ni "hr" dentro de cualquier palabra que lo
contenga. Ese bug (require_any_in_title matcheaba "ia" dentro de
"specialist") es lo que colaba ofertas irrelevantes en la version anterior.

Las yellow_flag_regex son independientes de R0-R6: no descartan, solo
anotan Job.flags para que L4 las tenga en cuenta al puntuar.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import yaml

from jobia.models import Job


def load_rules(path: str | Path = "config/rules.yaml") -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _find(norm_text: str, terms: list[str]) -> str | None:
    """Primer termino de `terms` que aparece en `norm_text` con limite de
    palabra. Los propios terminos se normalizan igual que el texto."""
    for t in terms:
        pattern = r"\b" + re.escape(_normalize(t)) + r"\b"
        if re.search(pattern, norm_text):
            return t
    return None


def _find_conditional(norm_text: str, terms: list, domain_present: bool) -> str | None:
    """Como _find, pero cada entrada puede ser un string o un
    {term, unless_domain}: si unless_domain=True y ya hay un termino de
    dominio en el texto, esa entrada no cuenta como kill (ver R1, caso
    "AI Solutions Architect" vs "Solutions Architect" generico)."""
    for t in terms:
        if isinstance(t, dict):
            if t.get("unless_domain") and domain_present:
                continue
            term = t["term"]
        else:
            term = t
        pattern = r"\b" + re.escape(_normalize(term)) + r"\b"
        if re.search(pattern, norm_text):
            return term
    return None


def _find_company(norm_company: str, names: list[str]) -> str | None:
    for n in names:
        pattern = r"\b" + re.escape(_normalize(n)) + r"\b"
        if re.search(pattern, norm_company):
            return n
    return None


def _kill_seniority_excesivo(norm_title: str, terms: list[str]) -> str | None:
    """R2: solo cuenta si el termino abre el titulo."""
    for t in terms:
        if t == "chief * officer":
            if re.match(r"^chief\s+\w+\s+officer\b", norm_title):
                return t
            continue
        pattern = r"^" + re.escape(_normalize(t)) + r"\b"
        if re.match(pattern, norm_title):
            return t
    return None


def score_title(title: str, company: str, title_rules: dict) -> tuple[str, int, list[str]]:
    """Aplica R0-R6 sobre un titulo+empresa. Devuelve (banda, score, razones)."""
    norm_title = _normalize(title)
    norm_company = _normalize(company)
    reasons: list[str] = []
    rel = title_rules["relevancia"]
    domain_present = bool(_find(norm_title, rel["dominio"]))

    exempt = bool(_find(norm_title, title_rules["exceptions"]))
    if exempt:
        reasons.append("R0 excepcion: exento de R1-R3")
    else:
        kc = title_rules["kill_credential"]
        skip_r1 = bool(_find(norm_title, kc.get("no_incluir", [])))
        if not skip_r1:
            for bucket, terms in kc.items():
                if bucket == "no_incluir":
                    continue
                hit = _find_conditional(norm_title, terms, domain_present)
                if hit:
                    return "KILL", 0, [f"R1 kill_credential/{bucket}: '{hit}'"]

        hit = _kill_seniority_excesivo(norm_title, title_rules["kill_seniority_excesivo"]["terms"])
        if hit:
            return "KILL", 0, [f"R2 nivel excesivo al inicio: '{hit}'"]

        seniority = _find(norm_title, rel["seniority"])
        arquetipo = _find(norm_title, rel["arquetipos"])
        # Seniority sola basta (no exige dominio en el propio titulo): un
        # "Migration Leader - CDAIO" o un "Service Delivery Manager" pueden
        # ser el puesto correcto sin que el titulo lo diga explicitamente
        # -- eso lo revela la descripcion en L4, no el titulo. R1 ya filtro
        # ventas/producto/IC/fuera de dominio antes de llegar aqui.
        if not (seniority or arquetipo):
            return "KILL", 0, ["R3: sin seniority ni arquetipo"]

    score = title_rules["scoring"]["base"]
    for tier_name, tier in title_rules["empresas"].items():
        hit = _find_company(norm_company, tier["names"])
        if hit:
            if tier.get("kill"):
                return "KILL", 0, [f"R4 empresa/{tier_name}: '{hit}'"]
            pts = tier.get("penalty", 0)
            score += pts
            reasons.append(f"R4 empresa/{tier_name}: '{hit}' ({pts:+d})")
            break

    for rule in title_rules["scoring"]["bonus"]:
        hit = _find(norm_title, rule["match"])
        if hit:
            score += rule["points"]
            reasons.append(f"R5 bonus '{hit}' ({rule['points']:+d})")
    for rule in title_rules["scoring"]["penalty"]:
        hit = _find(norm_title, rule["match"])
        if not hit:
            continue
        if rule.get("unless_domain") and domain_present:
            continue
        score += rule["points"]
        reasons.append(f"R5 penalty '{hit}' ({rule['points']:+d})")

    bands = title_rules["scoring"]["bands"]
    if score >= bands["pass_min"]:
        band = "PASS"
    elif score >= bands["review_min"]:
        band = "REVIEW"
    else:
        band = "KILL"
    reasons.append(f"R6: score={score} -> {band}")
    return band, score, reasons


def _kill_bucket(reasons: list[str]) -> str:
    """Reduce las razones de score_title a una etiqueta corta para el
    desglose del dashboard (RunReport.kill_breakdown). Los kills de R1-R4
    devuelven una sola razon (ver score_title); si el kill llego por
    puntuacion (R6) hay varias y la ultima es la que importa."""
    first = reasons[0]
    if first.startswith("R1 kill_credential/"):
        return f"R1:{first.split('/', 1)[1].split(':', 1)[0]}"
    if first.startswith("R2"):
        return "R2"
    if first.startswith("R3"):
        return "R3"
    if first.startswith("R4 empresa/"):
        return f"R4:{first.split('/', 1)[1].split(':', 1)[0]}"
    return "R6:score_bajo"


def apply(jobs: list[Job], rules: dict) -> tuple[list[Job], dict[str, int]]:
    title_rules = rules["title_rules"]
    yellow_flag = [re.compile(p) for p in rules.get("yellow_flag_regex", [])]

    out: list[Job] = []
    kill_breakdown: dict[str, int] = {}
    for j in jobs:
        band, score, reasons = score_title(j.title or "", j.company or "", title_rules)
        if band == "KILL":
            bucket = _kill_bucket(reasons)
            kill_breakdown[bucket] = kill_breakdown.get(bucket, 0) + 1
            continue

        j.flags.extend(f"L2 {r}" for r in reasons)
        j.flags.append(f"L2 banda={band} score={score}")

        for p in yellow_flag:
            if p.search(j.title or ""):
                j.flags.append(f"L2 yellow_flag: coincide '{p.pattern}' en titulo")

        out.append(j)
    return out, kill_breakdown
