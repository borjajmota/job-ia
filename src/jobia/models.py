"""Contratos de datos. Todo lo que cruza entre nodos del grafo pasa por aqui."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


class Job(BaseModel):
    """Una oferta tal y como sale de la fuente. Sin descripcion: eso es L4."""
    job_id: str
    title: str
    company: str
    location: str | None = None
    url: str
    posted_at: datetime | None = None
    source: str = "linkedin"
    query_id: str | None = None
    description: str | None = None      # se rellena SOLO en L4
    flags: list[str] = Field(default_factory=list)  # señales amarillas de L2, para L4

    @property
    def repost_key(self) -> str:
        """Los recruiters republican la misma oferta con jobId nuevo.
        Esta clave la caza; job_id sola no."""
        raw = f"{_norm(self.company)}|{_norm(self.title)}|{_norm(self.location)}"
        return hashlib.sha1(raw.encode()).hexdigest()[:16]


class ScoredJob(BaseModel):
    """Salida estructurada del LLM en L4. El LLM devuelve exactamente esto."""
    job_id: str
    score: int = Field(ge=0, le=100)
    veredicto: Literal["encaja", "dudoso", "descartar"]
    match: list[str] = Field(max_length=3, description="Por que encaja")
    gaps: list[str] = Field(max_length=2, description="Que te falta")
    señal_roja: str | None = None


class QueryHealth(BaseModel):
    query_id: str
    found: int
    new: int
    error: str | None = None


class RunReport(BaseModel):
    """Lo que se persiste de cada corrida. Es la memoria operativa del sistema."""
    run_id: str
    started_at: datetime
    profile_version: int
    queries: list[QueryHealth] = []
    n_raw: int = 0
    n_new: int = 0
    n_after_rules: int = 0
    n_scored: int = 0
    n_emailed: int = 0
    threshold_used: int | None = None
    blocked: bool = False               # bandera de bloqueo de LinkedIn
    notes: list[str] = []
