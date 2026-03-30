#!/usr/bin/env python3
"""
job_agent.py - Versión Estable 2026
Agente de búsqueda de empleo para Borja Jiménez Mota.
Bypass de LinkedIn + Motor Gemini 1.5 Flash (Tier Gratuito)
"""

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

# ──────────────────────────────────────────────
# CONFIGURACIÓN Y RUTAS
# ──────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR        = Path(__file__).parent
PROFILE_PATH    = BASE_DIR / "profile.yaml"
SEEN_JOBS_PATH  = BASE_DIR / "seen_jobs.txt"
QUEUE_PATH      = BASE_DIR / "application_queue.json"

# Credenciales desde GitHub Secrets
GEMINI_API_KEY  = os.environ.get("GEMINI_API_KEY")
GMAIL_USER      = os.environ.get("GMAIL_USER")
GMAIL_APP_PASS  = os.environ.get("GMAIL_APP_PASSWORD")
EMAIL_TO        = os.environ.get("EMAIL_TO", GMAIL_USER)

# Búsquedas optimizadas para tu perfil
SEARCH_QUERIES = [
    "Solutions Architect Cloud", 
    "Data Strategy Director", 
    "Head of Data Engineering", 
    "Program Manager AI"
]

# ──────────────────────────────────────────────
# GESTIÓN DE DATOS
# ──────────────────────────────────────────────

def load_profile():
    with open(PROFILE_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def load_seen_jobs():
    if SEEN_JOBS_PATH.exists():
        return set(SEEN_JOBS_PATH.read_text().splitlines())
    return set()

def save_seen_jobs(seen):
    SEEN_JOBS_PATH.write_text("\n".join(sorted(list(seen))))

def load_queue():
    if QUEUE_PATH.exists():
        try:
            return json.loads(QUEUE_PATH.read_text(encoding="utf-8"))
        except: return []
    return []

def save_queue(queue):
    QUEUE_PATH.write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")

# ──────────────────────────────────────────────
# MOTOR DE SCRAPING (GUEST API BYPASS)
# ──────────────────────────────────────────────

def scrape_linkedin_jobs():
    jobs_found = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9",
        "Referer": "https://www.google.com/",
    }

    for query in SEARCH_QUERIES:
        log.info(f"🔎 Buscando en LinkedIn: {query}")
        # Endpoint dinámico para evitar el muro de login
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={query.replace(' ', '%20')}&location=Madrid&f_TPR=r86400&start=0"
        
        try:
            res = requests.get(url, headers=headers, timeout=15)
            if res.status_code == 200:
                # Extraer IDs de ofertas mediante Regex
                ids = re.findall(r'jobPosting:(\d+)', res.text)
                if not ids:
                    ids = re.findall(r'data-id=["\'](\d+)["\']', res.text)
                
                unique_ids = list(set(ids))
                log.info(f"   ✅ Encontrados {len(unique_ids)} IDs potenciales.")

                for j_id in unique_ids:
                    jobs_found.append({
                        "job_id": j_id,
                        "link": f"https://www.linkedin.com/jobs/view/{j_id}/"
                    })
            else:
                log.warning(f"   ⚠️ Status {res.status_code} para {query}")
            
            time.sleep(random.uniform(4, 6))
        except Exception as e:
            log.error(f"   ❌ Error en scraping: {e}")
            
    return jobs_found

# ──────────────────────────────────────────────
# ANÁLISIS IA (GEMINI 1.5 FLASH)
# ──────────────────────────────────────────────

def analyze_job_with_ia(profile, job_link):
    if not GEMINI_API_KEY:
        log.error("Falta GEMINI_API_KEY")
        return None
    
    genai.configure(api_key=GEMINI_API_KEY)
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    resumen_perfil = profile.get('summary', '')[:500]
    prompt = (
        f"Analiza si este empleo {job_link} encaja con este perfil: {resumen_perfil}. "
        "Responde ESTRICTAMENTE con un JSON con estos campos: "
        '{"classification": "VÁLIDA" o "VÁLIDA_CON_MATICES" o "DESCARTAR", '
        '"score": 1-10, "real_title": "título", "company": "empresa", "match_summary": "resumen"}'
    )

    for attempt in range(3):
        try:
            response = model.generate_content(prompt)
            match = re.search(r'\{.*\}', response.text, re.DOTALL)
            if match:
                return json.loads(match.group())
        except Exception as e:
            if "429" in str(e):
                wait = 65 * (attempt + 1) # Espera superior al minuto para resetear cuota
                log.warning(f"⏳ Límite de cuota IA. Esperando {wait}s...")
                time.sleep(wait)
            else:
                log.error(f"❌ Error en Gemini: {e}")
                break
    return None

# ──────────────────────────────────────────────
# EMAIL Y REPORTE
# ──────────────────────────────────────────────

def build_email_html(valid, caveats, date_str):
    def job_row(j):
        color = "#22c55e" if j['analysis']['classification'] == "VÁLIDA" else "#f59e0b"
        return f"""
        <div style="border-left:4px solid {color}; padding:12px; margin-bottom:15px; background:#f9fafb;">
            <strong style="font-size:16px;">{j['title']}</strong><br>
            <span style="color:#666;">{j['company']}</span> | <b>Score: {j['analysis']['score']}/10</b><br>
            <p style="margin:5px 0; font-size:14px;">{j['analysis']['match_summary']}</p>
            <a href="{j['link']}" style="color:#2563eb; font-weight:bold;">Ver Oferta →</a>
        </div>"""

    v_html = "".join([job_row(j) for j in valid])
    c_html = "".join([job_row(j) for j in caveats])

    return f"""
    <html>
    <body style="font-family:sans-serif; color:#333;">
        <h2 style="color:#1e3a8a;">🚀 Informe de Empleo - {date_str}</h2>
        <h3 style="color:#166534;">✅ Ofertas Válidas ({len(valid)})</h3>
        {v_html if v_html else '<p>No hay matches directos hoy.</p>'}
        <h3 style="color:#92400e;">⚠️ Con Matices ({len(caveats)})</h3>
        {c_html if c_html else '<p>No hay ofertas con matices.</p>'}
    </body>
    </html>"""

# ──────────────────────────────────────────────
# EJECUCIÓN PRINCIPAL
# ──────────────────────────────────────────────

def main():
    today = datetime.now().strftime("%d/%m/%Y")
    log.info(f"=== INICIO AGENTE {today} ===")
    
    profile = load_profile()
    seen_jobs = load_seen_jobs()
    queue = load_queue()
    
    # 1. Scraping
    raw_jobs = scrape_linkedin_jobs()
    new_jobs = [j for j in raw_jobs if j["job_id"] not in seen_jobs]
    log.info(f"Total nuevas para analizar: {len(new_jobs)}")

    valid_to_send, caveats_to_send = [], []

    # 2. Análisis con IA (máximo 12 para no quemar la API gratuita)
    for job in new_jobs[:12]:
        log.info(f"   🤖 IA analizando: {job['job_id']}")
        res = analyze_job_with_ia(profile, job['link'])
        
        if res and res.get('classification') != "DESCARTAR":
            job.update({
                "title": res.get('real_title', 'Puesto sin título'),
                "company": res.get('company', 'Empresa desconocida'),
                "analysis": res
            })
            
            if res['classification'] == "VÁLIDA":
                valid_to_send.append(job)
            else:
                caveats_to_send.append(job)
            
            # Añadir a la cola de aplicación
            queue.append({
                "date_found": today,
                "applied": False,
                "score": res.get('score', 0),
                "title": job['title'],
                "company": job['company'],
                "link": job['link'],
                "summary": res.get('match_summary', '')
            })
        
        seen_jobs.add(job["job_id"])
        # Pausa obligatoria de 15s para no saturar el RPM gratuito de Gemini
        time.sleep(15)

    # 3. Guardar estado
    save_seen_jobs(seen_jobs)
    save_queue(queue)

    # 4. Enviar Email
    if valid_to_send or caveats_to_send:
        html = build_email_html(valid_to_send, caveats_to_send, today)
        
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🎯 {len(valid_to_send)} Ofertas Nuevas - {today}"
        msg["From"] = GMAIL_USER
        msg["To"] = EMAIL_TO
        msg.attach(MIMEText(html, "html"))
        
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(GMAIL_USER, GMAIL_APP_PASS)
                server.send_message(msg)
            log.info("📧 Email enviado con éxito.")
        except Exception as e:
            log.error(f"❌ Fallo al enviar email: {e}")
    else:
        log.info("Nada relevante que reportar hoy.")

if __name__ == "__main__":
    main()
