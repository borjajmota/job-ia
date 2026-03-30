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
import google.generativeai as genai

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

SEARCH_QUERIES = ["Solutions Architect Cloud", "Data Strategy Director", "Head of Data Engineering", "Program Manager AI"]

# --- GESTIÓN DE ARCHIVOS ---
def load_profile():
    with open(PROFILE_PATH, "r", encoding="utf-8") as f: return yaml.safe_load(f)

def load_seen_jobs():
    if SEEN_JOBS_PATH.exists(): return set(SEEN_JOBS_PATH.read_text().splitlines())
    return set()

def save_seen_jobs(seen):
    SEEN_JOBS_PATH.write_text("\n".join(sorted(list(seen))))

def load_queue():
    if QUEUE_PATH.exists():
        try: return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        except: return []
    return []

def save_queue(queue):
    QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")

# --- SCRAPER ---
def scrape_linkedin_jobs():
    jobs_found = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Referer": "https://www.google.com/",
    }
    for query in SEARCH_QUERIES:
        log.info(f"🔎 Buscando: {query}")
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={query.replace(' ', '%20')}&location=Madrid&f_TPR=r86400&start=0"
        try:
            res = requests.get(url, headers=headers, timeout=15)
            ids = re.findall(r'jobPosting:(\d+)', res.text) or re.findall(r'data-id=["\'](\d+)["\']', res.text)
            for j_id in set(ids):
                jobs_found.append({"job_id": j_id, "link": f"https://www.linkedin.com/jobs/view/{j_id}/"})
            time.sleep(random.uniform(3, 5))
        except: pass
    return jobs_found

# --- NÚCLEO IA (AUTO-DETECT MODEL) ---
def analyze_job_with_ia(profile, job_link):
    if not GEMINI_API_KEY: return None
    genai.configure(api_key=GEMINI_API_KEY)
    
    # Probamos los 3 nombres posibles en 2026 para evitar el 404
    model_names = ['gemini-1.5-flash', 'gemini-2.0-flash', 'gemini-pro']
    
    resumen = profile.get('summary', '')[:400]
    prompt = f"Analiza match entre perfil: {resumen} y oferta: {job_link}. Responde SOLO JSON: {{\"classification\":\"VÁLIDA\",\"score\":9,\"real_title\":\"...\",\"company\":\"...\",\"match_summary\":\"...\"}}"

    for m_name in model_names:
        try:
            model = genai.GenerativeModel(m_name)
            response = model.generate_content(prompt)
            if response and response.text:
                match = re.search(r'\{.*\}', response.text, re.DOTALL)
                if match: return json.loads(match.group())
        except Exception as e:
            if "404" in str(e):
                log.warning(f"⚠️ Modelo {m_name} no disponible, probando el siguiente...")
                continue
            if "429" in str(e):
                log.warning("⏳ Cuota excedida, esperando 60s...")
                time.sleep(60)
                return None
    return None

# --- EMAIL ---
def build_email_html(valid, caveats, date_str):
    def row(j):
        c = "#22c55e" if j['analysis']['classification'] == "VÁLIDA" else "#f59e0b"
        return f'<div style="border-left:4px solid {c};padding:10px;margin-bottom:10px;background:#f9fafb;"><b>{j["title"]}</b> ({j["company"]})<br><small>{j["analysis"]["match_summary"]}</small><br><a href="{j["link"]}">Link</a></div>'
    
    return f"<h2>Informe {date_str}</h2>" + "".join([row(j) for j in valid + caveats])

def main():
    today = datetime.now().strftime("%d/%m/%Y")
    log.info(f"=== INICIO AGENTE {today} ===")
    profile, seen_jobs, queue = load_profile(), load_seen_jobs(), load_queue()
    
    raw_jobs = scrape_linkedin_jobs()
    # Filtramos por los que NO están en visto
    new_jobs = [j for j in raw_jobs if j["job_id"] not in seen_jobs]
    log.info(f"Nuevas para analizar: {len(new_jobs)}")

    valid_to_send, caveats_to_send = [], []
    for job in new_jobs[:10]:
        log.info(f"   🤖 IA analizando: {job['job_id']}")
        res = analyze_job_with_ia(profile, job['link'])
        
        if res and res.get('classification') != "DESCARTAR":
            job.update({"title": res.get('real_title', 'Job'), "company": res.get('company', 'Company'), "analysis": res})
            if res['classification'] == "VÁLIDA": valid_to_send.append(job)
            else: caveats_to_send.append(job)
            
            queue.append({"date": today, "applied": False, "score": res.get('score', 0), "title": job['title'], "company": job['company'], "link": job['link']})
        
        seen_jobs.add(job["job_id"])
        time.sleep(12) # Pausa para evitar 429

    save_seen_jobs(seen_jobs)
    save_queue(queue)

    if (valid_to_send or caveats_to_send) and GMAIL_USER:
        html = build_email_html(valid_to_send, caveats_to_send, today)
        msg = MIMEMultipart("alternative")
        msg["Subject"], msg["From"], msg["To"] = f"🎯 Ofertas {today}", GMAIL_USER, EMAIL_TO
        msg.attach(MIMEText(html, "html"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASS)
            s.send_message(msg)
        log.info("📧 Email enviado.")

if __name__ == "__main__":
    main()
