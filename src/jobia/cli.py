"""Punto de entrada de consola. `jobia run` monta el grafo (graph.py) y lo
invoca de punta a punta.

Si el estado final vuelve con blocked=True, el proceso termina con codigo de
salida distinto de 0: es lo que hace que Actions marque la corrida en rojo en
vez de darla por buena en silencio (CLAUDE.md, regla numero uno).
"""
from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import yaml

from jobia.graph import GraphState, build_graph


def _load_profile_version() -> int:
    profile = yaml.safe_load(Path("config/profile.yaml").read_text(encoding="utf-8"))
    return profile["version"]


def cmd_run(_args: argparse.Namespace) -> int:
    graph = build_graph()
    initial_state: GraphState = {
        "run_id": datetime.now(UTC).strftime("%Y%m%d-%H%M%S"),
        "profile_version": _load_profile_version(),
    }
    final_state = graph.invoke(initial_state)

    report = final_state.get("report")
    if report is not None:
        print(
            f"run {report.run_id}: {report.n_raw} vistas, {report.n_new} nuevas, "
            f"{report.n_emailed} enviadas, blocked={report.blocked}"
        )

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
    run_parser.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
