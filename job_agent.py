import os
import requests
import re
import time
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google import genai
from google.genai import types

# --- CONFIGURACIÓN DE LOGS ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s: %(message)s')
log = logging.getLogger(__name__)

# --- VARIABLES DE ENTORNO ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
EMAIL_SENDER = os.environ.get("EMAIL_SENDER")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.environ.get("EMAIL_RECEIVER")

# Queries de búsqueda estratégica
SEARCH_QUERIES = [
    "Data AI Cloud", 
    "Solutions Architect Cloud", 
    "Inteligencia Artificial Machine Learning",
    "Azure Cloud Engineer"
]

def get_stealth_session():
    """Crea una sesión con headers que imitan a un iPhone real en 2026."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "es-ES,es;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Referer": "https://www.google.com/"
    })
    return session

def scrape_linkedin_jobs():
    """Scraping mediante API de invitados con bypass de headers."""
    all_jobs = []
    session = get_stealth_session()

    for query in SEARCH_QUERIES:
        log.info(f"🔍 Buscando: {query}...")
        # Usamos la URL de 'ver más' que LinkedIn usa para cargar contenido dinámico
        # Es mucho menos estricta que la página de búsqueda principal
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={query.replace(' ', '%20')}&location=Madrid%2C%20España&f_TPR=r604800&start=0"
        
        try:
            response = session.get(url, timeout=20)
            
            if response.status_code != 200:
                log.warning(f"⚠️ LinkedIn respondió con status {response.status_code} para {query}")
                continue

            # La 'llave' para vulnerar el 0: una regex que busca cualquier patrón de ID de oferta
            job_ids = re.findall(r'data-id=["\'](\d+)["\']', response.text)
            
            # Si no hay IDs por data-id, probamos por la URL de la card
            if not job_ids:
                job_ids = re.findall(r'jobListing:(\d+)', response.text)

            unique_ids = list(set(job_ids))
            log.info(f"✅ Encontradas {len(unique_ids)} ofertas para '{query}'")

            for j_id in unique_ids:
                all_jobs.append({
                    'id': j_id,
                    'link': f"https://www.linkedin.com/jobs/view/{j_id}/"
                })
            
            # Pausa aleatoria para no parecer un script
            time.sleep(3) 

        except Exception as e:
            log.error(f"❌ Error en query {query}: {e}")

    return all_jobs

def analyze_job_with_ia(job_link):
    """Analiza la relevancia de la oferta usando Gemini 1.5 Flash."""
    if not GEMINI_API_KEY:
        return {"relevant": False}

    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = (
        f"Eres un experto en reclutamiento IT. Analiza este link de LinkedIn: {job_link}\n"
        "Determina si es una posición de alto nivel para perfiles Cloud, AI o Data.\n"
        "Responde ESTRICTAMENTE en JSON con este formato:\n"
        '{"relevant": true/false, "score": 0-100, "reason": "breve explicación"}'
    )

    try:
        response = client.models.generate_content(model="gemini-1.5-flash", contents=prompt)
        # Limpieza por si Gemini añade markdown
        clean_json = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        log.error(f"🤖 Error IA: {e}")
        return {"relevant": False}

def send_email(matches):
    """Envía el reporte diario por email."""
    if not matches: return

    msg = MIMEMultipart()
    msg['Subject'] = f"🚀 {len(matches)} Nuevas Ofertas Cloud/AI Madrid"
    msg['From'] = EMAIL_SENDER
    msg['To'] = EMAIL_RECEIVER

    body = "He encontrado estas ofertas interesantes hoy:\n\n"
    for m in matches:
        body += f"📌 Link: {m['link']}\n"
        body += f"📊 Score IA: {m['score']}/100\n"
        body += f"💡 Por qué: {m['reason']}\n"
        body += "-"*30 + "\n"

    msg.attach(MIMEText(body, 'plain'))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)
        log.info("📧 Email enviado correctamente.")
    except Exception as e:
        log.error(f"📧 Error enviando email: {e}")

def main():
    log.info("=== ⚡ ARRANCANDO AGENTE SIGILOSO 2026 ⚡ ===")
    
    # 1. Scraping
    raw_jobs = scrape_linkedin_jobs()
    if not raw_jobs:
        log.info("Fin del ciclo: No se encontraron ofertas nuevas.")
        return

    # 2. Gestión de duplicados (Seen Jobs)
    seen_file = "seen_jobs.txt"
    seen_ids = set()
    if os.path.exists(seen_file):
        with open(seen_file, "r") as f:
            seen_ids = {line.strip() for line in f}

    new_jobs = [j for j in raw_jobs if j['id'] not in seen_ids]
    log.info(f"Filtradas: {len(new_jobs)} ofertas por analizar.")

    # 3. Análisis y Filtro
    matches = []
    for job in new_jobs[:10]: # Máximo 10 por ejecución para ahorrar API
        log.info(f"Analizando: {job['id']}...")
        result = analyze_job_with_ia(job['link'])
        
        if result.get("relevant") and result.get("score", 0) > 70:
            job.update(result)
            matches.append(job)
        
        # Guardar como visto inmediatamente
        with open(seen_file, "a") as f:
            f.write(f"{job['id']}\n")
        
        time.sleep(1) # Delay para la API de Gemini

    # 4. Notificar
    if matches:
        send_email(matches)
    else:
        log.info("No hubo ofertas con puntuación suficiente hoy.")

if __name__ == "__main__":
    main()
