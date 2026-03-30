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
    profile, seen, queue = load_data()
    
    found = scrape_ids()
    new_jobs = [j for j in found if j["id"] not in seen]
    log.info(f"Nuevas: {len(new_jobs)}")

    valid, caveats = [], []
    for job in new_jobs[:10]:
        log.info(f"   🤖 Analizando: {job['id']}")
        text = get_job_text(job['link'])
        res = analyze_ia(profile, text)
        
        if res and res.get('classification') != "DESCARTAR":
            job.update({"title": res.get('real_title'), "company": res.get('company'), "analysis": res})
            if res['classification'] == "VÁLIDA": valid.append(job)
            else: caveats.append(job)
            queue.append({"date": today, "score": res['score'], "title": job['title'], "link": job['link']})
        
        seen.add(job["id"])
        time.sleep(5) # Pausa corta, ya no necesitamos 60s porque no navegamos

    SEEN_PATH.write_text("\n".join(seen))
    QUEUE_PATH.write_text(json.dumps(queue, indent=2))

    if (valid or caveats) and GMAIL_USER:
        # (Lógica de envío de email simplificada para ahorrar espacio)
        body = f"Reporte {today}\n" + "\n".join([f"- {j['title']} ({j['company']}): {j['link']}" for j in valid+caveats])
        msg = MIMEMultipart(); msg["Subject"], msg["From"], msg["To"] = f"🎯 Ofertas {today}", GMAIL_USER, EMAIL_TO
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(GMAIL_USER, GMAIL_PASS); s.send_message(msg)
        log.info("📧 Email enviado.")

if __name__ == "__main__": main()
