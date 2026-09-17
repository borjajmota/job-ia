"""Dashboard local de job-ia. Lanzar con:

    streamlit run src/jobia/dashboard/app.py

JOBIA_DB_PATH debe apuntar a la misma base que usa la corrida programada
(ver scripts/dashboard.ps1) para que el historial que se ve aqui sea el
real, no uno vacio en data/jobia.db del repo.
"""
from __future__ import annotations

import os
import time
from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from jobia.dashboard import config_io, runner
from jobia.models import RunReport
from jobia.store import Store

st.set_page_config(page_title="job-ia", page_icon="📊", layout="wide")


def _db_path() -> str:
    return os.environ.get("JOBIA_DB_PATH", "data/jobia.db")


def _store() -> Store:
    return Store(_db_path())


def _runs_df(runs: list[RunReport]) -> pd.DataFrame:
    rows = []
    for r in runs:
        dur = (r.finished_at - r.started_at).total_seconds() if r.finished_at else None
        rows.append({
            "run_id": r.run_id,
            "fecha": r.started_at.strftime("%Y-%m-%d %H:%M"),
            "tipo": r.run_type,
            "ventana": {24: "diaria (24h)", 168: "semanal (7d)", 720: "mensual (30d)"}
                .get(r.hours_old, f"{r.hours_old}h"),
            "guardado": "si" if r.saved_to_dedupe else "no (exploracion)",
            "bloqueado": r.blocked,
            "raw": r.n_raw, "nuevas": r.n_new, "tras_L2": r.n_after_rules,
            "puntuadas": r.n_scored, "emailadas": r.n_emailed,
            "umbral": r.threshold_used,
            "duracion_s": round(dur) if dur is not None else None,
            "avisos": len(r.notes),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- Historial
def _salud_banner(runs: list[RunReport]) -> None:
    """Lo primero que se deberia ver al abrir el dashboard: si la ultima
    corrida fue mal, o si hace demasiado que no corre ninguna, eso importa
    mas que cualquier tabla."""
    last = runs[0]
    if last.blocked:
        st.error(f"⚠ La última corrida ({last.run_id}) se marcó como **bloqueada**. "
                  "Revisa el detalle antes de fiarte del resto de números.")
    horas = (datetime.now(UTC) - last.started_at).total_seconds() / 3600
    if horas > 36:
        st.warning(f"La última corrida fue hace {horas:.0f}h ({last.run_id}). "
                    "Si el cron es de lunes a viernes esto puede ser normal "
                    "(fin de semana), pero conviene comprobarlo.")


def page_historial(store: Store) -> None:
    st.header("Historial de corridas")
    runs = store.list_runs()
    if not runs:
        st.info("Todavia no hay ninguna corrida registrada en esta base de datos.")
        return

    _salud_banner(runs)
    df = _runs_df(runs)
    st.dataframe(df, width="stretch", hide_index=True)

    st.divider()
    st.subheader("Detalle de una corrida")
    sel = st.selectbox("Elige un run_id", df["run_id"].tolist())
    run = next(r for r in runs if r.run_id == sel)

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Encontradas (raw)", run.n_raw)
    c2.metric("Nuevas (L1)", run.n_new)
    c3.metric("Tras reglas (L2)", run.n_after_rules)
    c4.metric("Puntuadas (L4)", run.n_scored)
    c5.metric("Enviadas", run.n_emailed)

    col_a, col_b = st.columns(2)
    with col_a:
        st.markdown("**Bajas por regla en L2**")
        if run.kill_breakdown:
            st.bar_chart(pd.Series(run.kill_breakdown, name="bajas"))
        else:
            st.caption("Sin desglose para esta corrida (corridas anteriores al 2026-09-17 no lo tienen).")
    with col_b:
        st.markdown("**Salud por query**")
        if run.queries:
            qdf = pd.DataFrame([q.model_dump() for q in run.queries])
            st.dataframe(qdf, width="stretch", hide_index=True)
        else:
            st.caption("Sin datos de queries.")

    if run.notes:
        st.markdown("**Avisos**")
        for note in run.notes:
            st.warning(note)


# --------------------------------------------------------------------- KPIs
def page_kpis(store: Store) -> None:
    st.header("KPIs")
    runs = store.list_runs()
    if not runs:
        st.info("Todavia no hay corridas para calcular KPIs.")
        return

    df = _runs_df(runs).iloc[::-1]  # orden cronologico para las series
    ok_runs = [r for r in runs if not r.blocked]

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Corridas totales", len(runs))
    c2.metric("Bloqueadas", sum(r.blocked for r in runs))
    c3.metric("Ofertas emailadas (total)", sum(r.n_emailed for r in runs))
    avg_new = sum(r.n_new for r in ok_runs) / len(ok_runs) if ok_runs else 0
    c4.metric("Nuevas / corrida (media)", f"{avg_new:.1f}")

    st.subheader("Nuevas vs. emailadas por corrida")
    st.line_chart(df.set_index("fecha")[["nuevas", "emailadas"]])

    st.subheader("Embudo (ultima corrida sin bloqueo)")
    last_ok = next((r for r in runs if not r.blocked), None)
    if last_ok:
        col_chart, col_pct = st.columns([2, 1])
        funnel = pd.Series({
            "raw": last_ok.n_raw, "nuevas": last_ok.n_new,
            "tras L2": last_ok.n_after_rules, "puntuadas": last_ok.n_scored,
            "emailadas": last_ok.n_emailed,
        })
        with col_chart:
            st.bar_chart(funnel)
        with col_pct:
            # El conteo absoluto no dice donde esta el cuello de botella
            # real -- el % de caida entre pasos si.
            def _pct(num, den):
                return f"{100 * num / den:.0f}%" if den else "–"
            st.markdown("**Conversion por paso**")
            st.markdown(f"- raw → nuevas: {_pct(last_ok.n_new, last_ok.n_raw)}")
            st.markdown(f"- nuevas → tras L2: {_pct(last_ok.n_after_rules, last_ok.n_new)}")
            st.markdown(f"- tras L2 → puntuadas: {_pct(last_ok.n_scored, last_ok.n_after_rules)}")
            st.markdown(f"- puntuadas → emailadas: {_pct(last_ok.n_emailed, last_ok.n_scored)}")
    else:
        st.caption("No hay ninguna corrida sin bloqueo todavia.")

    st.subheader("Empresas mas vistas")
    top = store.top_companies(20)
    if top:
        st.bar_chart(pd.DataFrame(top, columns=["empresa", "veces"]).set_index("empresa"))
    else:
        st.caption("Sin datos de empresas todavia.")


# --------------------------------------------------------- Lanzar ejecucion
def page_lanzar(store: Store) -> None:
    st.header("Lanzar ejecución")

    active = runner.is_running()
    if active:
        st.info(f"Corriendo ahora mismo (PID {active['pid']}, "
                 f"ventana {active['hours_old']}h, "
                 f"{'guarda' if active['save_to_dedupe'] else 'NO guarda'} en la BD).")
        st.code(runner.tail_log(), language=None)
        time.sleep(2)
        st.rerun()
        return

    if store.already_ran_today():
        st.warning("Ya hubo una corrida sin bloqueo hoy. Lanzar otra es una "
                   "pasada extra por LinkedIn, no un reemplazo silencioso.")

    st.markdown("**Ventana de publicación** (antigüedad de las ofertas a buscar)")
    ventana = st.radio(
        "ventana", ["Diaria (24h)", "Semanal (7 dias)", "Mensual (30 dias)"],
        label_visibility="collapsed", horizontal=True,
    )
    hours_old = {"Diaria (24h)": 24, "Semanal (7 dias)": 168, "Mensual (30 dias)": 720}[ventana]

    guardar = st.checkbox(
        "Guardar en la base de datos (job_seen)", value=True,
        help="Si lo desmarcas, esta corrida no cuenta para el proceso diario: "
             "mañana volvera a ver estas mismas ofertas como nuevas. Util para "
             "explorar sin gastar el 'ya vista' de una oferta real.",
    )
    if not guardar:
        st.caption("⚠ Modo exploración: no se tocará job_seen. El email y las "
                   "puntuaciones sí quedan en el historial para que lo revises.")

    confirmar = st.checkbox("Confirmo que quiero lanzar esta corrida ahora "
                             "(pega de verdad a LinkedIn)")
    if st.button("Lanzar ejecución", type="primary", disabled=not confirmar):
        runner.launch(hours_old=hours_old, save_to_dedupe=guardar)
        st.rerun()


# ---------------------------------------------------------------- Configuracion
def page_config() -> None:
    st.header("Configuración")
    st.caption("Los cambios se guardan directamente en config/*.yaml, preservando "
               "los comentarios. Las listas de reglas R0-R6 (kill/bonus/penalty) "
               "todavia se editan solo por archivo -- fast-follow.")

    tab_busq, tab_reglas = st.tabs(["Búsquedas (searches.yaml)", "Reglas y umbral (rules.yaml)"])

    with tab_busq:
        searches = config_io.load_searches()
        c1, c2 = st.columns(2)
        with c1:
            location = st.text_input("location", str(searches.get("location", "")))
            geo_query = st.text_input("geo_query", str(searches.get("geo_query", "")))
        with c2:
            hours_old = st.number_input("hours_old por defecto", min_value=1,
                                         value=int(searches.get("hours_old", 24)))
            max_results = st.number_input("max_results_per_query", min_value=1,
                                           value=int(searches.get("max_results_per_query", 1000)))

        st.markdown("**Queries**")
        queries_df = pd.DataFrame(
            [{"id": q["id"], "term": q["term"]} for q in searches.get("queries", [])]
        )
        edited = st.data_editor(queries_df, num_rows="dynamic", width="stretch",
                                 key="queries_editor")

        if st.button("Guardar searches.yaml"):
            searches["location"] = location
            searches["geo_query"] = geo_query
            searches["hours_old"] = int(hours_old)
            searches["max_results_per_query"] = int(max_results)
            searches["queries"] = [
                {"id": row["id"], "term": row["term"]}
                for row in edited.to_dict("records") if row.get("id") and row.get("term")
            ]
            config_io.save_searches(searches)
            st.success("Guardado.")

    with tab_reglas:
        rules = config_io.load_rules()
        st.markdown("**L3 — semántico**")
        sin_tope = st.checkbox("Sin tope (top_k=null)",
                                value=rules["semantic"].get("top_k") is None)
        top_k = None
        if not sin_tope:
            top_k = st.number_input("top_k", min_value=1,
                                     value=int(rules["semantic"].get("top_k") or 18))

        st.markdown("**L4/decide — umbral adaptativo**")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            percentile = st.number_input("percentile", 1, 100,
                                          int(rules["scoring"]["percentile"]))
        with c2:
            floor = st.number_input("floor", 0, 100, int(rules["scoring"]["floor"]))
        with c3:
            max_per_email = st.number_input("max_per_email", 1, 50,
                                             int(rules["scoring"]["max_per_email"]))
        with c4:
            lookback_days = st.number_input("lookback_days", 1, 365,
                                             int(rules["scoring"]["lookback_days"]))

        st.markdown("**L2/R6 — bandas de título**")
        c5, c6 = st.columns(2)
        bands = rules["title_rules"]["scoring"]["bands"]
        with c5:
            pass_min = st.number_input("pass_min", 0, 200, int(bands["pass_min"]))
        with c6:
            review_min = st.number_input("review_min", 0, 200, int(bands["review_min"]))
        if review_min >= pass_min:
            st.error("review_min debe ser menor que pass_min.")

        if st.button("Guardar rules.yaml", disabled=review_min >= pass_min):
            rules["semantic"]["top_k"] = top_k
            rules["scoring"]["percentile"] = int(percentile)
            rules["scoring"]["floor"] = int(floor)
            rules["scoring"]["max_per_email"] = int(max_per_email)
            rules["scoring"]["lookback_days"] = int(lookback_days)
            rules["title_rules"]["scoring"]["bands"]["pass_min"] = int(pass_min)
            rules["title_rules"]["scoring"]["bands"]["review_min"] = int(review_min)
            config_io.save_rules(rules)
            st.success("Guardado.")


def main() -> None:
    st.sidebar.title("job·ia")
    st.sidebar.caption(f"BD: {_db_path()}")
    page = st.sidebar.radio(
        "Seccion", ["Historial", "KPIs", "Lanzar ejecución", "Configuración"],
        label_visibility="collapsed",
    )
    store = _store()
    if page == "Historial":
        page_historial(store)
    elif page == "KPIs":
        page_kpis(store)
    elif page == "Lanzar ejecución":
        page_lanzar(store)
    else:
        page_config()
    st.sidebar.divider()
    st.sidebar.caption(f"Hora actual: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")


main()
