"""Puerto JobSource. Cambiar de JobSpy a Apify o a una API de pago debe ser
sustituir una clase, no tocar el grafo."""
from __future__ import annotations

from abc import ABC, abstractmethod

from jobia.models import Job


class SourceBlocked(Exception):
    """429 / 403 / 999. NUNCA se traduce a 'lista vacia'.

    Esto es lo que hundio la v1: 26 corridas devolviendo 0 ofertas que el log
    registraba como 'no hay nada hoy'.
    """


class JobSource(ABC):
    name: str

    @abstractmethod
    def search(self, term: str, query_id: str, *, location: str, geo_query: str,
               hours_old: int, limit: int) -> list[Job]:
        """Devuelve ofertas. Lanza SourceBlocked si la fuente nos corta."""

    @abstractmethod
    def fetch_description(self, job: Job) -> str | None:
        """Solo se llama en la frontera L3->L4, para <=20 ofertas al dia."""
