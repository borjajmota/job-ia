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
    """Busca ofertas usando el buscador público con una Regex más robusta."""
    all_jobs = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    }

    for query in SEARCH_QUERIES:
        query_esc = query.replace(" ", "%20")
        log.info(f"Buscando: {query}...")
        
        # URL de la API de 'ver más' que es más fácil de parsear
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={query_esc}&location=Madrid%2C%20España&f_TPR=r604800&start=0"
        
        try:
            res = requests.get(url, headers=headers, timeout=15)
            # Probamos con dos patrones comunes de LinkedIn para asegurar el tiro
            job_ids = re.findall(r'job-search-card__list-item["\'].*?data-id=["\'](\d+)["\']', res.text)
            if not job_ids:
                job_ids = re.findall(r'jobPostingCardApiHandle["\'].*?data-id=["\'](\d+)["\']', res.text)
            if not job_ids:
                job_ids = re.findall(r'entity-id=["\']urn:li:jobListing:(\d+)["\']', res.text)

            unique_ids = list(set(job_ids))
            for j_id in unique_ids:
                all_jobs.append({
                    'id': j_id,
                    'title': f"Oferta {j_id}", 
                    'link': f"https://www.linkedin.com/jobs/view/{j_id}/",
                })
            
            log.info(f"-> {query}: {len(unique_ids)} ofertas encontradas.")
            time.sleep(2) 
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
