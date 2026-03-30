#!/usr/bin/env python3
"""
job_agent.py — Agente diario de búsqueda de empleo
Pipeline: Scraping → Agente 1 (filtro batch) → Agente 2 (análisis profundo) → Email + Logs
"""
import os, json, re, smtplib, yaml, logging, time, requests
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from groq import Groq

# ── CONFIG ───────────────────────────────────────────────────────────────────
BASE_DIR     = Path(__file__).parent
PROFILE_PATH = BASE_DIR / "profile.yaml"
SEEN_PATH    = BASE_DIR / "seen_jobs.txt"
QUEUE_PATH   = BASE_DIR / "application_queue.json"
LOGS_DIR     = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GMAIL_USER   = os.environ.get("GMAIL_USER")
GMAIL_PASS   = os.environ.get("GMAIL_APP_PASSWORD")
EMAIL_TO     = os.environ.get("EMAIL_TO", GMAIL_USER)

RUN_ID       = datetime.now().strftime("%Y%m%d_%H%M%S")
MAX_AGENT2   = 30

# ── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOGS_DIR / f"run_{RUN_ID}.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

# ── SEARCH CONFIG ─────────────────────────────────────────────────────────────
SEARCH_QUERIES = [
    "Data", "AI", "Cloud", "Analytics",
    "Head", "Lead", "Director", "Manager",
    "Architect", "Product", "Governance",
    "Platform", "Delivery", "Innovation", "Transformation",
]

LINKEDIN_LOCATION = "Madrid%2C%20Espa%C3%B1a"
LINKEDIN_GEO_ID   = "90009575"   # GeoID de Madrid en LinkedIn
LINKEDIN_DISTANCE = "50"          # km

# ── CARGA DE DATOS ────────────────────────────────────────────────────────────
def load_data():
    profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    seen    = set(SEEN_PATH.read_text().splitlines()) if SEEN_PATH.exists() else set()
    queue   = json.loads(QUEUE_PATH.read_text()) if QUEUE_PATH.exists() else []
    return profile, seen, queue

# ── SCRAPING ──────────────────────────────────────────────────────────────────
def scrape_job_ids() -> dict:
    """
    Devuelve dict {job_id: {id, title, company, link}} con IDs únicos.
    Madrid + 50km, últimas 24h, deduplicado entre búsquedas.
    """
    jobs    = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer":    "https://www.google.com/",
    }
    for q in SEARCH_QUERIES:
        log.info(f"  [{q}] @ Madrid +{LINKEDIN_DISTANCE}km")
        url = (
            f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords={q.replace(' ','%20')}"
            f"&location={LINKEDIN_LOCATION}"
            f"&geoId={LINKEDIN_GEO_ID}"
            f"&distance={LINKEDIN_DISTANCE}"
            f"&f_TPR=r86400"
            f"&start=0"
        )
        try:
            res  = requests.get(url, headers=headers, timeout=12)
            html = res.text

            ids       = re.findall(r'jobPosting:(\d+)', html)
            if not ids:
                ids   = re.findall(r'data-id=["\'](\d+)["\']', html)

            titles    = re.findall(r'class="[^"]*base-search-card__title[^"]*"[^>]*>(.*?)</h3', html, re.S)
            companies = re.findall(r'class="[^"]*base-search-card__subtitle[^"]*"[^>]*>.*?<a[^>]*>(.*?)</a', html, re.S)
            titles    = [re.sub(r'<[^<]+?>', '', t).strip() for t in titles]
            companies = [re.sub(r'<[^<]+?>', '', c).strip() for c in companies]

            added = 0
            for i, jid in enumerate(ids):
                if jid not in jobs:
                    jobs[jid] = {
                        "id":      jid,
                        "title":   titles[i]    if i < len(titles)    else "",
                        "company": companies[i] if i < len(companies) else "",
                        "link":    f"https://www.linkedin.com/jobs/view/{jid}/",
                    }
                    added += 1
            log.info(f"    +{added} nuevos (total: {len(jobs)})")
            time.sleep(3)
        except Exception as e:
            log.warning(f"  Error en [{q}]: {e}")

    log.info(f"Total IDs únicos scrapeados: {len(jobs)}")
    return jobs

# ── GROQ HELPER ───────────────────────────────────────────────────────────────
def call_groq(prompt: str, temperature: float = 0.1, max_retries: int = 3) -> str:
    client = Groq(api_key=GROQ_API_KEY, max_retries=0)
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=2000,
                timeout=30,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                m    = re.search(r'retry after (\d+(?:\.\d+)?)', err, re.I)
                wait = float(m.group(1)) + 2 if m else 65
                if attempt < max_retries - 1:
                    log.warning(f"  Rate limit — esperando {wait:.0f}s (intento {attempt+1}/{max_retries})")
                    time.sleep(wait)
                else:
                    log.error("  Rate limit agotado.")
                    return ""
            else:
                log.error(f"  Groq error: {e}")
                return ""
    return ""

def parse_json(text: str):
    text = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.M)
    text = re.sub(r'\s*```\s*$', '', text, flags=re.M)
    for pattern in [r'\{.*\}', r'\[.*\]']:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return None

# ── AGENTE 1: FILTRO BATCH ────────────────────────────────────────────────────
def agent1_filter_titles(jobs: dict, profile: dict) -> list:
    """
    Una sola llamada a Groq. Devuelve hasta MAX_AGENT2 IDs ordenados por
    similitud al perfil, de mayor a menor.
    """
    if not jobs:
        return []

    ideal_roles   = ", ".join(profile.get("ideal_role", {}).get("titles_of_interest", []))
    disqualifiers = "; ".join(profile.get("disqualifiers", []))
    summary       = profile.get("summary", "")[:400]

    titles_block = "\n".join(
        f'{j["id"]} | {j["title"]} | {j["company"]}'
        for j in jobs.values()
    )

    prompt = f"""Eres un filtro de ofertas de empleo para un profesional senior de Data & AI en Madrid.

PERFIL: {summary}
BUSCA ROLES COMO: {ideal_roles}
DESCARTAR SI: {disqualifiers}

LISTA (ID | Título | Empresa):
{titles_block}

DESCARTAR SIEMPRE (independientemente del título):
- Junior, Graduate, Intern, Becario, Trainee, Entry-level
- Freelance, Autónomo, por proyecto
- Ventas puras, Business Development, Account Executive, Sales Rep
- RRHH, Legal, Finanzas, Contabilidad sin componente tech
- Hostelería, Sanidad, Construcción, Inmobiliaria física, Logística operativa
- Roles 100% hands-on sin liderazgo ni arquitectura (ej: iOS Developer, Mobile Engineer, QA Tester)

INCLUIR SOLO SI:
- El rol tiene componente de Data, AI, Cloud, Analytics, Architecture, Platform o Digital
- O es un rol de liderazgo tech (Manager, Lead, Head, Director, Program Manager, Product Manager tech)
- Empresas conocidas con roles ambiguos: incluir si hay duda

TAREA:
- Selecciona IDs que cumplan los criterios anteriores.
- Devuelve MÁXIMO {MAX_AGENT2} IDs, ordenados de MAYOR a MENOR similitud al perfil.

RESPONDE SOLO CON ESTE JSON, sin texto adicional ni markdown:
{{"candidates": ["ID1", "ID2", "ID3"]}}"""

    log.info(f"  [Agente 1] Filtrando {len(jobs)} títulos...")
    raw = call_groq(prompt, temperature=0.0)
    log.info(f"  [Agente 1] Raw (200 chars): {raw[:200]}")

    result = parse_json(raw)
    if result and "candidates" in result:
        candidates = [str(c) for c in result["candidates"]][:MAX_AGENT2]
        log.info(f"  [Agente 1] OK — {len(candidates)} candidatos")
        return candidates

    # Fallback keyword si parse falla
    log.warning("  [Agente 1] Parse fallido — filtro keyword de emergencia")
    TECH = {
        "data","ai","cloud","analytics","architect","platform","program manager",
        "product manager","product owner","engineering manager","head of",
        "delivery manager","databricks","snowflake","azure","gcp","aws","llm",
        "machine learning","governance","datos","arquitecto","inteligencia artificial",
        "solutions","digital","tech","software","devops","mlops","mlops",
    }
    candidates = []
    for j in jobs.values():
        txt = (j["title"] + " " + j["company"]).lower()
        if any(kw in txt for kw in TECH):
            candidates.append(j["id"])
        if len(candidates) >= MAX_AGENT2:
            break
    log.info(f"  [Agente 1 emergencia] {len(candidates)} candidatos")
    return candidates

# ── EXTRACCIÓN DE DESCRIPCIÓN ─────────────────────────────────────────────────
def get_job_description(url: str) -> str:
    try:
        res  = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        html = res.text
        m    = re.search(r'class="[^"]*description__text[^"]*"[^>]*>(.*?)</div>', html, re.S)
        desc = re.sub(r'<[^<]+?>', ' ', m.group(1) if m else html)
        return re.sub(r'\s+', ' ', desc).strip()[:3500]
    except Exception as e:
        log.warning(f"  Error descripción: {e}")
        return ""

# ── AGENTE 2: ANÁLISIS PROFUNDO ───────────────────────────────────────────────
def agent2_deep_analysis(job: dict, profile: dict) -> dict | None:
    profile_str = yaml.dump({
        "summary":          profile.get("summary", ""),
        "technical_skills": profile.get("technical_skills", {}),
        "soft_skills":      profile.get("soft_skills", []),
        "ideal_role":       profile.get("ideal_role", {}),
        "compensable_gaps": profile.get("compensable_gaps", []),
        "disqualifiers":    profile.get("disqualifiers", []),
    }, allow_unicode=True, default_flow_style=False)

    prompt = f"""Eres un asesor de carrera experto y objetivo. Analiza el encaje de esta oferta con el perfil.

PERFIL:
{profile_str}

OFERTA:
Título:      {job.get('title','')}
Empresa:     {job.get('company','')}
Descripción: {job.get('description','')[:2500]}

REGLAS DE DESCARTE AUTOMÁTICO (si se cumple alguna → DESCARTAR sin analizar más):
- La oferta NO es tecnológica (Data/AI/Cloud/Software/Digital/Platform)
- Es un rol Junior, Graduate, Intern, Becario, Trainee o Entry-level
- Es Freelance, autónomo o por proyecto puntual
- Es ventas puras (Sales, BDR, Account Executive) sin componente tech
- Es RRHH, Legal, Finanzas sin componente tecnológico
- Es un rol 100% hands-on de desarrollo sin responsabilidad de arquitectura, liderazgo o estrategia
  (ej: iOS Developer, Mobile Developer, QA Engineer, Junior Developer)
- El perfil de Borja está claramente sobrecualificado Y el rol no tiene camino de crecimiento

REGLAS DE SCORING (solo si supera el descarte):
1. score_match (1-10): encaje real de skills, experiencia y aspiraciones con los requisitos del rol.
   - Penaliza si el rol es demasiado técnico/hands-on sin componente de liderazgo o arquitectura.
   - Bonifica si el rol combina arquitectura + gestión + estrategia (el perfil ideal de Borja).
2. score_empresa (1-10):
   - 8-10: empresa grande y reconocida (FAANG, Accenture, Deloitte, KPMG, McKinsey, BCG, IBM,
           BBVA, Santander, CaixaBank, Telefónica, Inditex, Repsol, Amadeus, Ferrovial, etc.)
   - 5-7: empresa mediana reconocida en su sector (>200 empleados)
   - 1-4: empresa pequeña (<50 empleados) o completamente desconocida
3. score_final = round((score_match * 0.7) + (score_empresa * 0.3))

CLASIFICACIÓN:
- VÁLIDA: encaja bien con el perfil senior de Borja, cumple requisitos principales
- VÁLIDA_CON_MATICES: hay gap real pero compensable dado el perfil de Borja
- DESCARTAR: no supera las reglas de descarte automático, o el gap es demasiado grande

RESPONDE SOLO JSON VÁLIDO sin texto adicional:
{{
  "classification": "VÁLIDA"|"VÁLIDA_CON_MATICES"|"DESCARTAR",
  "score_match": <1-10>,
  "score_empresa": <1-10>,
  "score_final": <1-10>,
  "match_summary": "<max 2 frases concisas>",
  "gaps": <null o "texto breve solo para VÁLIDA_CON_MATICES">,
  "discard_reason": <null o "1 frase solo para DESCARTAR">
}}"""

    raw    = call_groq(prompt, temperature=0.1)
    result = parse_json(raw)
    if not result:
        log.warning(f"  [Agente 2] Parse fallido para '{job.get('title')}'")
    return result

# ── EMAIL ─────────────────────────────────────────────────────────────────────
def build_email_html(valid: list, caveats: list, today: str, n_scraped: int, n_candidates: int) -> str:

    def dots(score):
        s = int(score) if score else 0
        return "".join(
            f'<span style="display:inline-block;width:7px;height:7px;border-radius:50%;'
            f'margin-right:2px;background:{"#22c55e" if i < s else "#e5e7eb"};"></span>'
            for i in range(10)
        )

    def card(j, idx, color, bg):
        a    = j["analysis"]
        gaps = a.get("gaps") or ""
        return f"""
        <div style="padding:14px 0;border-bottom:1px solid #f3f4f6;">
          <div style="display:flex;gap:10px;align-items:flex-start;">
            <div style="width:20px;height:20px;border-radius:50%;background:{bg};
                        display:flex;align-items:center;justify-content:center;
                        font-size:10px;font-weight:600;color:{color};flex-shrink:0;">{idx}</div>
            <div style="flex:1;min-width:0;">
              <div style="font-size:14px;font-weight:600;color:#111;margin-bottom:1px;
                          white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">
                {j.get('title','')}
              </div>
              <div style="font-size:12px;color:#9ca3af;margin-bottom:6px;">{j.get('company','')} &bull; Madrid</div>
              <div style="font-size:12px;color:#374151;margin-bottom:6px;line-height:1.5;">
                {a.get('match_summary','')}
              </div>
              {f'<div style="font-size:11px;color:#92400e;background:#fef9c3;padding:3px 8px;border-radius:4px;margin-bottom:6px;">{gaps}</div>' if gaps else ''}
              <div style="display:flex;align-items:center;gap:10px;">
                <div>{dots(a.get('score_final',0))}</div>
                <span style="font-size:11px;color:#9ca3af;">{a.get('score_final',0)}/10</span>
                <a href="{j.get('link','')}" style="font-size:11px;color:{color};
                   text-decoration:none;font-weight:500;margin-left:auto;">Ver →</a>
              </div>
            </div>
          </div>
        </div>"""

    valid_html  = "".join(card(j, i+1, "#16a34a", "#dcfce7") for i, j in enumerate(valid))
    caveat_html = "".join(card(j, i+1, "#d97706", "#fef3c7") for i, j in enumerate(caveats))
    none_html   = '<div style="padding:12px 0;font-size:12px;color:#d1d5db;">Ninguna hoy.</div>'

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f9fafb;
             font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;">
<div style="max-width:580px;margin:20px auto;background:#fff;
            border-radius:10px;border:1px solid #e5e7eb;overflow:hidden;">

  <div style="padding:18px 20px;border-bottom:1px solid #f3f4f6;
              display:flex;align-items:center;justify-content:space-between;">
    <div>
      <div style="font-size:16px;font-weight:600;color:#111;">Empleos del día</div>
      <div style="font-size:11px;color:#9ca3af;margin-top:2px;">
        {today} &bull; Madrid +50km &bull; {n_scraped} vistas &bull; {n_candidates} analizadas
      </div>
    </div>
    <div style="display:flex;gap:16px;text-align:center;">
      <div>
        <div style="font-size:22px;font-weight:600;color:#16a34a;">{len(valid)}</div>
        <div style="font-size:10px;color:#9ca3af;">válidas</div>
      </div>
      <div>
        <div style="font-size:22px;font-weight:600;color:#d97706;">{len(caveats)}</div>
        <div style="font-size:10px;color:#9ca3af;">matices</div>
      </div>
    </div>
  </div>

  <div style="padding:4px 20px 0;">
    <div style="font-size:10px;font-weight:600;color:#16a34a;letter-spacing:0.08em;
                text-transform:uppercase;padding:12px 0 0;">Válidas</div>
    {valid_html or none_html}
  </div>

  <div style="padding:4px 20px 0;">
    <div style="font-size:10px;font-weight:600;color:#d97706;letter-spacing:0.08em;
                text-transform:uppercase;padding:12px 0 0;">Con matices</div>
    {caveat_html or none_html}
  </div>

  <div style="padding:12px 20px;border-top:1px solid #f3f4f6;margin-top:8px;">
    <div style="font-size:10px;color:#d1d5db;text-align:center;">run {RUN_ID}</div>
  </div>

</div>
</body></html>"""

def send_email(html: str, today: str, n_valid: int, n_caveats: int):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Empleo] {n_valid} válidas · {n_caveats} matices — {today}"
    msg["From"]    = GMAIL_USER
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_PASS)
        s.send_message(msg)
    log.info(f"Email enviado a {EMAIL_TO}")

def send_diagnostic_email(today: str, n_scraped: int, n_new: int, n_candidates: int):
    msg = MIMEMultipart()
    msg["Subject"] = f"[Empleo Bot] Sin resultados — {today}"
    msg["From"]    = GMAIL_USER
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(
        f"Run {RUN_ID}\n\nScrapeadas: {n_scraped}\nNuevas: {n_new}\nAnalizadas: {n_candidates}\nResultado: ninguna superó el análisis.",
        "plain"
    ))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_PASS)
        s.send_message(msg)

# ── LOGS ──────────────────────────────────────────────────────────────────────
def save_scraping_log(jobs: dict):
    p = LOGS_DIR / f"scraping_{RUN_ID}.json"
    p.write_text(json.dumps({"run_id": RUN_ID, "total": len(jobs), "jobs": list(jobs.values())}, ensure_ascii=False, indent=2))
    log.info(f"Log scraping: {p.name}")

def save_agent1_log(candidate_ids: list, jobs: dict):
    p = LOGS_DIR / f"agent1_{RUN_ID}.json"
    p.write_text(json.dumps({"run_id": RUN_ID, "total": len(candidate_ids), "candidates": [jobs[jid] for jid in candidate_ids if jid in jobs]}, ensure_ascii=False, indent=2))
    log.info(f"Log Agente 1: {p.name}")

def save_results_log(valid: list, caveats: list):
    p = LOGS_DIR / f"results_{RUN_ID}.json"
    p.write_text(json.dumps({"run_id": RUN_ID, "valid": valid, "caveats": caveats}, ensure_ascii=False, indent=2))
    log.info(f"Log resultados: {p.name}")

# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    today = datetime.now().strftime("%d/%m/%Y")
    log.info(f"{'='*55}")
    log.info(f"AGENTE DE EMPLEO — {today} — {RUN_ID}")
    log.info(f"{'='*55}")

    profile, seen_jobs, queue = load_data()

    # PASO 1: SCRAPING
    all_jobs = scrape_job_ids()
    save_scraping_log(all_jobs)
    new_jobs = {jid: j for jid, j in all_jobs.items() if jid not in seen_jobs}
    log.info(f"Nuevos: {len(new_jobs)} de {len(all_jobs)} scrapeados")

    if not new_jobs:
        log.info("Sin nuevas ofertas hoy.")
        send_diagnostic_email(today, len(all_jobs), 0, 0)
        return

    # PASO 2: AGENTE 1
    candidate_ids = list(dict.fromkeys(agent1_filter_titles(new_jobs, profile)))
    save_agent1_log(candidate_ids, new_jobs)
    log.info(f"Candidatos para Agente 2: {len(candidate_ids)}")

    # PASO 3: AGENTE 2
    valid, caveats = [], []

    for jid in candidate_ids:
        job = new_jobs.get(jid)
        if not job:
            continue

        log.info(f"  [Agente 2] {job.get('title','?')} @ {job.get('company','?')}")
        job["description"] = get_job_description(job["link"])
        analysis = agent2_deep_analysis(job, profile)
        seen_jobs.add(jid)

        if not analysis:
            continue

        job["analysis"] = analysis
        clf   = analysis.get("classification", "DESCARTAR")
        score = analysis.get("score_final", 0)

        if clf == "VÁLIDA":
            valid.append(job)
            log.info(f"    → VÁLIDA (score {score})")
        elif clf == "VÁLIDA_CON_MATICES":
            caveats.append(job)
            log.info(f"    → MATICES (score {score})")
        else:
            log.info(f"    → DESCARTADA: {analysis.get('discard_reason','')}")

        queue.append({
            "date": datetime.now().strftime("%Y-%m-%d"),
            "run_id": RUN_ID, "applied": False,
            "classification": clf,
            "score_final":   score,
            "score_match":   analysis.get("score_match", 0),
            "score_empresa": analysis.get("score_empresa", 0),
            "title":         job.get("title", ""),
            "company":       job.get("company", ""),
            "link":          job.get("link", ""),
            "match_summary": analysis.get("match_summary", ""),
        })
        time.sleep(15)

    # PASO 4: ORDENAR
    valid.sort(key=lambda j: j["analysis"].get("score_final", 0), reverse=True)
    caveats.sort(key=lambda j: j["analysis"].get("score_final", 0), reverse=True)
    queue.sort(key=lambda x: x.get("score_final", 0), reverse=True)

    # PASO 5: PERSISTENCIA
    SEEN_PATH.write_text("\n".join(sorted(seen_jobs)))
    QUEUE_PATH.write_text(json.dumps(queue, indent=2, ensure_ascii=False))
    save_results_log(valid, caveats)

    # PASO 6: EMAIL
    log.info(f"Resultado — Válidas: {len(valid)}, Matices: {len(caveats)}")
    if valid or caveats:
        html = build_email_html(valid, caveats, today, len(all_jobs), len(candidate_ids))
        send_email(html, today, len(valid), len(caveats))
    else:
        send_diagnostic_email(today, len(all_jobs), len(new_jobs), len(candidate_ids))

    log.info(f"FIN — {RUN_ID}")

if __name__ == "__main__":
    main()
