#!/usr/bin/env python3
"""
job_agent.py  —  Pipeline de doble agente
  Agente 1: filtra títulos en batch (1 llamada Gemini para todos)
  Agente 2: análisis profundo por candidato (1 llamada por oferta)
"""
import os, json, re, smtplib, yaml, logging, time, requests
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from groq import Groq

# ── CONFIG ─────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR     = Path(__file__).parent
PROFILE_PATH = BASE_DIR / "profile.yaml"
SEEN_PATH    = BASE_DIR / "seen_jobs.txt"
QUEUE_PATH   = BASE_DIR / "application_queue.json"

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
GMAIL_USER     = os.environ.get("GMAIL_USER")
GMAIL_PASS     = os.environ.get("GMAIL_APP_PASSWORD")
EMAIL_TO       = os.environ.get("EMAIL_TO", GMAIL_USER)

# Búsquedas amplias — objetivo: dragar el máximo de ofertas tech/data posibles.
# La estrategia es usar términos PARAGUAS que aparecen en casi cualquier oferta
# relevante, sin filtrar por título. El Agente 1 se encarga del filtro real.
# Cada query devuelve hasta ~25 resultados de las últimas 24h en Madrid.
SEARCH_QUERIES = [
    # ── Core tech/data ──────────────────────────────────────────────────
    "Data",           # Data Engineer, Head of Data, Data Analyst, Data PM...
    "AI",             # AI Lead, ML Engineer, AI Product Manager, LLM...
    "Cloud",          # Cloud Architect, Platform Engineer, DevOps Lead...
    "Analytics",      # Analytics Manager, Head of Analytics, BI Lead...
    # ── Roles de liderazgo ──────────────────────────────────────────────
    "Head",           # Head of Data, Head of Engineering, Head of Product...
    "Lead",           # Tech Lead, Data Lead, AI Lead, Engineering Lead...
    "Director",       # Director of Data, Director of Engineering...
    "Manager",        # Engineering Manager, Program Manager, Product Manager...
    # ── Roles de arquitectura y producto ────────────────────────────────
    "Architect",      # Solutions Architect, Enterprise Architect, Data Architect...
    "Product",        # Product Manager, Product Owner, Product Lead (tech)...
    # ── Especialidades alineadas con tu perfil ──────────────────────────
    "Governance",     # Data Governance, AI Governance, IT Governance...
    "Platform",       # Platform Engineer, Data Platform, ML Platform Lead...
    "Delivery",       # Delivery Manager, Program Delivery, Agile Delivery...
    "Innovation",     # Innovation Manager, AI Innovation, Digital Innovation...
    "Transformation", # Digital Transformation, Data Transformation Lead...
]

# Ubicaciones a rastrear
SEARCH_LOCATIONS = [
    "Madrid%2C%20Espa%C3%B1a",
    "Spain",  # captura remoto España
]

# ── CARGA DE DATOS ──────────────────────────────────────────────────────────
def load_data():
    profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    seen    = set(SEEN_PATH.read_text().splitlines()) if SEEN_PATH.exists() else set()
    queue   = json.loads(QUEUE_PATH.read_text()) if QUEUE_PATH.exists() else []
    return profile, seen, queue

# ── SCRAPING DE IDs ─────────────────────────────────────────────────────────
def scrape_job_ids() -> list[dict]:
    """
    Draga LinkedIn con términos paraguas (Data, AI, Cloud, Manager...) en Madrid
    y Spain (remoto). La deduplicación dentro de la misma ejecución es por set.
    El Agente 1 se encarga del filtro real por relevancia.
    """
    jobs        = []
    seen_in_run = set()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer":    "https://www.google.com/",
    }
    for location in SEARCH_LOCATIONS:
        for q in SEARCH_QUERIES:
            log.info(f"  [{q}] @ [{location}]")
            url = (
                f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
                f"?keywords={q.replace(' ','%20')}&location={location}"
                f"&f_TPR=r86400&start=0"
            )
            try:
                res = requests.get(url, headers=headers, timeout=12)
                ids = re.findall(r'jobPosting:(\d+)', res.text)
                if not ids:
                    ids = re.findall(r'data-id=["\'](\d+)["\']', res.text)
                # Extraemos título y empresa directamente del HTML de resultados
                # para evitar visitar cada URL individualmente en el Agente 1
                titles   = re.findall(r'class="[^"]*base-search-card__title[^"]*"[^>]*>(.*?)</h3', res.text, re.S)
                companies= re.findall(r'class="[^"]*base-search-card__subtitle[^"]*"[^>]*>.*?<a[^>]*>(.*?)</a', res.text, re.S)
                # Limpiamos el HTML
                titles    = [re.sub(r'<[^<]+?>', '', t).strip() for t in titles]
                companies = [re.sub(r'<[^<]+?>', '', c).strip() for c in companies]
                added = 0
                for i, jid in enumerate(ids):
                    if jid not in seen_in_run:
                        seen_in_run.add(jid)
                        jobs.append({
                            "id":      jid,
                            "link":    f"https://www.linkedin.com/jobs/view/{jid}/",
                            "title":   titles[i]   if i < len(titles)    else "",
                            "company": companies[i] if i < len(companies) else "",
                        })
                        added += 1
                log.info(f"    +{added} nuevos (total: {len(jobs)})")
                time.sleep(3)
            except Exception as e:
                log.warning(f"  Error scraping '{q}': {e}")
    log.info(f"Total IDs únicos recogidos: {len(jobs)}")
    return jobs

# ── EXTRACCIÓN DE TEXTO DE OFERTA ───────────────────────────────────────────
def get_job_page(url: str) -> dict:
    """
    Extrae título, empresa y descripción de la página pública de la oferta.
    Devuelve dict con title, company, description (o vacíos si falla).
    """
    try:
        res  = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        html = res.text

        # Título
        title_match = re.search(r'<h1[^>]*class="[^"]*top-card-layout__title[^"]*"[^>]*>(.*?)</h1>', html, re.S)
        if not title_match:
            title_match = re.search(r'<title>(.*?)(?:\s*\|.*)?</title>', html)
        title = re.sub('<[^<]+?>', '', title_match.group(1)).strip() if title_match else ""

        # Empresa
        company_match = re.search(r'class="[^"]*topcard__org-name-link[^"]*"[^>]*>(.*?)</a>', html, re.S)
        company = re.sub('<[^<]+?>', '', company_match.group(1)).strip() if company_match else ""

        # Descripción limpia
        desc_match = re.search(r'class="[^"]*description__text[^"]*"[^>]*>(.*?)</div>', html, re.S)
        if desc_match:
            description = re.sub('<[^<]+?>', ' ', desc_match.group(1))
            description = re.sub(r'\s+', ' ', description).strip()[:3500]
        else:
            # Fallback: texto completo limpio
            description = re.sub('<[^<]+?>', ' ', html)
            description = re.sub(r'\s+', ' ', description).strip()[:3500]

        return {"title": title, "company": company, "description": description}
    except Exception as e:
        log.warning(f"  Error extrayendo {url}: {e}")
        return {"title": "", "company": "", "description": ""}

# ── GEMINI HELPER ───────────────────────────────────────────────────────────
def call_gemini(prompt: str, temperature: float = 0.1, max_retries: int = 3) -> str:
    """Llama a Groq con reintentos inteligentes ante rate limit (429)."""
    client = Groq(api_key=GROQ_API_KEY, max_retries=0)  # nosotros gestionamos los retries
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=1500,
                timeout=30,
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate_limit" in err_str.lower():
                wait = 60
                delay_match = re.search(r"retry after (\d+(?:\.\d+)?)", err_str, re.IGNORECASE)
                if delay_match:
                    wait = float(delay_match.group(1)) + 2
                if attempt < max_retries - 1:
                    log.warning(f"  Rate limit — esperando {wait:.0f}s (intento {attempt+1}/{max_retries})")
                    time.sleep(wait)
                else:
                    log.error(f"  Rate limit agotado tras {max_retries} intentos. Saltando.")
                    return ""
            else:
                log.error(f"  Groq error: {e}")
                return ""
    return ""

def parse_json_from_response(text: str):
    """Extrae y parsea el primer bloque JSON de la respuesta."""
    # Limpia posibles markdown fences
    text = re.sub(r"^```json\s*", "", text, flags=re.M)
    text = re.sub(r"\s*```\s*$", "", text, flags=re.M)
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    return None

# ── AGENTE 1: FILTRO DE TÍTULOS EN BATCH ────────────────────────────────────
def agent1_filter_titles(jobs_with_titles: list[dict], profile: dict) -> list[str]:
    """
    Recibe lista de {id, title, company} y devuelve los IDs candidatos.
    UNA SOLA llamada a Gemini para todos los títulos.
    """
    if not jobs_with_titles:
        return []

    # Construimos la lista numerada de títulos para el prompt
    titles_block = "\n".join(
        f'{j["id"]} | {j["title"]} | {j["company"]}'
        for j in jobs_with_titles
    )

    ideal_roles   = ", ".join(profile.get("ideal_role", {}).get("titles_of_interest", []))
    disqualifiers = "\n".join(f"- {d}" for d in profile.get("disqualifiers", []))
    summary       = profile.get("summary", "")[:600]

    prompt = f"""
Eres un filtro rápido de ofertas de empleo. Tu misión es determinar, SOLO por el título
del puesto y el nombre de la empresa, si una oferta PODRÍA encajar con este profesional.

PERFIL (resumen):
{summary}

ROLES QUE BUSCA: {ideal_roles}

MOTIVOS DE DESCARTE AUTOMÁTICO (si el título sugiere alguno de estos, descarta):
{disqualifiers}

LISTA DE OFERTAS (formato: ID | Título | Empresa):
{titles_block}

INSTRUCCIONES:
- Sé INCLUSIVO: si hay la menor duda, inclúyelo como candidato. Mejor falso positivo que falso negativo.
- Descarta solo los que claramente NO tienen ninguna relación con Data, AI, Cloud, tecnología, gestión de equipos tech o arquitectura.
- NO descartes por sector (puede ser retail, banca, salud... da igual si el rol es tech).
- NO descartes por nombre de empresa desconocida.

RESPONDE ÚNICAMENTE CON JSON VÁLIDO. Sin explicaciones, sin texto extra, sin markdown.
Formato EXACTO (sustituye ID1, ID2... con los IDs reales de la lista de arriba):
{{"candidates": ["ID1", "ID2"], "discarded": ["ID3", "ID4"]}}
"""

    log.info(f"  [Agente 1] Filtrando {len(jobs_with_titles)} títulos en 1 llamada Groq...")
    raw = call_gemini(prompt, temperature=0.0)
    log.info(f"  [Agente 1] Respuesta cruda (primeros 300 chars): {raw[:300]}")
    result = parse_json_from_response(raw)

    if not result or "candidates" not in result:
        log.warning("  [Agente 1] Parse fallido — aplicando filtro de emergencia por keywords")
        # Filtro de emergencia: keywords que claramente indican relevancia
        RELEVANT_KEYWORDS = {
            "data", "ai", "cloud", "analytics", "architect", "platform",
            "program manager", "product manager", "product owner",
            "engineering manager", "head of", "director", "lead",
            "databricks", "snowflake", "azure", "gcp", "aws",
            "llm", "machine learning", "governance", "delivery manager",
            "solutions", "datos", "arquitecto", "ia ", "inteligencia artificial",
        }
        emergency_candidates = []
        for j in jobs_with_titles:
            title_lower = (j["title"] + " " + j.get("company","")).lower()
            if any(kw in title_lower for kw in RELEVANT_KEYWORDS):
                emergency_candidates.append(j["id"])
        log.info(f"  [Agente 1 emergencia] {len(emergency_candidates)} candidatos de {len(jobs_with_titles)}")
        return emergency_candidates

    candidates = result.get("candidates", [])
    discarded  = result.get("discarded", [])
    log.info(f"  [Agente 1] Candidatos: {len(candidates)}, Descartados: {len(discarded)}")
    return [str(c) for c in candidates]

# ── AGENTE 2: ANÁLISIS PROFUNDO ─────────────────────────────────────────────
def agent2_deep_analysis(job: dict, profile: dict) -> dict | None:
    """
    Análisis completo de una oferta contra el perfil de Borja.
    Devuelve dict con classification, score, match_summary, gaps, etc.
    """
    profile_yaml = yaml.dump(
        {
            "summary":          profile.get("summary", ""),
            "technical_skills": profile.get("technical_skills", {}),
            "soft_skills":      profile.get("soft_skills", []),
            "ideal_role":       profile.get("ideal_role", {}),
            "compensable_gaps": profile.get("compensable_gaps", []),
            "disqualifiers":    profile.get("disqualifiers", []),
        },
        allow_unicode=True, default_flow_style=False
    )

    prompt = f"""
Eres un asesor de carrera experto y honesto. Analiza si esta oferta encaja con el perfil
de Borja. Sé riguroso: ni infles el match, ni lo subestimes.

═══════════ PERFIL DE BORJA ═══════════
{profile_yaml}

═══════════ OFERTA ════════════════════
Título:    {job.get('title', 'N/A')}
Empresa:   {job.get('company', 'N/A')}
URL:       {job.get('link', '')}
Descripción:
{job.get('description', '')[:3000]}

═══════════ CLASIFICACIÓN ═════════════
Elige UNA:
  VÁLIDA           → Encaja bien. Borja cumple los requisitos principales.
  VÁLIDA_CON_MATICES → Hay gap relevante pero compensable. Merece considerarlo.
  DESCARTAR        → No encaja. Gap demasiado grande o hay disqualifier claro.

Responde SOLO con JSON válido, sin texto adicional:
{{
  "classification": "VÁLIDA"|"VÁLIDA_CON_MATICES"|"DESCARTAR",
  "score": <entero 1-10>,
  "match_summary": "<1-2 frases muy concisas. Por qué encaja o no.>",
  "gaps": "<null si VÁLIDA o DESCARTAR. Si VÁLIDA_CON_MATICES: qué falta y cómo Borja lo compensa. Máx 2 frases.>",
  "discard_reason": "<null si no es DESCARTAR. Si DESCARTAR: razón en 1 frase.>"
}}
"""

    raw = call_gemini(prompt, temperature=0.2)
    result = parse_json_from_response(raw)
    if not result:
        log.warning(f"  [Agente 2] No se pudo parsear resultado para '{job.get('title')}'")
        return None
    return result

# ── GENERACIÓN DEL EMAIL HTML ───────────────────────────────────────────────
def build_email_html(valid: list, caveats: list, today: str) -> str:

    def card_valid(j, idx):
        score = j["analysis"].get("score", "?")
        return f"""
        <div style="border-left:4px solid #16a34a;padding:14px 18px;margin-bottom:14px;
                    background:#f0fdf4;border-radius:0 8px 8px 0;">
          <div style="margin-bottom:6px;">
            <span style="background:#16a34a;color:#fff;font-size:11px;font-weight:600;
                         padding:2px 8px;border-radius:20px;">#{idx} VÁLIDA</span>
            <span style="background:#dcfce7;color:#166534;font-size:11px;font-weight:600;
                         padding:2px 8px;border-radius:20px;margin-left:6px;">Score {score}/10</span>
          </div>
          <div style="font-size:16px;font-weight:600;color:#111;margin:4px 0 2px;">{j.get('title','')}</div>
          <div style="font-size:12px;color:#555;margin-bottom:8px;">{j.get('company','')} &bull; {j.get('location','Madrid')}</div>
          <div style="font-size:13px;color:#333;margin-bottom:10px;">{j['analysis'].get('match_summary','')}</div>
          <a href="{j.get('link','')}" style="display:inline-block;background:#16a34a;color:#fff;
             text-decoration:none;padding:6px 16px;border-radius:6px;font-size:12px;font-weight:600;">
             Aplicar →</a>
        </div>"""

    def card_caveat(j, idx):
        score = j["analysis"].get("score", "?")
        gaps  = j['analysis'].get('gaps') or ''
        return f"""
        <div style="border-left:4px solid #d97706;padding:14px 18px;margin-bottom:14px;
                    background:#fffbeb;border-radius:0 8px 8px 0;">
          <div style="margin-bottom:6px;">
            <span style="background:#d97706;color:#fff;font-size:11px;font-weight:600;
                         padding:2px 8px;border-radius:20px;">#{idx} CON MATICES</span>
            <span style="background:#fef3c7;color:#92400e;font-size:11px;font-weight:600;
                         padding:2px 8px;border-radius:20px;margin-left:6px;">Score {score}/10</span>
          </div>
          <div style="font-size:16px;font-weight:600;color:#111;margin:4px 0 2px;">{j.get('title','')}</div>
          <div style="font-size:12px;color:#555;margin-bottom:8px;">{j.get('company','')} &bull; {j.get('location','Madrid')}</div>
          <div style="font-size:13px;color:#333;margin-bottom:8px;">{j['analysis'].get('match_summary','')}</div>
          {f'<div style="font-size:12px;color:#92400e;background:#fef9c3;padding:8px 10px;border-radius:6px;margin-bottom:10px;"><strong>Matices:</strong> {gaps}</div>' if gaps else ''}
          <a href="{j.get('link','')}" style="display:inline-block;background:#d97706;color:#fff;
             text-decoration:none;padding:6px 16px;border-radius:6px;font-size:12px;font-weight:600;">
             Aplicar →</a>
        </div>"""

    valid_html  = "".join(card_valid(j, i+1)  for i, j in enumerate(valid))
    caveat_html = "".join(card_caveat(j, i+1) for i, j in enumerate(caveats))
    none_msg    = '<p style="color:#aaa;font-size:13px;margin:0;">Ninguna hoy.</p>'

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Arial,sans-serif;
             max-width:660px;margin:0 auto;padding:20px;background:#f9fafb;">
<div style="background:#fff;border-radius:12px;padding:28px;box-shadow:0 1px 4px rgba(0,0,0,.07);">

  <div style="border-bottom:1px solid #e5e7eb;padding-bottom:14px;margin-bottom:20px;">
    <div style="font-size:20px;font-weight:700;color:#111;">Informe diario de empleo</div>
    <div style="font-size:12px;color:#888;margin-top:3px;">{today} &bull; Madrid &bull; últimas 24h</div>
  </div>

  <div style="display:flex;gap:12px;margin-bottom:24px;">
    <div style="flex:1;background:#f0fdf4;border-radius:8px;padding:14px;text-align:center;">
      <div style="font-size:26px;font-weight:700;color:#16a34a;">{len(valid)}</div>
      <div style="font-size:11px;color:#555;margin-top:2px;">Válidas</div>
    </div>
    <div style="flex:1;background:#fffbeb;border-radius:8px;padding:14px;text-align:center;">
      <div style="font-size:26px;font-weight:700;color:#d97706;">{len(caveats)}</div>
      <div style="font-size:11px;color:#555;margin-top:2px;">Con matices</div>
    </div>
  </div>

  <h2 style="color:#166534;font-size:14px;margin:0 0 12px;border-bottom:2px solid #22c55e;padding-bottom:5px;">
    OFERTAS VÁLIDAS
  </h2>
  {valid_html or none_msg}

  <h2 style="color:#92400e;font-size:14px;margin:20px 0 12px;border-bottom:2px solid #f59e0b;padding-bottom:5px;">
    VÁLIDAS CON MATICES
  </h2>
  {caveat_html or none_msg}

  <div style="margin-top:24px;padding-top:14px;border-top:1px solid #e5e7eb;
              font-size:11px;color:#bbb;text-align:center;">
    Agente de empleo automatizado &bull; borja.jmota@gmail.com
  </div>
</div>
</body></html>"""

def send_email(html: str, today: str, n_valid: int, n_caveats: int):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Empleo] {n_valid} válidas, {n_caveats} con matices — {today}"
    msg["From"]    = GMAIL_USER
    msg["To"]      = EMAIL_TO
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_PASS)
        s.send_message(msg)
    log.info(f"Email enviado a {EMAIL_TO}")

def send_diagnostic_email(today: str, n_scraped: int, n_new: int, n_candidates: int):
    """Email de diagnóstico cuando no hay resultados — para confirmar que el bot vive."""
    msg = MIMEMultipart()
    msg["Subject"] = f"[Empleo Bot] Sin matches hoy — {today}"
    msg["From"]    = GMAIL_USER
    msg["To"]      = EMAIL_TO
    body = (
        f"Hola Borja,\n\n"
        f"El agente se ejecutó correctamente el {today}.\n\n"
        f"  Ofertas scrapeadas:        {n_scraped}\n"
        f"  Nuevas (no vistas antes):  {n_new}\n"
        f"  Pasan filtro de títulos:   {n_candidates}\n"
        f"  Resultado:                 Ninguna superó el análisis profundo.\n\n"
        f"Si recibes este correo, Gmail y Gemini funcionan correctamente."
    )
    msg.attach(MIMEText(body, "plain"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(GMAIL_USER, GMAIL_PASS)
        s.send_message(msg)
    log.info("Email de diagnóstico enviado.")

# ── MAIN ────────────────────────────────────────────────────────────────────
def main():
    today = datetime.now().strftime("%d/%m/%Y")
    log.info(f"=== AGENTE DOBLE — {today} ===")

    profile, seen_jobs, queue = load_data()

    # ── PASO 1: SCRAPING ──────────────────────────────────────────────────
    raw_jobs = scrape_job_ids()
    new_jobs = [j for j in raw_jobs if j["id"] not in seen_jobs]
    log.info(f"Nuevas (no vistas antes): {len(new_jobs)}")

    if not new_jobs:
        log.info("Sin ofertas nuevas hoy. Saliendo.")
        send_diagnostic_email(today, len(raw_jobs), 0, 0)
        return

    # ── PASO 2: CONSTRUIR LISTA DE TÍTULOS PARA AGENTE 1 ─────────────────
    # Los títulos ya vienen del scraper — no hace falta visitar cada URL.
    # Solo rellenamos con fallback los que lleguen sin título.
    log.info("Preparando títulos para Agente 1 (sin peticiones extra)...")
    jobs_with_titles = []
    for job in new_jobs:
        if not job.get("title"):
            job["title"]   = f"Oferta {job['id']}"
            job["company"] = "Empresa desconocida"
        jobs_with_titles.append({
            "id":      job["id"],
            "title":   job["title"],
            "company": job.get("company", ""),
        })

    # Dict por id para acceso rápido en pasos posteriores
    jobs_by_id = {j["id"]: j for j in new_jobs}

    # ── PASO 3: AGENTE 1 — FILTRO DE TÍTULOS (batch, 1 llamada) ──────────
    candidate_ids = agent1_filter_titles(jobs_with_titles, profile)
    log.info(f"Candidatos tras Agente 1: {len(candidate_ids)} de {len(new_jobs)}")

    # Hard cap: máximo 25 candidatos para el Agente 2
    # Garantiza que el tiempo total sea < 15 min incluso con retries
    MAX_AGENT2_CANDIDATES = 25
    if len(candidate_ids) > MAX_AGENT2_CANDIDATES:
        log.warning(f"  Limitando a {MAX_AGENT2_CANDIDATES} candidatos (había {len(candidate_ids)})")
        candidate_ids = candidate_ids[:MAX_AGENT2_CANDIDATES]

    # ── PASO 4: AGENTE 2 — ANÁLISIS PROFUNDO (1 llamada por candidato) ────
    valid    = []
    caveats  = []

    candidate_ids = list(dict.fromkeys(candidate_ids))  # elimina duplicados manteniendo orden
    for jid in candidate_ids:
        job = jobs_by_id.get(jid)
        if not job:
            continue

        # Si la descripción aún está vacía (falló extracción en paso 2), reintentamos
        if not job.get("description"):
            data = get_job_page(job["link"])
            job.update(data)

        log.info(f"  [Agente 2] Analizando: {job.get('title','?')} @ {job.get('company','?')}")
        analysis = agent2_deep_analysis(job, profile)

        if analysis:
            job["analysis"] = analysis
            clf = analysis.get("classification", "DESCARTAR")
            score = analysis.get("score", 0)

            if clf == "VÁLIDA":
                valid.append(job)
                log.info(f"    → VÁLIDA (score {score})")
            elif clf == "VÁLIDA_CON_MATICES":
                caveats.append(job)
                log.info(f"    → MATICES (score {score})")
            else:
                log.info(f"    → DESCARTADA")

            # Añadir a la cola de aplicación
            queue.append({
                "date":           datetime.now().strftime("%Y-%m-%d"),
                "applied":        False,
                "classification": clf,
                "score":          score,
                "title":          job.get("title", ""),
                "company":        job.get("company", ""),
                "link":           job.get("link", ""),
                "match_summary":  analysis.get("match_summary", ""),
            })

        # Marcar como vista siempre, independientemente del resultado
        seen_jobs.add(jid)

        time.sleep(15)   # 15s entre llamadas = ~4 req/min, bien por debajo del RPM limit

    # Ordenar cola por score desc
    queue.sort(key=lambda x: x.get("score", 0), reverse=True)
    valid.sort(key=lambda j: j["analysis"].get("score", 0), reverse=True)
    caveats.sort(key=lambda j: j["analysis"].get("score", 0), reverse=True)

    # ── PASO 5: PERSISTENCIA ──────────────────────────────────────────────
    SEEN_PATH.write_text("\n".join(sorted(seen_jobs)))
    QUEUE_PATH.write_text(json.dumps(queue, indent=2, ensure_ascii=False))
    log.info("Estado guardado.")

    # ── PASO 6: EMAIL ─────────────────────────────────────────────────────
    if valid or caveats:
        html = build_email_html(valid, caveats, today)
        send_email(html, today, len(valid), len(caveats))
    else:
        send_diagnostic_email(today, len(raw_jobs), len(new_jobs), len(candidate_ids))

    log.info(f"=== FIN — Válidas: {len(valid)}, Matices: {len(caveats)} ===")

if __name__ == "__main__":
    main()
