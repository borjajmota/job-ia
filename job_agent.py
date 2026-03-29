#!/usr/bin/env python3
"""
job_agent.py
Agente diario de búsqueda de empleo en LinkedIn para Borja Jiménez Mota.
Scraping → Pre-filtro → Análisis IA (Gemini) → Email HTML
"""

import os
import json
import re
import smtplib
import yaml
import logging
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import requests
from linkedin_jobs_scraper import LinkedinScraper
from linkedin_jobs_scraper.events import Events, EventData, EventMetrics
from linkedin_jobs_scraper.filters import RelevanceFilters, TimeFilters, TypeFilters
from linkedin_jobs_scraper.query import Query, QueryOptions, QueryFilters
import google.generativeai as genai

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR        = Path(__file__).parent
PROFILE_PATH    = BASE_DIR / "profile.yaml"
SEEN_JOBS_PATH  = BASE_DIR / "seen_jobs.txt"
QUEUE_PATH      = BASE_DIR / "application_queue.json"

GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]
GMAIL_USER      = os.environ["GMAIL_USER"]          # tu cuenta Gmail
GMAIL_APP_PASS  = os.environ["GMAIL_APP_PASSWORD"]  # App Password de Google
EMAIL_TO        = os.environ.get("EMAIL_TO", GMAIL_USER)

# Búsquedas amplias en Madrid (sin filtrar por título)
SEARCH_QUERIES = [
    "Data AI Cloud",
    "Data Platform Analytics",
    "Inteligencia Artificial Machine Learning",
    "Solutions Architect Cloud",
    "Program Manager Technology",
    "Head of Data Engineering",
    "AI Innovation Lead",
]

# ──────────────────────────────────────────────
# PRE-FILTRO LIGERO (sin IA, sin coste)
# ──────────────────────────────────────────────
HARD_DISCARD_PATTERNS = [
    r"\b(enfermer[ao]|médic[ao]|farmacéutic[ao]|abogad[ao]|juríd|notaría)\b",
    r"\b(camarero|cocinero|hostelería|restaurante|hotel|recepcionista)\b",
    r"\b(administrativ[ao] contable|auxiliar administrativ)\b",
    r"\b(conductor|repartidor|almacén|operario|carretillero)\b",
    r"\b(profesor de (inglés|matemáticas|primaria|secundaria))\b",
    r"\b(comercial de seguros|agente comercial)\b",
    r"\b(fontanero|electricista|albañil|carpintero)\b",
    r"\bjunior developer\b",
    r"\bgraduate scheme\b",
    r"\binternship\b",
    r"\bprácticas\b",
]

REQUIRED_SIGNAL_PATTERNS = [
    r"\b(data|datos|dato)\b",
    r"\b(cloud|nube)\b",
    r"\b(ia|ai|machine learning|ml|llm|analytics|analítica)\b",
    r"\b(architect|arquitecto|arquitectura)\b",
    r"\b(program manager|project manager|delivery|product manager|product owner)\b",
    r"\b(engineering manager|head of|director de)\b",
    r"\b(databricks|snowflake|azure|gcp|aws)\b",
    r"\b(bi|business intelligence|power bi|tableau)\b",
    r"\b(plataforma de datos|data platform|data strategy|estrategia de datos)\b",
]

def pre_filter(title: str, description: str) -> bool:
    """Devuelve True si la oferta pasa el pre-filtro (merece análisis IA)."""
    text = (title + " " + description).lower()

    # Descarte duro
    for pattern in HARD_DISCARD_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            log.info(f"  [DESCARTADO por pre-filtro] {title}")
            return False

    # Al menos una señal relevante
    for pattern in REQUIRED_SIGNAL_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True

    log.info(f"  [DESCARTADO por falta de señal] {title}")
    return False

# ──────────────────────────────────────────────
# CARGA DE PERFIL
# ──────────────────────────────────────────────
def load_profile() -> dict:
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# ──────────────────────────────────────────────
# SEEN JOBS (deduplicación entre días)
# ──────────────────────────────────────────────
def load_seen_jobs() -> set:
    if SEEN_JOBS_PATH.exists():
        return set(SEEN_JOBS_PATH.read_text().splitlines())
    return set()

def save_seen_jobs(seen: set):
    SEEN_JOBS_PATH.write_text("\n".join(sorted(seen)))

# ──────────────────────────────────────────────
# APPLICATION QUEUE (cola persistente)
# ──────────────────────────────────────────────
def load_queue() -> list:
    if QUEUE_PATH.exists():
        return json.loads(QUEUE_PATH.read_text())
    return []

def save_queue(queue: list):
    QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2))

# ──────────────────────────────────────────────
# ANÁLISIS IA CON GEMINI
# ──────────────────────────────────────────────
def build_analysis_prompt(profile: dict, job: dict) -> str:
    profile_str = yaml.dump(profile, allow_unicode=True, default_flow_style=False)
    return f"""
Eres un asesor de carrera honesto y riguroso. Analiza si esta oferta de empleo encaja
con el perfil profesional de Borja. Sé sincero: no infles el match, no lo subestimes.

═══════════════════════════════════════
PERFIL DE BORJA
═══════════════════════════════════════
{profile_str}

═══════════════════════════════════════
OFERTA DE EMPLEO
═══════════════════════════════════════
Título: {job['title']}
Empresa: {job['company']}
Ubicación: {job['location']}
Descripción:
{job['description'][:3000]}

═══════════════════════════════════════
INSTRUCCIONES DE ANÁLISIS
═══════════════════════════════════════
Clasifica la oferta en UNA de estas 3 categorías:

VÁLIDA → El perfil de Borja cumple los requisitos principales. Encaja bien.
VÁLIDA_CON_MATICES → Hay algún gap relevante, pero compensable. Merece considerar.
DESCARTAR → No encaja. El gap es demasiado grande o hay un disqualifier claro.

Responde ÚNICAMENTE con un JSON válido con esta estructura exacta:
{{
  "classification": "VÁLIDA" | "VÁLIDA_CON_MATICES" | "DESCARTAR",
  "score": <número entero del 1 al 10>,
  "match_summary": "<1-2 frases muy cortas explicando por qué encaja>",
  "gaps": "<solo para VÁLIDA_CON_MATICES: qué falta y cómo Borja puede compensarlo. 2-3 frases max. Para VÁLIDA y DESCARTAR: null>",
  "discard_reason": "<solo para DESCARTAR: razón en 1 frase. Para el resto: null>"
}}

Criterios de scoring (1-10):
- 9-10: Borja es candidato ideal, encaja en casi todo
- 7-8: Buen match, algún gap menor
- 5-6: Match parcial, gaps compensables pero reales
- 3-4: Gap importante en área core
- 1-2: No encaja
"""

def analyze_job_with_ai(profile: dict, job: dict) -> dict:
    """Llama a Gemini y devuelve el análisis estructurado."""
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel("gemini-1.5-flash")

    prompt = build_analysis_prompt(profile, job)

    try:
        response = model.generate_content(
            prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=500,
            )
        )
        raw = response.text.strip()
        # Limpia posibles markdown fences
        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        result = json.loads(raw)
        return result
    except Exception as e:
        log.error(f"Error en Gemini para '{job['title']}': {e}")
        return {
            "classification": "DESCARTAR",
            "score": 0,
            "match_summary": "Error en análisis IA",
            "gaps": None,
            "discard_reason": f"Error técnico: {str(e)}"
        }

# ──────────────────────────────────────────────
# SCRAPING DE LINKEDIN
# ──────────────────────────────────────────────
scraped_jobs = []

def on_data(data: EventData):
    scraped_jobs.append({
        "job_id":     data.job_id,
        "title":      data.title or "",
        "company":    data.company or "",
        "location":   data.location or "",
        "description": data.description or "",
        "link":       data.link or "",
        "date":       data.date or "",
    })

def on_error(error):
    log.error(f"Scraper error: {error}")

def on_end():
    log.info(f"Scraping finalizado. {len(scraped_jobs)} ofertas recogidas.")

def scrape_linkedin_jobs() -> list:
    """Lanza el scraper y devuelve la lista de ofertas."""
    li_at_cookie = os.environ.get("LI_AT_COOKIE") 

    # 1. Creamos el scraper sin la cookie en el constructor para evitar el TypeError
    scraper = LinkedinScraper(
        chrome_executable_path=None, 
        headless=True,
        max_workers=1,
        slow_mo=5,
        page_load_timeout=40
    )

    scraper.on(Events.DATA, on_data)
    scraper.on(Events.ERROR, on_error)
    scraper.on(Events.END, on_end)

    # 2. Definimos las queries
    queries = [
        Query(
            query=q,
            options=QueryOptions(
                locations=["Madrid, España"],
                apply_link=True,
                limit=15,
                filters=QueryFilters(
                    relevance=RelevanceFilters.RECENT,
                    time=TimeFilters.DAY, # Mantén WEEK para asegurar resultados en el test
                    type=[TypeFilters.FULL_TIME, TypeFilters.CONTRACT],
                )
            )
        )
        for q in SEARCH_QUERIES
    ]

    # 3. PASAMOS LA COOKIE AQUÍ (Esta es la forma correcta para esta librería)
    try:
        scraper.run(queries, li_at=li_at_cookie) 
    except Exception as e:
        log.error(f"Error durante el scraping: {e}")
    
    return scraped_jobs
# ──────────────────────────────────────────────
# EMAIL HTML
# ──────────────────────────────────────────────
def build_email_html(valid: list, with_caveats: list, date_str: str) -> str:
    """Genera el cuerpo HTML del email de informe."""

    def job_card_valid(j: dict, idx: int) -> str:
        return f"""
        <div style="border-left:4px solid #22c55e;padding:14px 18px;margin-bottom:16px;
                    background:#f0fdf4;border-radius:0 8px 8px 0;">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">
            <span style="background:#22c55e;color:white;font-size:11px;font-weight:600;
                         padding:2px 8px;border-radius:20px;">#{idx} VÁLIDA</span>
            <span style="background:#dcfce7;color:#166534;font-size:11px;font-weight:600;
                         padding:2px 8px;border-radius:20px;">Score: {j['analysis']['score']}/10</span>
          </div>
          <div style="font-size:17px;font-weight:600;color:#111;margin:6px 0 2px;">{j['title']}</div>
          <div style="font-size:13px;color:#555;margin-bottom:8px;">{j['company']} &bull; {j['location']}</div>
          <div style="font-size:13px;color:#333;margin-bottom:12px;">{j['analysis']['match_summary']}</div>
          <a href="{j['link']}" style="display:inline-block;background:#16a34a;color:white;
                    text-decoration:none;padding:7px 18px;border-radius:6px;font-size:13px;
                    font-weight:600;">Aplicar →</a>
        </div>"""

    def job_card_caveat(j: dict, idx: int) -> str:
        return f"""
        <div style="border-left:4px solid #f59e0b;padding:14px 18px;margin-bottom:16px;
                    background:#fffbeb;border-radius:0 8px 8px 0;">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">
            <span style="background:#f59e0b;color:white;font-size:11px;font-weight:600;
                         padding:2px 8px;border-radius:20px;">#{idx} CON MATICES</span>
            <span style="background:#fef3c7;color:#92400e;font-size:11px;font-weight:600;
                         padding:2px 8px;border-radius:20px;">Score: {j['analysis']['score']}/10</span>
          </div>
          <div style="font-size:17px;font-weight:600;color:#111;margin:6px 0 2px;">{j['title']}</div>
          <div style="font-size:13px;color:#555;margin-bottom:8px;">{j['company']} &bull; {j['location']}</div>
          <div style="font-size:13px;color:#333;margin-bottom:6px;">{j['analysis']['match_summary']}</div>
          <div style="font-size:12px;color:#92400e;background:#fef9c3;padding:8px 12px;
                      border-radius:6px;margin-bottom:12px;">
            <strong>Matices:</strong> {j['analysis']['gaps']}
          </div>
          <a href="{j['link']}" style="display:inline-block;background:#d97706;color:white;
                    text-decoration:none;padding:7px 18px;border-radius:6px;font-size:13px;
                    font-weight:600;">Aplicar →</a>
        </div>"""

    valid_cards    = "".join(job_card_valid(j, i+1)    for i, j in enumerate(valid))
    caveat_cards   = "".join(job_card_caveat(j, i+1)   for i, j in enumerate(with_caveats))

    valid_section = f"""
    <h2 style="color:#166534;font-size:16px;margin:24px 0 12px;border-bottom:2px solid #22c55e;
               padding-bottom:6px;">
      ✅ OFERTAS VÁLIDAS ({len(valid)})
    </h2>
    {valid_cards if valid_cards else '<p style="color:#888;font-size:13px;">Ninguna hoy.</p>'}
    """ if True else ""

    caveat_section = f"""
    <h2 style="color:#92400e;font-size:16px;margin:24px 0 12px;border-bottom:2px solid #f59e0b;
               padding-bottom:6px;">
      ⚠️ VÁLIDAS CON MATICES ({len(with_caveats)})
    </h2>
    {caveat_cards if caveat_cards else '<p style="color:#888;font-size:13px;">Ninguna hoy.</p>'}
    """ if True else ""

    return f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
             max-width:680px;margin:0 auto;padding:20px;background:#f9fafb;">

  <div style="background:white;border-radius:12px;padding:28px;box-shadow:0 1px 4px rgba(0,0,0,.08);">

    <div style="border-bottom:1px solid #e5e7eb;padding-bottom:16px;margin-bottom:20px;">
      <div style="font-size:22px;font-weight:700;color:#111;">
        Informe diario de empleo
      </div>
      <div style="font-size:13px;color:#888;margin-top:4px;">{date_str} &bull; Madrid</div>
    </div>

    <div style="display:flex;gap:12px;margin-bottom:8px;">
      <div style="flex:1;background:#f0fdf4;border-radius:8px;padding:14px;text-align:center;">
        <div style="font-size:28px;font-weight:700;color:#16a34a;">{len(valid)}</div>
        <div style="font-size:12px;color:#555;margin-top:2px;">Válidas</div>
      </div>
      <div style="flex:1;background:#fffbeb;border-radius:8px;padding:14px;text-align:center;">
        <div style="font-size:28px;font-weight:700;color:#d97706;">{len(with_caveats)}</div>
        <div style="font-size:12px;color:#555;margin-top:2px;">Con matices</div>
      </div>
    </div>

    {valid_section}
    {caveat_section}

    <div style="margin-top:28px;padding-top:16px;border-top:1px solid #e5e7eb;
                font-size:11px;color:#aaa;text-align:center;">
      Generado automáticamente por tu agente de empleo &bull; borja.jmota@gmail.com
    </div>
  </div>
</body>
</html>"""

def send_email(html_body: str, date_str: str, n_valid: int, n_caveats: int):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Empleos] {n_valid} válidas, {n_caveats} con matices — {date_str}"
    msg["From"]    = GMAIL_USER
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_USER, GMAIL_APP_PASS)
        server.sendmail(GMAIL_USER, EMAIL_TO, msg.as_string())
    log.info(f"Email enviado a {EMAIL_TO}")

# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────
def main():
    today       = datetime.now().strftime("%d/%m/%Y")
    log.info(f"=== Agente de empleo arrancando — {today} ===")

    profile     = load_profile()
    seen_jobs   = load_seen_jobs()
    queue       = load_queue()

    # 1. SCRAPING
    log.info("Scrapeando LinkedIn...")
    raw_jobs = scrape_linkedin_jobs()
    log.info(f"Total scrapeadas: {len(raw_jobs)}")

    # 2. DEDUPLICACIÓN
    new_jobs = [j for j in raw_jobs if j["job_id"] not in seen_jobs]
    log.info(f"Nuevas (no vistas antes): {len(new_jobs)}")

    # 3. PRE-FILTRO LIGERO
    candidate_jobs = [j for j in new_jobs if pre_filter(j["title"], j["description"])]
    log.info(f"Pasan pre-filtro: {len(candidate_jobs)}")

    # 4. ANÁLISIS IA
    valid        = []
    with_caveats = []

    for job in candidate_jobs:
        log.info(f"  Analizando: {job['title']} @ {job['company']}")
        analysis = analyze_job_with_ai(profile, job)
        job["analysis"] = analysis

        clf = analysis.get("classification", "DESCARTAR")
        if clf == "VÁLIDA":
            valid.append(job)
            log.info(f"    → VÁLIDA (score {analysis.get('score')})")
        elif clf == "VÁLIDA_CON_MATICES":
            with_caveats.append(job)
            log.info(f"    → MATICES (score {analysis.get('score')})")
        else:
            log.info(f"    → DESCARTADA")

        # Marcar como visto
        seen_jobs.add(job["job_id"])

    # 5. ORDENAR POR SCORE DESC
    valid.sort(key=lambda j: j["analysis"].get("score", 0), reverse=True)
    with_caveats.sort(key=lambda j: j["analysis"].get("score", 0), reverse=True)

    # 6. ACTUALIZAR COLA DE APLICACIÓN
    today_iso = datetime.now().strftime("%Y-%m-%d")
    for job in valid + with_caveats:
        queue.append({
            "date_found":       today_iso,
            "applied":          False,
            "classification":   job["analysis"]["classification"],
            "score":            job["analysis"].get("score", 0),
            "title":            job["title"],
            "company":          job["company"],
            "link":             job["link"],
            "match_summary":    job["analysis"].get("match_summary", ""),
        })
    # Ordenar cola completa por score desc
    queue.sort(key=lambda x: x["score"], reverse=True)

    # 7. PERSISTIR
    save_seen_jobs(seen_jobs)
    save_queue(queue)

    # 8. EMAIL
    if valid or with_caveats:
        html = build_email_html(valid, with_caveats, today)
        send_email(html, today, len(valid), len(with_caveats))
    else:
        log.info("Sin resultados relevantes hoy. No se envía email.")

    log.info(f"=== Finalizado. Válidas: {len(valid)}, Matices: {len(with_caveats)} ===")

if __name__ == "__main__":
    main()
