"""Adaptador LinkedIn: JobSpy como via principal, endpoint guest como respaldo.

Correcciones sobre la v1 del proyecto (ver DISENO.md, seccion 1):
  1. curl_cffi con huella de Chrome  -> la v1 usaba `requests` pelado
  2. SourceBlocked explicito en 429/403/999 -> la v1 hacia `except Exception`
  3. geoId resuelto por typeahead y cacheado -> la v1 lo tenia hardcodeado
  4. Cache en disco + modo replay -> desarrollar sin volver a pegarle a LinkedIn
"""
from __future__ import annotations

import json
import random
import re
import time
from datetime import UTC, datetime
from pathlib import Path

from bs4 import BeautifulSoup

from jobia.models import Job
from jobia.sources.base import JobSource, SourceBlocked

try:
    from curl_cffi import requests as http
    _IMP = {"impersonate": "chrome"}
except ImportError:  # degradado: funciona, pero te bloquean antes
    import requests as http  # type: ignore
    _IMP = {}

GUEST = "https://www.linkedin.com/jobs-guest"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Referer": "https://www.linkedin.com/jobs/search/",
}
BLOCK_CODES = {429, 403, 999}


def _pause() -> None:
    time.sleep(random.uniform(2.5, 5.0))


class LinkedInSource(JobSource):
    name = "linkedin"

    def __init__(self, cache_dir: str | Path = "data/cache", replay: bool = False):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.replay = replay
        self._geo_id: str | None = None
        self._searched_once = False

    # ---------------- geoId ----------------
    def geo_id(self, geo_query: str) -> str:
        if self._geo_id:
            return self._geo_id
        cached = self.cache / f"geo_{geo_query.lower()}.txt"
        if cached.exists():
            self._geo_id = cached.read_text().strip()
            return self._geo_id
        r = http.get(f"{GUEST}/api/typeaheadHits", headers=HEADERS, timeout=30,
                     params={"origin": "jserp", "typeaheadType": "GEO",
                             "geoTypes": "POPULATED_PLACE", "query": geo_query}, **_IMP)
        if r.status_code in BLOCK_CODES:
            raise SourceBlocked(f"HTTP {r.status_code} resolviendo geoId")
        hits = r.json()
        if not hits:
            raise SourceBlocked(f"typeahead sin resultados para '{geo_query}'")
        self._geo_id = str(hits[0]["id"])
        cached.write_text(self._geo_id)
        return self._geo_id

    # ---------------- via principal ----------------
    def search(self, term, query_id, *, location, geo_query, hours_old, limit) -> list[Job]:
        # UTC, no hora local: el resto del codebase (run_id, timestamps de
        # BD) razona en UTC, y mezclar con hora local aqui podia desalinear
        # la clave de cache justo en el borde del cambio de dia.
        key = self.cache / f"search_{query_id}_{datetime.now(UTC):%Y%m%d}.json"
        if self.replay and key.exists():
            return [Job(**j) for j in json.loads(key.read_text(encoding="utf-8"))]

        if self._searched_once:
            # Pausa entre queries: antes no existia ninguna, y hoy son 10 al
            # dia en vez de 5. No pausar aqui era la unica peticion en serie
            # sin jitter de todo el modulo.
            _pause()
        self._searched_once = True

        try:
            jobs = self._via_jobspy(term, query_id, location, hours_old, limit)
        except SourceBlocked:
            raise
        except Exception:
            jobs = []

        if not jobs:  # contraste con el endpoint crudo antes de declarar "0"
            _pause()
            jobs = self._via_guest(term, query_id, geo_query, limit)

        key.write_text(
            json.dumps([j.model_dump(mode="json") for j in jobs], ensure_ascii=False),
            encoding="utf-8",
        )
        return jobs

    def _via_jobspy(self, term, query_id, location, hours_old, limit) -> list[Job]:
        from jobspy import scrape_jobs
        df = scrape_jobs(site_name=["linkedin"], search_term=term, location=location,
                         results_wanted=limit, hours_old=hours_old,
                         linkedin_fetch_description=False)
        if df is None or len(df) == 0:
            return []
        out: list[Job] = []
        for _, r in df.iterrows():
            url = str(r.get("job_url") or "")
            m = re.search(r"/jobs/view/(?:[^/?]*?-)?(\d{6,})", url)
            if not m:
                continue
            out.append(Job(job_id=m.group(1), title=str(r.get("title") or ""),
                           company=str(r.get("company") or ""),
                           location=str(r.get("location") or "") or None,
                           url=f"https://www.linkedin.com/jobs/view/{m.group(1)}",
                           posted_at=_parse_dt(r.get("date_posted")), query_id=query_id))
        return out

    # ---------------- respaldo / control ----------------
    def _via_guest(self, term, query_id, geo_query, limit, max_pages: int = 4) -> list[Job]:
        gid, seen, start = self.geo_id(geo_query), {}, 0
        for _ in range(max_pages):
            r = http.get(f"{GUEST}/jobs/api/seeMoreJobPostings/search", headers=HEADERS,
                         timeout=30, params={"keywords": term, "geoId": gid,
                                             "f_TPR": "r86400", "sortBy": "DD",
                                             "start": start}, **_IMP)
            if r.status_code in BLOCK_CODES:
                raise SourceBlocked(f"HTTP {r.status_code} en start={start}")
            if r.status_code != 200:
                break
            cards = self._parse(r.text, query_id)
            if not cards:
                break
            for c in cards:
                seen.setdefault(c.job_id, c)
            if len(seen) >= limit:
                break
            start += len(cards)
            _pause()
        return list(seen.values())

    @staticmethod
    def _parse(html: str, query_id: str) -> list[Job]:
        out = []
        for card in BeautifulSoup(html, "html.parser").select("li, div.base-card"):
            urn = card.select_one("[data-entity-urn]")
            link = card.select_one("a.base-card__full-link, a[href*='/jobs/view/']")
            jid = None
            if urn:
                m = re.search(r"(\d{6,})", urn.get("data-entity-urn", ""))
                jid = m.group(1) if m else None
            if not jid and link:
                m = re.search(r"-(\d{6,})", link.get("href", ""))
                jid = m.group(1) if m else None
            title = card.select_one("h3, .base-search-card__title")
            company = card.select_one("h4 a, .base-search-card__subtitle") or card.select_one("h4")
            if not (jid and title and company):
                continue
            loc = card.select_one(".job-search-card__location")
            when = card.select_one("time")
            out.append(Job(job_id=jid, title=title.get_text(strip=True),
                           company=company.get_text(strip=True),
                           location=loc.get_text(strip=True) if loc else None,
                           url=f"https://www.linkedin.com/jobs/view/{jid}",
                           posted_at=_parse_dt(when.get("datetime") if when else None),
                           query_id=query_id))
        return out

    def fetch_description(self, job: Job) -> str | None:
        cached = self.cache / f"desc_{job.job_id}.txt"
        if cached.exists():
            return cached.read_text(encoding="utf-8")
        if self.replay:
            return None
        try:
            r = http.get(f"{GUEST}/jobs/api/jobPosting/{job.job_id}", headers=HEADERS,
                         timeout=30, **_IMP)
        finally:
            # La pausa va SIEMPRE, no solo tras un 200: antes, un bloqueo o
            # un error disparaba la siguiente peticion sin esperar nada,
            # justo cuando mas conviene frenar.
            _pause()
        if r.status_code in BLOCK_CODES:
            raise SourceBlocked(f"HTTP {r.status_code} en descripcion {job.job_id}")
        if r.status_code != 200:
            return None
        text = BeautifulSoup(r.text, "html.parser").get_text(" ", strip=True)[:4000]
        cached.write_text(text, encoding="utf-8")
        return text


def _parse_dt(v) -> datetime | None:
    if not v or str(v) in ("nan", "NaT"):
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None
