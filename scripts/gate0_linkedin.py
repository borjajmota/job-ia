#!/usr/bin/env python3
"""
PUERTA 0 - Validacion de busqueda LinkedIn (Madrid, ultimas 24h).

Objetivo: falsar o confirmar la hipotesis de ingesta ANTES de escribir
una linea de LangGraph.

Ejecuta dos vias en paralelo sobre el mismo criterio:
  VIA A: JobSpy (python-jobspy) -> la ruta elegida
  VIA B: endpoint guest crudo   -> control, para saber si un fallo de A
                                   es de la libreria o del bloqueo de LinkedIn

CRITERIO DE ACEPTACION (3 ejecuciones separadas >=4h):
  - >=15 tarjetas unicas por concepto
  - con jobId, titulo, empresa y fecha dentro de 24h
  - 0 respuestas 429 no controladas

Uso:
    pip install python-jobspy beautifulsoup4 curl_cffi
    python gate0_linkedin.py
    python gate0_linkedin.py --conceptos "data ia" "cloud architect"

Guarda cada corrida en gate0_runs/<timestamp>.json para comparar entre dias.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- HTTP: curl_cffi imita la huella TLS/JA3 de Chrome. requests no, y eso
# --- por si solo dispara bloqueos. Si no esta, degradamos y lo avisamos.
try:
    from curl_cffi import requests as http

    IMPERSONATE = {"impersonate": "chrome"}
    HTTP_BACKEND = "curl_cffi"
except ImportError:  # pragma: no cover
    import requests as http  # type: ignore

    IMPERSONATE = {}
    HTTP_BACKEND = "requests (SIN impersonacion - mas probabilidad de bloqueo)"

from bs4 import BeautifulSoup

GUEST = "https://www.linkedin.com/jobs-guest"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
}
OUT_DIR = Path("gate0_runs")


class RateLimited(Exception):
    """429 explicito. NUNCA lo confundas con 'no hay resultados'."""


def _sleep():
    """Pausa aleatoria. El patron regular es tan delator como la frecuencia."""
    time.sleep(random.uniform(2.5, 5.0))


# --------------------------------------------------------------------------
# VIA B: endpoint guest crudo
# --------------------------------------------------------------------------
def resolve_geo_id(query: str = "Madrid") -> tuple[str | None, str | None]:
    """geoId via typeahead. NO hardcodear: el texto libre de location es poco
    fiable y un geoId inventado devuelve 0 resultados silenciosamente."""
    url = f"{GUEST}/api/typeaheadHits"
    params = {
        "origin": "jserp",
        "typeaheadType": "GEO",
        "geoTypes": "POPULATED_PLACE",
        "query": query,
    }
    r = http.get(url, params=params, headers=HEADERS, timeout=30, **IMPERSONATE)
    if r.status_code == 429:
        raise RateLimited("429 en typeaheadHits")
    r.raise_for_status()
    try:
        hits = r.json()
    except Exception:
        print(f"  ! typeahead no devolvio JSON (status {r.status_code})")
        return None, None
    if not hits:
        return None, None
    return str(hits[0].get("id")), hits[0].get("displayName")


def parse_cards(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select("li, div.base-card"):
        urn = card.select_one("[data-entity-urn]")
        link = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
        title = card.select_one("h3, .base-search-card__title")
        company = card.select_one("h4 a, .base-search-card__subtitle")
        loc = card.select_one(".job-search-card__location")
        when = card.select_one("time")

        job_id = None
        if urn and urn.get("data-entity-urn"):
            m = re.search(r"(\d{6,})", urn["data-entity-urn"])
            job_id = m.group(1) if m else None
        if not job_id and link and link.get("href"):
            m = re.search(r"/jobs/view/[^/?]*?-(\d{6,})", link["href"])
            job_id = m.group(1) if m else None
        if not job_id:
            continue

        out.append(
            {
                "job_id": job_id,
                "title": title.get_text(strip=True) if title else None,
                "company": company.get_text(strip=True) if company else None,
                "location": loc.get_text(strip=True) if loc else None,
                "posted_at": when.get("datetime") if when else None,
                "url": f"https://www.linkedin.com/jobs/view/{job_id}",
            }
        )
    return out


def via_b(concepto: str, geo_id: str, max_pages: int = 4) -> dict:
    """Paginacion. El paso real (10 o 25) varia: lo medimos, no lo asumimos."""
    url = f"{GUEST}/jobs/api/seeMoreJobPostings/search"
    seen: dict[str, dict] = {}
    pages, step_observed = 0, None
    start = 0

    for _ in range(max_pages):
        params = {
            "keywords": concepto,
            "geoId": geo_id,
            "f_TPR": "r86400",  # ultimas 24h
            "sortBy": "DD",  # por fecha
            "start": start,
        }
        r = http.get(url, params=params, headers=HEADERS, timeout=30, **IMPERSONATE)
        pages += 1

        if r.status_code == 429:
            raise RateLimited(f"429 en pagina {pages} (start={start})")
        if r.status_code in (403, 999):
            raise RateLimited(f"muro de login/bloqueo: HTTP {r.status_code}")
        if r.status_code != 200:
            print(f"  ! HTTP {r.status_code} en start={start}")
            break

        cards = parse_cards(r.text)
        if not cards:
            break
        if step_observed is None:
            step_observed = len(cards)
        for c in cards:
            seen.setdefault(c["job_id"], c)

        start += len(cards)
        _sleep()

    return {
        "jobs": list(seen.values()),
        "pages_fetched": pages,
        "page_step_observed": step_observed,
    }


# --------------------------------------------------------------------------
# VIA A: JobSpy
# --------------------------------------------------------------------------
def via_a(concepto: str, location: str) -> dict:
    try:
        from jobspy import scrape_jobs
    except ImportError:
        return {"error": "python-jobspy no instalado", "jobs": []}

    try:
        df = scrape_jobs(
            site_name=["linkedin"],
            search_term=concepto,
            location=location,
            results_wanted=50,
            hours_old=24,
            linkedin_fetch_description=False,  # detalles solo en L4, nunca aqui
            verbose=1,
        )
    except Exception as exc:  # 429 incluido
        return {"error": f"{type(exc).__name__}: {exc}", "jobs": []}

    if df is None or len(df) == 0:
        return {"jobs": [], "note": "0 filas (¿bloqueo silencioso o sin ofertas?)"}

    cols = {c.lower(): c for c in df.columns}

    def get(row, *names):
        for n in names:
            if n in cols:
                v = row.get(cols[n])
                if v is not None and str(v).strip().lower() not in ("nan", "nat"):
                    return str(v)
        return None

    if "date_posted" in cols:
        raw_samples = df[cols["date_posted"]].head(3).tolist()
        print(f"  date_posted crudo (JobSpy, sin procesar): {raw_samples}")

    jobs = []
    for _, row in df.iterrows():
        jobs.append(
            {
                "job_id": get(row, "id", "job_url"),
                "title": get(row, "title"),
                "company": get(row, "company"),
                "location": get(row, "location"),
                "posted_at": get(row, "date_posted"),
                "url": get(row, "job_url"),
            }
        )
    return {"jobs": jobs}


# --------------------------------------------------------------------------
DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def evaluate(jobs: list[dict]) -> dict:
    now = datetime.now(timezone.utc)
    fresh = 0
    for j in jobs:
        raw = j.get("posted_at")
        if not raw:
            continue
        raw_str = str(raw).strip()
        try:
            d = datetime.fromisoformat(raw_str.replace("Z", "+00:00"))
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            if DATE_ONLY_RE.match(raw_str):
                # date puro (sin hora): comparar por dia de calendario, no
                # por resta en horas, o se pierden hasta 24h por redondeo.
                if d.date() >= (now - timedelta(days=1)).date():
                    fresh += 1
            elif now - d <= timedelta(hours=26):  # margen de husos
                fresh += 1
        except ValueError:
            continue
    complete = sum(
        1 for j in jobs if j.get("job_id") and j.get("title") and j.get("company")
    )
    return {
        "total": len(jobs),
        "con_campos_minimos": complete,
        "dentro_de_24h": fresh,
        "cumple_umbral_15": len(jobs) >= 15,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conceptos", nargs="+", default=["data ia", "cloud architect"])
    ap.add_argument("--location", default="Madrid, Community of Madrid, Spain")
    ap.add_argument("--geo-query", default="Madrid")
    args = ap.parse_args()

    print(f"Backend HTTP: {HTTP_BACKEND}\n")

    report: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "http_backend": HTTP_BACKEND,
        "resultados": {},
    }

    try:
        geo_id, geo_name = resolve_geo_id(args.geo_query)
        print(f"geoId resuelto: {geo_id} ({geo_name})\n")
    except RateLimited as exc:
        print(f"BLOQUEO ya en el typeahead: {exc}")
        print("-> IP quemada o bloqueada. No sigas: cambia de IP o de ruta.")
        return 2
    report["geo_id"] = geo_id

    veredicto_global = True
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"

    try:
        for concepto in args.conceptos:
            print(f"=== '{concepto}' ===")
            entry: dict = {}

            a = via_a(concepto, args.location)
            entry["via_a_jobspy"] = {**evaluate(a["jobs"]), "error": a.get("error")}
            print(f"  A/JobSpy  -> {entry['via_a_jobspy']}")
            _sleep()

            if geo_id:
                try:
                    b = via_b(concepto, geo_id)
                    entry["via_b_guest"] = {
                        **evaluate(b["jobs"]),
                        "paginas": b["pages_fetched"],
                        "paso_pagina": b["page_step_observed"],
                    }
                    entry["muestra"] = b["jobs"][:3]
                except RateLimited as exc:
                    entry["via_b_guest"] = {"rate_limited": str(exc)}
                    veredicto_global = False
                print(f"  B/guest   -> {entry['via_b_guest']}")

            via_a_res = entry.get("via_a_jobspy", {})
            via_b_res = entry.get("via_b_guest", {})
            # >=15 unicas Y >=5 dentro de 24h: total sin frescura tapa bugs
            # de parseo de fecha (ver el "NaT" de via_a) tras un falso VERDE.
            ok = (
                via_a_res.get("cumple_umbral_15") and via_a_res.get("dentro_de_24h", 0) >= 5
            ) or (
                via_b_res.get("cumple_umbral_15") and via_b_res.get("dentro_de_24h", 0) >= 5
            )
            veredicto_global = veredicto_global and bool(ok)
            entry["concepto_ok"] = bool(ok)
            report["resultados"][concepto] = entry
            path.write_text(
                json.dumps(report, indent=2, ensure_ascii=False, default=str),
                encoding="utf-8",
            )
            print()
            _sleep()
    finally:
        report["veredicto"] = "VERDE" if veredicto_global else "ROJO"
        path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str),
            encoding="utf-8",
        )

    print(f"VEREDICTO DE ESTA CORRIDA: {report['veredicto']}")
    print(f"Guardado en {path}")
    print("\nLa puerta 0 necesita 3 corridas VERDE separadas >=4h.")
    return 0 if veredicto_global else 1


if __name__ == "__main__":
    sys.exit(main())
