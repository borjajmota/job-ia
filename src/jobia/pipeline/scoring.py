"""L4: juez LLM sobre Groq (tier gratuito). Unico nivel que cuesta algo y el
unico que ve descripciones completas -- ya bajadas en enrich_details, para
<=20 ofertas. Todo lo que devuelve el LLM se valida con ScoredJob antes de
tocar la BD: si el parseo o la validacion fallan, esa oferta se omite, nunca
se inventa un score.
"""
from __future__ import annotations

import json
import os
import re
import time

import yaml

from jobia.models import Job, ScoredJob

DEFAULT_MODEL = "llama-3.3-70b-versatile"


def _client():
    from groq import Groq

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY no configurada")
    return Groq(api_key=api_key, max_retries=0)


def _call(client, prompt: str, model: str, max_retries: int = 3) -> str:
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=800,
                timeout=30,
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            err = str(exc)
            is_rate_limited = "429" in err or "rate_limit" in err.lower()
            if is_rate_limited and attempt < max_retries - 1:
                m = re.search(r"retry after (\d+(?:\.\d+)?)", err, re.IGNORECASE)
                time.sleep(float(m.group(1)) + 2 if m else 30)
                continue
            raise
    raise RuntimeError("groq: reintentos agotados")


def _extract_json(text: str) -> dict | None:
    text = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.MULTILINE)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.MULTILINE)
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group())
    except json.JSONDecodeError:
        return None


def _prompt(job: Job, guidance: dict) -> str:
    guidance_yaml = yaml.dump(guidance, allow_unicode=True, default_flow_style=False)
    flags = "\n".join(f"- {f}" for f in job.flags) or "(ninguna)"
    return f"""Eres el juez de encaje de ofertas de empleo para un candidato senior de Data & AI en Madrid.

GUIA DE CALIBRACION:
{guidance_yaml}

SEÑALES DE L2 SOBRE ESTA OFERTA (no descartan, solo contexto):
{flags}

OFERTA:
Titulo: {job.title}
Empresa: {job.company}
Ubicacion: {job.location or "no especificada"}
Descripcion: {(job.description or "")[:3000]}

TAREA: puntua el encaje real (0-100), no la ambicion. Se especialmente
estricto con calibration y penalty_signals. Usa compensable_gaps para no
penalizar dos veces lo que ya esta cubierto.

RESPONDE SOLO CON ESTE JSON, sin texto adicional ni markdown:
{{
  "job_id": "{job.job_id}",
  "score": <0-100>,
  "veredicto": "encaja"|"dudoso"|"descartar",
  "match": ["razon 1", "razon 2 (opcional)", "razon 3 (opcional)"],
  "gaps": ["gap 1 (opcional)", "gap 2 (opcional)"],
  "señal_roja": <null o "texto breve">
}}"""


def score(jobs: list[Job], guidance: dict, model: str | None = None) -> list[ScoredJob]:
    if not jobs:
        return []
    client = _client()
    model = model or os.environ.get("JOBIA_LLM_MODEL", DEFAULT_MODEL)

    out: list[ScoredJob] = []
    for j in jobs:
        raw = _call(client, _prompt(j, guidance), model)
        data = _extract_json(raw)
        if not data:
            continue
        data.setdefault("job_id", j.job_id)
        try:
            out.append(ScoredJob(**data))
        except Exception:
            continue
    return out
