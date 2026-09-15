"""Grafo LangGraph. Estado tipado, checkpoint en SQLite.

    load_profile -> plan_queries -> fetch_jobs -> filter_new -> apply_rules
                 -> rank_semantic -> enrich_details -> score_llm
                 -> decide -> notify -> persist

El checkpointer no es decoracion: permite reanudar una corrida cortada por un
429 y, mas adelante, entrar por una "segunda puerta" al nodo de generacion de
CV a medida sobre una oferta concreta sin repetir la busqueda.
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from jobia.models import Job, QueryHealth, RunReport, ScoredJob


class GraphState(TypedDict, total=False):
    run_id: str
    profile_version: int
    profile: dict
    queries: list[dict]
    raw: list[Job]
    new: list[Job]
    candidates: list[Job]     # tras L2 reglas
    shortlist: list[Job]      # tras L3 semantico, con descripcion
    scored: list[ScoredJob]
    threshold: int
    digest: list[dict]
    health: list[QueryHealth]
    blocked: bool
    report: RunReport


# --- TODO(claude-code): implementar cada nodo. Contratos en DISENO.md §4 ---

def load_profile(state: GraphState) -> GraphState: ...
def plan_queries(state: GraphState) -> GraphState: ...
def fetch_jobs(state: GraphState) -> GraphState: ...      # captura SourceBlocked -> blocked=True
def filter_new(state: GraphState) -> GraphState: ...      # Store.filter_new
def apply_rules(state: GraphState) -> GraphState: ...     # L2
def rank_semantic(state: GraphState) -> GraphState: ...   # L3, top_k
def enrich_details(state: GraphState) -> GraphState: ...  # unico sitio que baja descripciones
def score_llm(state: GraphState) -> GraphState: ...       # L4, salida ScoredJob
def decide(state: GraphState) -> GraphState: ...          # umbral adaptativo
def notify(state: GraphState) -> GraphState: ...
def persist(state: GraphState) -> GraphState: ...
def alert_blocked(state: GraphState) -> GraphState: ...   # email de alarma, NO silencio


def route_after_fetch(state: GraphState) -> str:
    """La bifurcacion mas importante del grafo.

    Si estamos bloqueados, o si TODAS las queries volvieron vacias, esto no es
    'hoy no hay ofertas': es que LinkedIn nos ha cortado. La v1 no distinguia
    los dos casos y se paso 26 corridas creyendo que Madrid no publicaba empleo.
    """
    if state.get("blocked"):
        return "alert_blocked"
    if state.get("raw") is not None and len(state["raw"]) == 0:
        return "alert_blocked"
    return "filter_new"


def build_graph(checkpointer=None):
    """TODO(claude-code): StateGraph(GraphState), add_node por cada funcion,
    add_conditional_edges("fetch_jobs", route_after_fetch), compile(checkpointer)."""
    raise NotImplementedError
