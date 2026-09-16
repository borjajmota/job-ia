"""Punto de entrada de consola. `jobia run` monta el grafo (graph.py) y lo
invoca de punta a punta.

Si el estado final vuelve con blocked=True, el proceso termina con codigo de
salida distinto de 0: es lo que hace que Actions marque la corrida en rojo en
vez de darla por buena en silencio (CLAUDE.md, regla numero uno).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv
from langgraph.checkpoint.sqlite import SqliteSaver

from jobia.graph import GraphState, build_graph
from jobia.store import Store


def _load_profile_version() -> int:
    profile = yaml.safe_load(Path("config/profile.yaml").read_text(encoding="utf-8"))
    return profile["version"]


def _db_path() -> str:
    return os.environ.get("JOBIA_DB_PATH", "data/jobia.db")


def _checkpoint_path() -> str:
    return os.environ.get("JOBIA_CHECKPOINT_PATH", "data/checkpoints.db")


def cmd_run(args: argparse.Namespace) -> int:
    if not args.force and Store(_db_path()).already_ran_today():
        print("Ya hubo una corrida sin bloqueo hoy. Usa --force para forzar otra "
              "(cada corrida extra es otra pasada por LinkedIn).")
        return 0

    Path(_checkpoint_path()).parent.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")

    initial_state: GraphState = {
        "run_id": run_id,
        "profile_version": _load_profile_version(),
        "run_type": args.run_type,
        "save_to_dedupe": not args.no_dedupe,
    }
    if args.hours_old:
        initial_state["hours_old_override"] = args.hours_old

    # El checkpointer permite reanudar una corrida cortada a mitad (p.ej. un
    # 429 que ni siquiera SourceBlocked llega a capturar) sin tener que
    # repetir las queries ya hechas desde cero.
    with SqliteSaver.from_conn_string(_checkpoint_path()) as checkpointer:
        graph = build_graph(checkpointer=checkpointer)
        final_state = graph.invoke(
            initial_state, config={"configurable": {"thread_id": run_id}}
        )

    report = final_state.get("report")
    if report is not None:
        print(
            f"run {report.run_id}: {report.n_raw} vistas, {report.n_new} nuevas, "
            f"{report.n_emailed} enviadas, blocked={report.blocked}"
        )
        for note in report.notes:
            print(f"aviso: {note}", file=sys.stderr)

    if final_state.get("blocked"):
        print("BLOQUEO detectado (SourceBlocked o 0 resultados en todas las queries).",
              file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jobia", description=(
        "Busca ofertas en LinkedIn, las filtra en embudo y manda un email diario."
    ))
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Ejecuta el grafo completo de punta a punta.")
    run_parser.add_argument(
        "--force", action="store_true",
        help="Ignora el guard de 'ya corrio hoy sin bloqueo' y ejecuta igualmente.",
    )
    run_parser.add_argument(
        "--hours-old", type=int, default=None,
        help="Ventana de antiguedad de publicacion en horas (24=diaria, 168=semanal, "
             "720=mensual). Por defecto, el hours_old de config/searches.yaml.",
    )
    run_parser.add_argument(
        "--no-dedupe", action="store_true",
        help="No escribe en job_seen: exploracion que el proceso diario no vera "
             "como 'ya vista'. Las tablas runs/scores si se guardan igual.",
    )
    run_parser.add_argument(
        "--run-type", choices=["scheduled", "manual"], default="scheduled",
        help="Etiqueta la corrida en el historial (dashboard la pasa como 'manual').",
    )
    run_parser.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    # No-op si no hay .env (caso normal en Actions, donde los secrets ya
    # llegan como variables de entorno reales). python-dotenv estaba
    # declarado como dependencia desde el principio pero nunca se usaba.
    load_dotenv()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
