"""L3: ranking semantico local, sin red. Compara cada oferta (post-L2) contra
las semantic_anchors del perfil con embeddings de intfloat/multilingual-e5-base.

Una oferta puntua por su MEJOR ancla, no por el promedio: asi un match fuerte
en una sola linea del perfil no se diluye con las otras cuatro (aggregation:
max en config/rules.yaml).

Sin descripcion todavia: eso solo se baja en enrich_details, para <=20
ofertas. Aqui se compara contra titulo + empresa.
"""
from __future__ import annotations

from functools import lru_cache

from jobia.models import Job

_PREFIX_QUERY = "query: "
_PREFIX_PASSAGE = "passage: "


@lru_cache(maxsize=1)
def _model(name: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name)


def _job_text(j: Job) -> str:
    parts = [j.title or "", j.company or ""]
    return _PREFIX_PASSAGE + ". ".join(p for p in parts if p)


def rank(
    jobs: list[Job],
    anchors: list[dict],
    model_name: str,
    top_k: int | None,
    aggregation: str = "max",
) -> list[Job]:
    """Ordena de mas a menos afin al perfil. Si top_k es None, no recorta:
    devuelve todos (2026-09-16, decision explicita -- el filtrado real ya
    lo hace L2/R0-R6, este ranking es para orden, no para exclusion)."""
    if not jobs:
        return []
    if aggregation != "max":
        raise NotImplementedError(f"aggregation={aggregation!r} no soportado, solo 'max'")

    model = _model(model_name)
    anchor_texts = [_PREFIX_QUERY + a["text"] for a in anchors]
    anchor_weights = [float(a.get("weight", 1.0)) for a in anchors]

    anchor_emb = model.encode(anchor_texts, normalize_embeddings=True)
    job_emb = model.encode([_job_text(j) for j in jobs], normalize_embeddings=True)

    scored: list[tuple[float, Job]] = []
    for j, emb in zip(jobs, job_emb):
        best = max(
            float(emb @ a_emb) * w for a_emb, w in zip(anchor_emb, anchor_weights)
        )
        scored.append((best, j))

    scored.sort(key=lambda t: t[0], reverse=True)
    ranked = [j for _, j in scored]
    return ranked if top_k is None else ranked[:top_k]
