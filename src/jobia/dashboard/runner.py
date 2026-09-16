"""Lanza `jobia run` como subproceso desde el dashboard y seguimiento por
lock file en disco -- no session_state de Streamlit, que se pierde si se
recarga la pagina o se reinicia el servidor. Reutiliza cli.cmd_run tal
cual (mismo checkpointer, mismo guard de already_ran_today); el dashboard
no reimplementa nada del grafo, solo lo invoca.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

LOCK_PATH = Path("data/dashboard_run.lock")
LOG_PATH = Path("data/dashboard_run.log")


def is_running() -> dict | None:
    """None si no hay nada en marcha; si lo hay, el dict del lock file."""
    if not LOCK_PATH.exists():
        return None
    try:
        info = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        LOCK_PATH.unlink(missing_ok=True)
        return None
    if _pid_alive(info.get("pid")):
        return info
    LOCK_PATH.unlink(missing_ok=True)
    return None


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        out = subprocess.run(
            ["tasklist", "/fi", f"PID eq {pid}"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return str(pid) in out.stdout
    except OSError:
        return False


def launch(hours_old: int, save_to_dedupe: bool) -> None:
    """Lanza `jobia run` en segundo plano. force=True siempre: el guard de
    already_ran_today esta pensado para evitar dobles corridas ACCIDENTALES
    del cron; un clic deliberado en el dashboard ya es consentimiento
    informado (la propia UI se lo pregunta antes)."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, "-m", "jobia.cli", "run",
        "--run-type", "manual", "--hours-old", str(hours_old), "--force",
    ]
    if not save_to_dedupe:
        cmd.append("--no-dedupe")

    with LOG_PATH.open("w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(cmd, stdout=log_file, stderr=subprocess.STDOUT)

    LOCK_PATH.write_text(json.dumps({
        "pid": proc.pid, "hours_old": hours_old, "save_to_dedupe": save_to_dedupe,
    }), encoding="utf-8")


def tail_log(n_chars: int = 5000) -> str:
    if not LOG_PATH.exists():
        return "(sin log todavia)"
    text = LOG_PATH.read_text(encoding="utf-8", errors="replace")
    return text[-n_chars:]
