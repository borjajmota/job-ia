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
    "Solutions Architect Cloud", "Data Strategy Director", 
    "Head of Data Engineering", "Program Manager AI"
]

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

# --- MOTOR DE SCRAPING REFORZADO ---
def scrape_linkedin_jobs():
    jobs_found = []
    # Simulamos un navegador real de 2026
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Referer": "https://www.google.com/", # CRÍTICO: LinkedIn confía más si vienes de Google
        "Upgrade-Insecure-Requests": "1"
    }

    for query in SEARCH_QUERIES:
        log.info(f"🔎 Atacando query: {query}")
        # URL de la API de carga dinámica (Guest API)
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={query.replace(' ', '%20')}&location=Madrid&f_TPR=r86400&start=0"
        
        try:
            res = requests.get(url, headers=headers, timeout=15)
            log.info(f"   HTTP Status: {res.status_code}")
            
            # Buscamos IDs usando 3 patrones distintos de 2026
            ids = re.findall(r'jobPosting:(\d+)', res.text)
            if not ids:
                ids = re.findall(r'data-id=["\'](\d+)["\']', res.text)
            if not ids:
                ids = re.findall(r'view/(\d+)', res.text)

            unique_ids = list(set(ids))
            log.info(f"   🎯 IDs capturados: {len(unique_ids)}")

            for j_id in unique_ids:
                jobs_found.append({
                    "job_id": j_id,
                    "link": f"https://www.linkedin.com/jobs/view/{j_id}/"
                })
            
            time.sleep(random.uniform(4, 7)) # Pausa para no quemar la IP
        except Exception as e:
            log.error(f"   ❌ Error en query: {e}")
            
    return jobs_found

# --- ANÁLISIS IA (GEMINI 2.0 FLASH) ---
def analyze_job_with_ia(profile, job_link):
    if not GEMINI_API_KEY: return None
    client = genai.Client(api_key=GEMINI_API_KEY)
    
    # Reducimos el perfil para enviar menos datos
    summary = profile.get('summary', '')[:500]
    
    prompt = f"Analiza match entre este perfil: {summary} y esta oferta: {job_link}. Responde JSON con campos: classification, score, real_title, company, match_summary."

    for attempt in range(3):
        try:
            # USAMOS 1.5 FLASH (MÁS ESTABLE EN TIER GRATUITO)
            response = client.models.generate_content(model="gemini-1.5-flash", contents=prompt)
            clean_json = re.search(r'\{.*\}', response.text, re.DOTALL).group()
            return json.loads(clean_json)
        except Exception as e:
            if "429" in str(e):
                log.warning(f"⚠️ Esperando 60s por cuota de Google...")
                time.sleep(60) # Espera agresiva
            else:
                return None
    return None

# --- EMAIL (CONSERVANDO TU ESTILO) ---
def build_email_html(valid, caveats, date):
    # Genera una lista simple para el reporte
    def row(j):
        return f"<li><b>{j['title']}</b> ({j['company']}) - Score: {j['analysis']['score']}/10<br><a href='{j['link']}'>Link</a></li>"
    
    v_html = "".join([row(j) for j in valid])
    c_html = "".join([row(j) for j in caveats])
    
    return f"""
    <html><body>
    <h2>🚀 Informe de Empleo - {date}</h2>
    <h3>✅ Válidas ({len(valid)})</h3><ul>{v_html}</ul>
    <h3>⚠️ Con Matices ({len(caveats)})</h3><ul>{c_html}</ul>
    </body></html>
    """

def main():
    today = datetime.now().strftime("%d/%m/%Y")
    log.info(f"=== INICIO AGENTE SIGILOSO {today} ===")
    
    profile = load_profile()
    seen_jobs = load_seen_jobs()
    queue = load_queue()
    
    raw_jobs = scrape_linkedin_jobs()
    new_jobs = [j for j in raw_jobs if j["job_id"] not in seen_jobs]
    
    log.info(f"Total nuevas para analizar: {len(new_jobs)}")

    valid_to_send, caveats_to_send = [], []

    for job in new_jobs[:12]: # Máximo 12 para no saturar
        log.info(f"   🤖 IA analizando: {job['job_id']}")
        res = analyze_job_with_ia(profile, job['link'])
        
        if res and res['classification'] != "DESCARTAR":
            job.update({"title": res['real_title'], "company": res['company'], "analysis": res})
            if res['classification'] == "VÁLIDA":
                valid_to_send.append(job)
            else:
                caveats_to_send.append(job)
            
            queue.append({
                "date": today, "applied": False, "score": res['score'],
                "title": res['real_title'], "company": res['company'], "link": job['link']
            })
        
        seen_jobs.add(job["job_id"])
        time.sleep(10)

    save_seen_jobs(seen_jobs)
    save_queue(queue)

    if valid_to_send or caveats_to_send:
        html = build_email_html(valid_to_send, caveats_to_send, today)
        
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🎯 {len(valid_to_send)} Ofertas Filtradas - {today}"
        msg["From"] = GMAIL_USER
        msg["To"] = EMAIL_TO
        msg.attach(MIMEText(html, "html"))
        
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_USER, GMAIL_APP_PASS)
            server.send_message(msg)
        log.info("📧 Email enviado con éxito.")
    else:
        log.info("Nada relevante hoy.")

if __name__ == "__main__":
    main()
