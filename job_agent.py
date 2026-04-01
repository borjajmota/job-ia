#!/usr/bin/env python3
"""
job_agent.py — Agente diario de búsqueda de empleo
Pipeline: Scraping → Agente 1 (filtro batch) → Agente 2 (análisis profundo) → Email + Logs
"""
import os, json, re, smtplib, yaml, logging, time, requests, random
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

LINKEDIN_GEO_ID   = "100958104" 
LINKEDIN_LOCATION = "Madrid%2C%20Spain"

# ── CARGA DE DATOS ────────────────────────────────────────────────────────────
def load_data():
    profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    seen    = set(SEEN_PATH.read_text().splitlines()) if SEEN_PATH.exists() else set()
    queue   = json.loads(QUEUE_PATH.read_text()) if QUEUE_PATH.exists() else []
    return profile, seen, queue

# ── SCRAPING ──────────────────────────────────────────────────────────────────
def scrape_job_ids() -> dict:
    jobs = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "es-ES,es;q=0.9",
        "Referer": "https://www.google.es/",
    }

    for q in SEARCH_QUERIES:
        log.info(f"🔎 Capturando IDs para [{q}]...")
        url = (
            f"https://es.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
            f"?keywords={q.replace(' ','%20')}"
            f"&location={LINKEDIN_LOCATION}"
            f"&geoId={LINKEDIN_GEO_ID}"
            f"&f_TPR=r86400"
        )
        try:
            res = requests.get(url, headers=headers, timeout=15)
            html = res.text
            ids = re.findall(r'jobPosting:(\d+)', html) or re.findall(r'data-id=["\'](\d+)["\']', html)
            titles = re.findall(r'class="[^"]*base-search-card__title[^"]*"[^>]*>(.*?)</h3', html, re.S)
            companies = re.findall(r'class="[^"]*base-search-card__subtitle[^"]*"[^>]*>.*?<a[^>]*>(.*?)</a', html, re.S)

            for i, jid in enumerate(ids):
                if jid not in jobs:
                    t = re.sub(r'<[^<]+?>', '', titles[i]).strip() if i < len(titles) else "Título no extraído"
                    c = re.sub(r'<[^<]+?>', '', companies[i]).strip() if i < len(companies) else "Empresa no extraída"
                    jobs[jid] = {
                        "id": jid,
                        "title": t,
                        "company": c,
                        "link": f"https://www.linkedin.com/jobs/view/{jid}/"
                    }
            log.info(f"   ✅ {len(ids)} encontrados")
            time.sleep(random.uniform(1, 3))
        except Exception as e:
            log.error(f"  ❌ Error en [{q}]: {e}")
    return jobs

# ── GROQ HELPER ───────────────────────────────────────────────────────────────
def call_groq(prompt: str, temperature: float = 0.1, max_retries: int = 3) -> str:
    client = Groq(api_key=GROQ_API_KEY)
    for attempt in range(max_retries):
        try:
            resp = client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=2000,
                timeout=30
            )
            return resp.choices[0].message.content.strip()
        except Exception as e:
            err = str(e)
            if "429" in err or "rate_limit" in err.lower():
                m = re.search(r'retry after (\d+(?:\.\d+)?)', err, re.I)
                wait = float(m.group(1)) + 2 if m else 65
                log.warning(f"  Rate limit: esperando {wait:.0f}s")
                time.sleep(wait)
            else:
                log.error(f"  Groq error: {e}")
                return ""
    return ""

def parse_json(text: str):
    text = re.sub(r'^
