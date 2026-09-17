"""Grafo LangGraph. Estado tipado, checkpoint en SQLite.

    load_profile -> plan_queries -> fetch_jobs -> filter_new -> apply_rules
                 -> rank_semantic -> enrich_details -> score_llm
                 -> decide -> notify -> persist

El checkpointer no es decoracion: permite reanudar una corrida cortada por un
429 y, mas adelante, entrar por una "segunda puerta" al nodo de generacion de
CV a medida sobre una oferta concreta sin repetir la busqueda.
"""
from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import yaml
from langgraph.graph import END, StateGraph

from jobia.models import Job, QueryHealth, RunReport, ScoredJob
from jobia.notify import email as notify_email
from jobia.pipeline import rules as rules_pipeline
from jobia.pipeline import scoring as scoring_pipeline
from jobia.pipeline import semantic as semantic_pipeline
from jobia.sources.base import SourceBlocked
from jobia.sources.linkedin import LinkedInSource
from jobia.store import Store


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
    notes: list[str]
    kill_breakdown: dict[str, int]
    report: RunReport
    # Cableado para corridas manuales desde el dashboard (2026-09-17):
    run_type: str             # "scheduled" | "manual"
    hours_old_override: int   # ventana de publicacion pedida: 24 / 168 / 720
    hours_old_used: int       # la que realmente aplico plan_queries (para persist)
    save_to_dedupe: bool      # False = exploracion, no escribe en job_seen


def _load_yaml(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _cache_dir() -> str:
    return os.environ.get("JOBIA_CACHE_DIR", "data/cache")


def _replay() -> bool:
    return os.environ.get("JOBIA_REPLAY") == "1"


def _db_path() -> str:
    return os.environ.get("JOBIA_DB_PATH", "data/jobia.db")


def load_profile(state: GraphState) -> GraphState:
    profile = _load_yaml("config/profile.yaml")
    run_id = state.get("run_id") or datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return {"profile": profile, "profile_version": profile["version"], "run_id": run_id}


def plan_queries(state: GraphState) -> GraphState:
    cfg = _load_yaml("config/searches.yaml")
    common = {k: cfg[k] for k in ("location", "geo_query", "hours_old", "max_results_per_query")}
    if state.get("hours_old_override"):
        common["hours_old"] = state["hours_old_override"]
    queries = [{"id": q["id"], "term": q["term"], **common} for q in cfg["queries"]]
    # persist() lo guarda tal cual se uso, no un 24 hardcodeado que se
    # desincroniza en cuanto cambie el hours_old por defecto de este yaml.
    return {"queries": queries, "hours_old_used": common["hours_old"]}


def fetch_jobs(state: GraphState) -> GraphState:
    """Captura SourceBlocked -> blocked=True. NUNCA lo traduce en lista vacia
    silenciosa: eso es exactamente lo que hundio la v1 (CLAUDE.md, regla 1)."""
    source = LinkedInSource(cache_dir=_cache_dir(), replay=_replay())
    raw: list[Job] = []
    health: list[QueryHealth] = []
    blocked = False
    for q in state["queries"]:
        try:
            jobs = source.search(
                term=q["term"], query_id=q["id"], location=q["location"],
                geo_query=q["geo_query"], hours_old=q["hours_old"],
                limit=q["max_results_per_query"],
            )
            health.append(QueryHealth(query_id=q["id"], found=len(jobs), new=0))
            raw.extend(jobs)
        except SourceBlocked as exc:
            blocked = True
            health.append(QueryHealth(query_id=q["id"], found=0, new=0, error=str(exc)))

    # Solo para corridas programadas: una exploracion manual con ventana
    # distinta (7d/30d) puede dar found=0 en una query por motivos que no
    # tienen nada que ver con que el selector se haya roto, y ademas
    # ensuciaria el streak de consecutive_empty que mira el cron diario.
    is_scheduled = state.get("run_type", "scheduled") == "scheduled"
    notes = [] if blocked or not is_scheduled else _dead_query_notes(health)
    return {"raw": raw, "health": health, "blocked": blocked, "notes": notes}


def _dead_query_notes(health: list[QueryHealth]) -> list[str]:
    """health.alert_query_dead_after_runs (rules.yaml): una query concreta
    en 0 durante N corridas seguidas es distinta de "todas vacias hoy" --
    no es bloqueo, pero probablemente el termino o el selector se rompio.
    Antes esta config existia pero Store.consecutive_empty() nunca se
    llamaba desde ningun sitio.

    Limitacion conocida: query_health no distingue scheduled/manual (no hay
    columna run_type; anadirla es migracion de esquema, no se ha hecho).
    Solo se llama a esta funcion para corridas scheduled (ver fetch_jobs),
    pero si una corrida manual encuentra 0 en una query, esa fila igual
    cuenta dentro de la ventana de N corridas que mira consecutive_empty
    la proxima vez. Poco probable y no critico (es solo un aviso, no un
    bloqueo), pero es una contaminacion real, no una duda teorica."""
    rules_cfg = _load_yaml("config/rules.yaml")
    n = rules_cfg.get("health", {}).get("alert_query_dead_after_runs")
    if not n:
        return []
    store = Store(_db_path())
    notes = []
    for h in health:
        if h.found != 0:
            continue
        # Las corridas ya persistidas cubren n-1; sumando la de hoy (0) son n.
        prior_dead = n <= 1 or store.consecutive_empty(h.query_id, n - 1)
        if prior_dead:
            notes.append(
                f"query '{h.query_id}' lleva {n} corridas seguidas en 0 resultados "
                "-- revisa el termino o si el selector de LinkedIn cambio"
            )
    return notes


def filter_new(state: GraphState) -> GraphState:
    """Store.filter_new: L1, novedad via job_id + repost_key."""
    store = Store(_db_path())
    new = store.filter_new(state["raw"], persist=state.get("save_to_dedupe", True))
    new_by_query: dict[str, int] = {}
    for j in new:
        new_by_query[j.query_id or ""] = new_by_query.get(j.query_id or "", 0) + 1
    health = [
        QueryHealth(query_id=h.query_id, found=h.found,
                    new=new_by_query.get(h.query_id, 0), error=h.error)
        for h in state.get("health", [])
    ]
    return {"new": new, "health": health}


def apply_rules(state: GraphState) -> GraphState:
    """L2: filtros deterministas de config/rules.yaml."""
    rules_cfg = _load_yaml("config/rules.yaml")
    candidates, kill_breakdown = rules_pipeline.apply(state["new"], rules_cfg)
    return {"candidates": candidates, "kill_breakdown": kill_breakdown}


def rank_semantic(state: GraphState) -> GraphState:
    """L3: embeddings locales vs semantic_anchors del perfil, top_k."""
    rules_cfg = _load_yaml("config/rules.yaml")
    sem_cfg = rules_cfg["semantic"]
    shortlist = semantic_pipeline.rank(
        state["candidates"], state["profile"]["semantic_anchors"],
        model_name=sem_cfg["model"], top_k=sem_cfg["top_k"],
        aggregation=sem_cfg["aggregation"],
    )
    return {"shortlist": shortlist}


def enrich_details(state: GraphState) -> GraphState:
    """Unico sitio del grafo donde se bajan descripciones completas. Es el
    punto que mas fuerte pega a LinkedIn (2026-09-16: sin tope de shortlist,
    ver rules.yaml semantic.top_k), asi que un SourceBlocked aqui para el
    bucle en el acto -- antes se tragaba por oferta y seguia intentando el
    resto en silencio, justo el bug que la regla 1 de CLAUDE.md prohibe."""
    source = LinkedInSource(cache_dir=_cache_dir(), replay=_replay())
    enriched: list[Job] = []
    blocked = False
    for j in state["shortlist"]:
        if blocked:
            enriched.append(j)
            continue
        try:
            desc = source.fetch_description(j)
            enriched.append(j.model_copy(update={"description": desc}))
        except SourceBlocked:
            blocked = True
            enriched.append(j)
    return {"shortlist": enriched, "blocked": blocked}


def score_llm(state: GraphState) -> GraphState:
    """L4: Groq, salida ScoredJob validada con pydantic. Un fallo total
    (Groq caido, key invalida) no debe tirar el grafo entero ni perder el
    estado de dedupe que L1 ya comprometio -- se registra como nota y se
    sigue con scored=[] (decide()/notify() ya saben tratar una lista vacia,
    igual que un dia sin candidatas que pasen el umbral)."""
    try:
        scored = scoring_pipeline.score(state["shortlist"], state["profile"]["scoring_guidance"])
    except Exception as exc:
        note = f"L4 (Groq) fallo por completo, 0 ofertas puntuadas: {exc}"
        print(f"score_llm: {note}", file=sys.stderr)
        return {"scored": [], "notes": state.get("notes", []) + [note]}
    return {"scored": scored}


def decide(state: GraphState) -> GraphState:
    """Umbral adaptativo (percentil de los ultimos N dias, con suelo)."""
    rules_cfg = _load_yaml("config/rules.yaml")
    scoring_cfg = rules_cfg["scoring"]
    store = Store(_db_path())
    threshold = store.threshold(
        scoring_cfg["percentile"], scoring_cfg["floor"], scoring_cfg["lookback_days"]
    )

    jobs_by_id = {j.job_id: j for j in state["shortlist"]}
    passed = [s for s in state["scored"] if s.score >= threshold and s.veredicto != "descartar"]
    passed.sort(key=lambda s: s.score, reverse=True)
    passed = passed[: scoring_cfg["max_per_email"]]

    digest = []
    for s in passed:
        j = jobs_by_id.get(s.job_id)
        if not j:
            continue
        digest.append({
            "title": j.title, "company": j.company, "location": j.location,
            "url": j.url, "score": s.score, "match": s.match, "gaps": s.gaps,
            "señal_roja": s.señal_roja,
        })
    return {"threshold": threshold, "digest": digest}


def notify(state: GraphState) -> GraphState:
    """Un fallo de SMTP no debe tirar el grafo: si pasara, persist() no
    llegaria a correr y el estado de dedupe que L1 ya comprometio (commit
    temprano en Store.filter_new) se perderia en silencio sin haber
    llegado nunca a puntuarse ni notificarse. Se registra como nota y se
    sigue a persist() igual."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    digest = state.get("digest", [])
    notes = list(state.get("notes", []))
    try:
        if digest:
            html = notify_email.render_digest(digest, today, notes=notes)
            n = len(digest)
            subject = f"job-ia · {today} · {n} oferta{'s' if n != 1 else ''}"
            notify_email.send(html, subject=subject)
        elif notes:
            # Dia sin ofertas que pasen el umbral pero con avisos (p.ej.
            # una query muerta): antes esto se quedaba sin email, invisible
            # salvo que alguien mirara la BD a mano.
            html = notify_email.render_notice(notes, today)
            notify_email.send(html, subject=f"job-ia · {today} · sin ofertas, con avisos")
    except Exception as exc:
        notes.append(f"fallo enviando email: {exc}")
    return {"notes": notes}


def persist(state: GraphState) -> GraphState:
    store = Store(_db_path())
    if state.get("scored"):
        store.save_scores(state["run_id"], state.get("profile_version", 0), state["scored"])
    # run_id ya lleva el timestamp de arranque (load_profile); antes este
    # started_at se recalculaba aqui con datetime.now(), asi que "duracion"
    # siempre salia ~0 -- persist() corre al final, no al principio.
    started_at = datetime.strptime(state["run_id"], "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
    report = RunReport(
        run_id=state["run_id"],
        started_at=started_at,
        finished_at=datetime.now(UTC),
        profile_version=state.get("profile_version", 0),
        run_type=state.get("run_type", "scheduled"),
        hours_old=state.get("hours_old_used", 24),
        saved_to_dedupe=state.get("save_to_dedupe", True),
        queries=state.get("health", []),
        n_raw=len(state.get("raw", [])),
        n_new=len(state.get("new", [])),
        n_after_rules=len(state.get("candidates", [])),
        n_scored=len(state.get("scored", [])),
        n_emailed=len(state.get("digest", [])),
        threshold_used=state.get("threshold"),
        blocked=state.get("blocked", False),
        notes=state.get("notes", []),
        kill_breakdown=state.get("kill_breakdown", {}),
    )
    store.save_run(report)
    return {"report": report}


def alert_blocked(state: GraphState) -> GraphState:
    """El email de alarma: la linea de codigo mas valiosa del proyecto
    (DISENO.md 4.1). Es la correccion directa de los 25 dias perdidos en v1."""
    today = datetime.now(UTC).strftime("%Y-%m-%d")
    reason = ("bloqueo explicito (SourceBlocked) en una o mas queries" if state.get("blocked")
              else "todas las queries devolvieron 0 resultados")
    html = notify_email.render_alert(reason, today)
    notify_email.send(html, subject=f"⚠ job-ia · posible bloqueo · {today}")
    return {"blocked": True}


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


def route_after_enrich(state: GraphState) -> str:
    """Si enrich_details se bloqueo a mitad de las descripciones, no sigas
    a score_llm con una mezcla de ofertas con y sin descripcion real."""
    if state.get("blocked"):
        return "alert_blocked"
    return "score_llm"


def build_graph(checkpointer=None):
    g = StateGraph(GraphState)

    for name, fn in [
        ("load_profile", load_profile),
        ("plan_queries", plan_queries),
        ("fetch_jobs", fetch_jobs),
        ("filter_new", filter_new),
        ("apply_rules", apply_rules),
        ("rank_semantic", rank_semantic),
        ("enrich_details", enrich_details),
        ("score_llm", score_llm),
        ("decide", decide),
        ("notify", notify),
        ("persist", persist),
        ("alert_blocked", alert_blocked),
    ]:
        g.add_node(name, fn)

    g.set_entry_point("load_profile")
    g.add_edge("load_profile", "plan_queries")
    g.add_edge("plan_queries", "fetch_jobs")
    g.add_conditional_edges(
        "fetch_jobs", route_after_fetch,
        {"filter_new": "filter_new", "alert_blocked": "alert_blocked"},
    )
    g.add_edge("filter_new", "apply_rules")
    g.add_edge("apply_rules", "rank_semantic")
    g.add_edge("rank_semantic", "enrich_details")
    g.add_conditional_edges(
        "enrich_details", route_after_enrich,
        {"score_llm": "score_llm", "alert_blocked": "alert_blocked"},
    )
    g.add_edge("score_llm", "decide")
    g.add_edge("decide", "notify")
    g.add_edge("notify", "persist")
    g.add_edge("persist", END)
    g.add_edge("alert_blocked", "persist")

    return g.compile(checkpointer=checkpointer)
