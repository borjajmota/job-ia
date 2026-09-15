"""Estado en SQLite. Es lo que convierte el proceso en iterativo:
sin esto no hay "solo lo nuevo", ni umbral adaptativo, ni deteccion de bloqueo.
"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from jobia.models import Job, RunReport, ScoredJob

SCHEMA = """
CREATE TABLE IF NOT EXISTS job_seen (
  job_id      TEXT PRIMARY KEY,
  repost_key  TEXT NOT NULL,
  title       TEXT, company TEXT, location TEXT, url TEXT,
  query_id    TEXT,
  posted_at   TEXT,
  first_seen  TEXT NOT NULL,
  last_seen   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_repost ON job_seen(repost_key);

CREATE TABLE IF NOT EXISTS scores (
  job_id   TEXT, run_id TEXT, profile_version INTEGER,
  score    INTEGER, veredicto TEXT, payload TEXT, scored_at TEXT,
  PRIMARY KEY (job_id, profile_version)
);

CREATE TABLE IF NOT EXISTS runs (
  run_id TEXT PRIMARY KEY, started_at TEXT, blocked INTEGER,
  report TEXT
);

CREATE TABLE IF NOT EXISTS query_health (
  run_id TEXT, query_id TEXT, found INTEGER, new INTEGER, error TEXT,
  PRIMARY KEY (run_id, query_id)
);

-- Fase 2: respondes al email con los IDs que te interesan y esto se llena.
-- El esquema existe desde el dia 1 para no migrar despues.
CREATE TABLE IF NOT EXISTS feedback (
  job_id TEXT PRIMARY KEY, veredicto_humano TEXT, nota TEXT, created_at TEXT
);
"""


class Store:
    def __init__(self, path: str | Path = "data/jobia.db"):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    # ---------- L1: novedad ----------
    def filter_new(self, jobs: list[Job]) -> list[Job]:
        """Nuevo = job_id desconocido Y repost_key desconocida.
        Lo segundo evita que la misma oferta republicada te llegue cada semana."""
        now = datetime.now(UTC).isoformat()
        new: list[Job] = []
        for j in jobs:
            row = self.db.execute(
                "SELECT 1 FROM job_seen WHERE job_id=? OR repost_key=?",
                (j.job_id, j.repost_key)).fetchone()
            if row:
                self.db.execute("UPDATE job_seen SET last_seen=? WHERE job_id=?",
                                (now, j.job_id))
                continue
            self.db.execute(
                "INSERT OR IGNORE INTO job_seen VALUES (?,?,?,?,?,?,?,?,?,?)",
                (j.job_id, j.repost_key, j.title, j.company, j.location, j.url,
                 j.query_id, j.posted_at.isoformat() if j.posted_at else None, now, now))
            new.append(j)
        self.db.commit()
        return new

    # ---------- umbral adaptativo ----------
    def threshold(self, percentile: int, floor: int, lookback_days: int) -> int:
        since = (datetime.now(UTC) - timedelta(days=lookback_days)).isoformat()
        vals = [r[0] for r in self.db.execute(
            "SELECT score FROM scores WHERE scored_at>=? ORDER BY score", (since,))]
        if len(vals) < 20:          # sin historia suficiente, usa el suelo
            return floor
        idx = int(len(vals) * percentile / 100)
        return max(floor, vals[min(idx, len(vals) - 1)])

    def save_scores(self, run_id: str, pv: int, scored: list[ScoredJob]) -> None:
        now = datetime.now(UTC).isoformat()
        self.db.executemany(
            "INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?,?,?)",
            [(s.job_id, run_id, pv, s.score, s.veredicto, s.model_dump_json(), now)
             for s in scored])
        self.db.commit()

    # ---------- salud ----------
    def consecutive_empty(self, query_id: str, n: int) -> bool:
        rows = self.db.execute(
            "SELECT found FROM query_health WHERE query_id=? ORDER BY run_id DESC LIMIT ?",
            (query_id, n)).fetchall()
        return len(rows) == n and all(r[0] == 0 for r in rows)

    def save_run(self, rep: RunReport) -> None:
        self.db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?)",
                        (rep.run_id, rep.started_at.isoformat(), int(rep.blocked),
                         rep.model_dump_json()))
        self.db.executemany(
            "INSERT OR REPLACE INTO query_health VALUES (?,?,?,?,?)",
            [(rep.run_id, q.query_id, q.found, q.new, q.error) for q in rep.queries])
        self.db.commit()

    def already_ran_today(self) -> bool:
        today = datetime.now(UTC).strftime("%Y%m%d")
        return self.db.execute(
            "SELECT 1 FROM runs WHERE run_id LIKE ? AND blocked=0", (f"{today}%",)
        ).fetchone() is not None
