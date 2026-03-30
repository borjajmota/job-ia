#!/usr/bin/env python3
import os, json, re, smtplib, yaml, logging, time, random, requests
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
import google.generativeai as genai

# --- CONFIG ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
PROFILE_PATH, SEEN_PATH, QUEUE_PATH = BASE_DIR/"profile.yaml", BASE_DIR/"seen_jobs.txt", BASE_DIR/"application_queue.json"

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
GMAIL_USER, GMAIL_PASS, EMAIL_TO = os.environ.get("GMAIL_USER"), os.environ.get("GMAIL_APP_PASSWORD"), os.environ.get("EMAIL_TO")

SEARCH_QUERIES = ["Solutions Architect Cloud", "Data Strategy Director", "Head of Data Engineering", "Program Manager AI"]

def load_data():
    profile = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    seen = set(SEEN_PATH.read_text().splitlines()) if SEEN_PATH.exists() else set()
    queue = json.loads(QUEUE_PATH.read_text()) if QUEUE_PATH.exists() else []
    return profile, seen, queue

def scrape_ids():
    jobs = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Referer": "https://www.google.com/"}
    for q in SEARCH_QUERIES:
        log.info(f"🔎 Buscando: {q}")
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={q.replace(' ','%20')}&location=Madrid&start=0"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            ids = re.findall(r'jobPosting:(\d+)', res.text) or re.findall(r'data-id=["\'](\d+)["\']', res.text)
            for j_id in set(ids): jobs.append({"id": j_id, "link": f"https://www.linkedin.com/jobs/view/{j_id}/"})
            time.sleep(3)
        except: pass
    return jobs

def get_job_text(url):
    """ Extrae el texto de la oferta para que la IA no tenga que navegar """
    try:
        res = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        # Limpiamos el HTML básico para quedarnos con el texto
        text = re.sub('<[^<]+?>', '', res.text)
        return text[:3000] # Solo los primeros 3000 caracteres
    except: return ""

def analyze_ia(profile, job_text):
    if not GEMINI_API_KEY or not job_text: return None
    genai.configure(api_key=GEMINI_API_KEY)
    # Usamos 1.5-flash que es el estándar más estable
    model = genai.GenerativeModel('gemini-1.5-flash')
    
    prompt = f"""
    PERFIL: {profile.get('summary','')[:500]}
    OFERTA: {job_text}
    
    Responde SOLO JSON:
    {{"classification": "VÁLIDA"|"VÁLIDA_CON_MATICES"|"DESCARTAR", "score": 1-10, "real_title": "...", "company": "...", "match_summary": "..."}}
    """
    try:
        response = model.generate_content(prompt)
        match = re.search(r'\{.*\}', response.text, re.DOTALL)
        return json.loads(match.group()) if match else None
    except: return None

def main():
    today = datetime.now().strftime("%d/%m/%Y")
    log.info(f"=== INICIO AGENTE {today} ===")
    
    # 1. CARGAR DATOS
    try:
        profile, seen_jobs, queue = load_data()
    except Exception as e:
        log.error(f"Error cargando archivos base: {e}")
        return

    # 2. SCRAPING DE IDs
    raw_jobs = scrape_ids()
    
    # Filtrar las que no hayamos visto nunca
    new_jobs = [j for j in raw_jobs if j["id"] not in seen_jobs]
    log.info(f"Total nuevas para analizar: {len(new_jobs)}")

    valid_to_send = []
    caveats_to_send = []

    # 3. ANÁLISIS CON IA (Limitamos a 10 por tanda para no quemar la API)
    for job in new_jobs[:10]:
        log.info(f"   🤖 IA analizando: {job['id']}")
        
        # Extraemos el texto de la web antes de enviarlo a la IA
        job_text = get_job_text(job['link'])
        
        if not job_text:
            log.warning(f"      ⚠️ No se pudo extraer texto de {job['id']}, saltando...")
            continue
            
        res = analyze_ia(profile, job_text)
        
        if res and res.get('classification') != "DESCARTAR":
            # Enriquecemos el objeto job con la respuesta de la IA
            job.update({
                "title": res.get('real_title', 'Puesto sin título'),
                "company": res.get('company', 'Empresa desconocida'),
                "analysis": res
            })
            
            if res['classification'] == "VÁLIDA":
                valid_to_send.append(job)
            else:
                caveats_to_send.append(job)
            
            # Guardamos en la cola histórica
            queue.append({
                "date": today,
                "score": res.get('score', 0),
                "title": job['title'],
                "company": job['company'],
                "link": job['link'],
                "summary": res.get('match_summary', '')
            })
        
        # Marcamos como vista independientemente del resultado
        seen_jobs.add(job["id"])
        
        # Pausa de cortesía para la API de Google
        time.sleep(12)

    # 4. GUARDAR ESTADO (Persistencia)
    try:
        SEEN_PATH.write_text("\n".join(seen_jobs))
        QUEUE_PATH.write_text(json.dumps(queue, indent=2, ensure_ascii=False))
        log.info("✅ Estado guardado en archivos locales.")
    except Exception as e:
        log.error(f"Error guardando el estado: {e}")

    # 5. LÓGICA DE EMAIL (CON PRUEBA DE FALLO)
    if valid_to_send or caveats_to_send:
        log.info(f"📧 Enviando informe con {len(valid_to_send) + len(caveats_to_send)} ofertas...")
        html_content = build_email_html(valid_to_send, caveats_to_send, today)
        
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"🎯 {len(valid_to_send)} Ofertas Filtradas - {today}"
        msg["From"] = GMAIL_USER
        msg["To"] = EMAIL_TO
        msg.attach(MIMEText(html_content, "html"))
        
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(GMAIL_USER, GMAIL_PASS)
                server.send_message(msg)
            log.info("📧 Email enviado con éxito.")
        except Exception as e:
            log.error(f"❌ Error crítico enviando email: {e}")
    else:
        # --- ESTO ES LO QUE HEMOS AÑADIDO PARA EL TEST ---
        log.info("Nada relevante hoy. Enviando correo de diagnóstico...")
        msg = MIMEMultipart()
        msg["Subject"] = f"🤖 Bot Vivo - {today} (Sin matches)"
        msg["From"] = GMAIL_USER
        msg["To"] = EMAIL_TO
        
        cuerpo_test = (
            f"Hola Borja,\n\n"
            f"El script se ha ejecutado correctamente hoy {today}.\n"
            f"- Ofertas encontradas en LinkedIn: {len(raw_jobs)}\n"
            f"- Ofertas nuevas analizadas por la IA: {len(new_jobs[:10])}\n"
            f"- Resultado: La IA ha descartado todas por no cumplir los requisitos del profile.yaml.\n\n"
            f"Si recibes este correo, la configuración de Gmail es CORRECTA."
        )
        msg.attach(MIMEText(cuerpo_test, "plain"))
        
        try:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
                server.login(GMAIL_USER, GMAIL_PASS)
                server.send_message(msg)
            log.info("📧 Email de diagnóstico enviado correctamente.")
        except Exception as e:
            log.error(f"❌ Error en el email de diagnóstico: {e}")

if __name__ == "__main__":
    main()
