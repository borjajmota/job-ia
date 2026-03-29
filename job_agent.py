import os
import requests
import re
import time
import json
import logging
from google import genai
from google.genai import types

# --- CONFIGURACIÓN ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
SEARCH_QUERIES = ["Data AI Cloud", "Solutions Architect Cloud", "Inteligencia Artificial"]
# Añade aquí el resto de tus queries

def scrape_linkedin_jobs():
    """Busca ofertas usando el buscador público de LinkedIn (Guest API)."""
    all_jobs = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    for query in SEARCH_QUERIES:
        log.info(f"Buscando: {query}...")
        # Buscamos en Madrid, España (geoId 104305776) o por texto
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={query}&location=Madrid%2C%20España&f_TPR=r604800"
        
        try:
            res = requests.get(url, headers=headers, timeout=15)
            # Extraer IDs de trabajos con Regex del HTML devuelto
            job_ids = re.findall(r'data-entity-id=\"urn:li:jobListing:(\d+)\"', res.text)
            
            for j_id in set(job_ids):
                all_jobs.append({
                    'id': j_id,
                    'title': "Oferta encontrada", 
                    'link': f"https://www.linkedin.com/jobs/view/{j_id}/",
                })
            time.sleep(1) # Cortesía para evitar bloqueos
        except Exception as e:
            log.error(f"Error en query {query}: {e}")

    return all_jobs

def analyze_with_gemini(job_link):
    """Análisis simple con la nueva librería de Google 2026."""
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"Analiza si esta oferta es relevante para un experto en Cloud/AI: {job_link}. Responde solo JSON: {{'relevant': true/false, 'reason': '...'}}"
    
    try:
        response = client.models.generate_content(
            model="gemini-1.5-flash",
            contents=prompt
        )
        return json.loads(response.text.replace("```json", "").replace("```", ""))
    except:
        return {"relevant": False, "reason": "Error análisis"}

def main():
    log.info("=== Agente arrancando (Versión HTTP/2026) ===")
    
    # 1. Scraping
    jobs = scrape_linkedin_jobs()
    log.info(f"Encontradas {len(jobs)} ofertas potenciales.")

    # 2. Cargar vistos (para no repetir)
    seen_file = "seen_jobs.txt"
    seen_ids = set()
    if os.path.exists(seen_file):
        with open(seen_file, "r") as f:
            seen_ids = set(line.strip() for line in f)

    # 3. Procesar nuevas
    new_jobs = [j for j in jobs if j['id'] not in seen_ids]
    log.info(f"Nuevas para analizar: {len(new_jobs)}")

    for job in new_jobs[:5]: # Limitamos a 5 para no quemar la API en tests
        analysis = analyze_with_gemini(job['link'])
        if analysis.get('relevant'):
            log.info(f"¡INTERESANTE!: {job['link']} - {analysis['reason']}")
            # Aquí iría tu función de enviar email
        
        # Guardar como visto
        with open(seen_file, "a") as f:
            f.write(f"{job['id']}\n")

if __name__ == "__main__":
    main()
