#!/usr/bin/env python3
import os
import json
import re
import smtplib
import yaml
import logging
import time
import random
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import requests
from google import genai

# --- CONFIGURACIÓN ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR        = Path(__file__).parent
PROFILE_PATH    = BASE_DIR / "profile.yaml"
SEEN_JOBS_PATH  = BASE_DIR / "seen_jobs.txt"
QUEUE_PATH      = BASE_DIR / "application_queue.json"

GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY")
GMAIL_USER      = os.environ.get("GMAIL_USER")
GMAIL_APP_PASS  = os.environ.get("GMAIL_APP_PASSWORD")
EMAIL_TO        = os.environ.get("EMAIL_TO", GMAIL_USER)

SEARCH_QUERIES = [
    "Data AI Cloud", "Solutions Architect Cloud", "Inteligencia Artificial",
    "Head of Data Engineering", "Program Manager Data"
]

# --- CARGA DE DATOS ---
def load_profile():
    with open(PROFILE_PATH, "r", encoding="utf-8") as f: return yaml.safe_load(f)

def load_seen_jobs():
    if SEEN_JOBS_PATH.exists(): return set(SEEN_JOBS_PATH.read_text().splitlines())
    return set()

def save_seen_jobs(seen):
    SEEN_JOBS_PATH.write_text("\n".join(sorted(seen)))

def load_queue():
    if QUEUE_PATH.exists(): return json.loads(QUEUE_PATH.read_text())
    return []

def save_queue(queue):
    QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2))

# --- SCRAPER (EL CORAZÓN DEL BYPASS) ---
def scrape_linkedin_jobs():
    jobs_found = []
    # Usamos la API de "ver más" de invitados, que es la más fiable sin login
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    for query in SEARCH_QUERIES:
        log.info(f"🔎 Buscando: {query}...")
        # Esta URL es la clave: carga los resultados dinámicos
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={query.replace(' ', '%20')}&location=Madrid%2C%20España&f_TPR=r604800&start=0"
        
        try:
            res = requests.get(url, headers=headers, timeout=15)
            # Buscamos IDs de ofertas en cualquier formato posible
            ids = re.findall(r'jobPosting:(\d+)', res.text)
            if not ids:
                ids = re.findall(r'data-id=["\'](\d+)["\']', res.text)
            
            unique_ids = list(set(ids))
            log.info(f"   ✅ Encontrados {len(unique_ids)} IDs.")

            for j_id in unique_ids:
                jobs_found.append({
                    "job_id": j_id,
                    "link": f"https://www.linkedin.com/jobs/view/{j_id}/"
                })
            time.sleep(random.uniform(2, 4))
        except Exception as e:
            log.error(f"Error en query {query}: {e}")
            
    return jobs_found

# --- ANÁLISIS IA ---
def analyze_job_with_ia(profile, job_link):
    if not GEMINI_API_KEY: return None
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # Le pedimos que él mismo averigüe el título y la empresa entrando al link
    prompt = f"""
    Analiza esta oferta de LinkedIn: {job_link}
    Basándote en este perfil profesional: {yaml.dump(profile, allow_unicode=True)}
    
    Responde ESTRICTAMENTE en JSON:
    {{
      "classification": "VÁLIDA" o "VÁLIDA_CON_MATICES" o "DESCARTAR",
      "score": 0-10,
      "real_title": "Título del puesto",
      "company": "Nombre empresa",
      "match_summary": "Por qué encaja (1 frase)",
      "gaps": "Qué le falta (si aplica)"
    }}
    """
    try:
        response = client.models.generate_content(model="gemini-2.0-flash", contents=prompt)
        clean_json = re.search(r'\{.*\}', response.text, re.DOTALL).group()
        return json.loads(clean_json)
    except Exception:
        return None

# --- EMAIL HTML (TU DISEÑO) ---
def build_email_html(valid, with_caveats, date_str):
    def card(j, is_caveat=False):
        color = "#f59e0b" if is_caveat else "#22c55e"
        return f"""
        <div style="border-left:4px solid {color}; padding:10px; margin-bottom:10px; background:#fff;">
            <strong>{j['title']}</strong> - {j['company']}<br>
            <small>Score: {j['analysis']['score']}/10 - {j['analysis']['match_summary']}</small><br>
            <a href="{j['link']}">Ver Oferta</a>
        </div>"""

    v_html = "".join(card(j) for j in valid)
    c_html
