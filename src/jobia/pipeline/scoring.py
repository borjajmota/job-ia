"""L4: juez LLM, Gemini como via principal y Groq (tier gratuito) como red
de seguridad secundaria si Gemini falla del todo. Unico nivel que cuesta
algo y el unico que ve descripciones completas -- ya bajadas en
enrich_details, para lo que haya sobrevivido L2 (sin tope fijo desde
2026-09-16). Todo lo que devuelve el LLM se valida con ScoredJob antes de
tocar la BD: si el parseo o la validacion fallan, esa oferta se omite,
nunca se inventa un score.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import TYPE_CHECKING

from jobia.models import Job, ScoredJob

if TYPE_CHECKING:
    from jobia.store import Store

DEFAULT_MODEL = "gemini-3.6-flash"
# Groq de respaldo: modelo fijo, no configurable via JOBIA_LLM_MODEL --
# esa variable pilota el modelo de Gemini (el principal), y mezclar ambos
# en el mismo env var mandaria nombres de modelo de un proveedor al otro.
FALLBACK_MODEL = "openai/gpt-oss-120b"


def _gemini_client():
    from google import genai

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY no configurada")
    return genai.Client(api_key=api_key)


def _groq_client():
    from groq import Groq

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY no configurada")
    return Groq(api_key=api_key, max_retries=0)


def _call_gemini(prompt: str, model: str, max_retries: int = 3) -> str:
    from google.genai import types

    client = _gemini_client()
    for attempt in range(max_retries):
        try:
            resp = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=1500,
                ),
            )
            text = (resp.text or "").strip()
            if not text:
                raise RuntimeError("gemini: respuesta vacia")
            return text
        except Exception as exc:
            err = str(exc)
            is_rate_limited = "429" in err or "RESOURCE_EXHAUSTED" in err.upper()
            if is_rate_limited and attempt < max_retries - 1:
                time.sleep(30)
                continue
            raise
    raise RuntimeError("gemini: reintentos agotados")


def _call_groq(prompt: str, model: str, max_retries: int = 3) -> str:
    client = _groq_client()
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=1500,
                # openai/gpt-oss-120b es un modelo de razonamiento: por defecto
                # gasta un numero variable de tokens "pensando" (campo
                # `reasoning`, aparte de `content`) antes de escribir el JSON.
                # Con prompts largos eso agotaba max_tokens y `content` salia
                # vacio -- "low" mantiene el razonamiento corto y predecible.
                reasoning_effort="low",
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


def _call(prompt: str, model: str) -> str:
    """Gemini primero; si falla del todo (tras sus propios reintentos),
    Groq como segunda red de seguridad -- para no perder la corrida del dia
    si Gemini esta caido o mal configurado."""
    try:
        return _call_gemini(prompt, model)
    except Exception as exc:
        print(f"score_llm: Gemini fallo ({exc}), probando Groq de respaldo", file=sys.stderr)
        return _call_groq(prompt, FALLBACK_MODEL)


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
    perfil = guidance["perfil_candidato"]
    notas = guidance.get("notas_calibracion_adicionales", "")
    flags = "\n".join(f"- {f}" for f in job.flags) or "(ninguna)"
    return f"""Eres el juez de encaje de ofertas de empleo para el candidato descrito abajo.

PERFIL DEL CANDIDATO:
{perfil}

NOTAS DE CALIBRACION ADICIONALES (complementan el perfil, no lo contradicen):
{notas}

SEÑALES DE L2 SOBRE ESTA OFERTA (no descartan, solo contexto):
{flags}

OFERTA:
Titulo: {job.title}
Empresa: {job.company}
Ubicacion: {job.location or "no especificada"}
Descripcion: {(job.description or "")[:3000]}

TAREA: puntua el encaje real (0-100) siguiendo el perfil de arriba, no la
ambicion del candidato. Se estricto con la seccion "QUE NO BUSCA" y con
las notas de calibracion. Usa las compensaciones ya listadas para no
penalizar dos veces lo que ya esta cubierto. Si la oferta no da un dato
que necesitas para decidir, trata esa carencia como "no especificado" en
vez de inventarla.

RESPONDE SOLO CON ESTE JSON, sin texto adicional, sin markdown, sin
explicar el razonamiento:
{{
  "job_id": "{job.job_id}",
  "score": <0-100>,
  "veredicto": "encaja"|"dudoso"|"descartar",
  "match": ["razon 1", "razon 2 (opcional)", "razon 3 (opcional)"],
  "gaps": ["gap 1 (opcional)", "gap 2 (opcional)"],
  "señal_roja": <null o "texto breve">
}}"""


def score(
    jobs: list[Job],
    guidance: dict,
    model: str | None = None,
    store: Store | None = None,
    profile_version: int | None = None,
) -> list[ScoredJob]:
    if not jobs:
        return []
    model = model or os.environ.get("JOBIA_LLM_MODEL", DEFAULT_MODEL)

    out: list[ScoredJob] = []
    n_cached = 0
    for j in jobs:
        if store is not None and profile_version is not None:
            cached = store.get_cached_score(j.job_id, profile_version)
            if cached is not None:
                out.append(cached)
                n_cached += 1
                continue

        raw = _call(_prompt(j, guidance), model)
        data = _extract_json(raw)
        if not data:
            print(f"score_llm: {j.job_id} omitida, respuesta sin JSON valido", file=sys.stderr)
            continue
        data.setdefault("job_id", j.job_id)
        # El modelo a veces devuelve mas items de los que pide el prompt
        # (bien fondo, mal formato): recortar es mejor que tirar todo el
        # score valido por un list-length de pydantic.
        if isinstance(data.get("match"), list):
            data["match"] = data["match"][:3]
        if isinstance(data.get("gaps"), list):
            data["gaps"] = data["gaps"][:2]
        try:
            out.append(ScoredJob(**data))
        except Exception as exc:
            print(f"score_llm: {j.job_id} omitida, no valida contra ScoredJob: {exc}", file=sys.stderr)
            continue
    if n_cached:
        print(f"score_llm: {n_cached}/{len(jobs)} reutilizadas de cache, sin llamar al LLM",
              file=sys.stderr)
    return out
