def scrape_linkedin_jobs():
    """
    Atacamos el endpoint de carga dinámica (seeMoreJobPostings).
    Es mucho más ligero y menos propenso a devolver 0 resultados.
    """
    jobs_found = []
    # Rotamos User-Agents de 2026
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ]

    for query in SEARCH_QUERIES:
        log.info(f"🕵️ Buscando via API Oculta: {query}...")
        
        # Endpoint de 'Invitados' que suele saltarse el muro de login
        # start=0 es la primera página, podemos subirlo para más resultados
        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings?keywords={query.replace(' ', '%20')}&location=Madrid%2C%20España&f_TPR=r604800&start=0"
        
        try:
            headers = {"User-Agent": random.choice(user_agents)}
            res = requests.get(url, headers=headers, timeout=20)
            
            if res.status_code == 200:
                # Buscamos el patrón data-entity-urn="urn:li:jobPosting:4181953086"
                ids = re.findall(r'jobPosting:(\d+)', res.text)
                
                # Si no encuentra por URN, buscamos por el link directo
                if not ids:
                    ids = re.findall(r'/view/(\d+)', res.text)

                unique_ids = list(set(ids))
                for j_id in unique_ids:
                    jobs_found.append({
                        "job_id": j_id,
                        "title": "Analizando...", # Se sacará en el paso de IA
                        "link": f"https://www.linkedin.com/jobs/view/{j_id}/"
                    })
                log.info(f"  ✅ {len(unique_ids)} IDs potenciales encontrados.")
            else:
                log.warning(f"  ⚠️ Error {res.status_code} en query {query}")
                
            time.sleep(random.uniform(3, 6)) # Delay humano para evitar el baneo de IP
            
        except Exception as e:
            log.error(f"❌ Fallo en scraping: {e}")
            
    return jobs_found

# --- FUNCIÓN DE EMAIL COMPLETA (TU DISEÑO ORIGINAL) ---
def build_email_html(valid, with_caveats, date_str):
    """Genera el cuerpo HTML del email de informe conservando tu estilo."""

    def job_card(j, idx, is_caveat=False):
        color = "#f59e0b" if is_caveat else "#22c55e"
        bg = "#fffbeb" if is_caveat else "#f0fdf4"
        badge = "CON MATICES" if is_caveat else "VÁLIDA"
        
        return f"""
        <div style="border-left:4px solid {color};padding:14px 18px;margin-bottom:16px;background:{bg};border-radius:0 8px 8px 0;">
          <div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">
            <span style="background:{color};color:white;font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;">#{idx} {badge}</span>
            <span style="background:white;color:{color};border:1px solid {color};font-size:11px;font-weight:600;padding:2px 8px;border-radius:20px;">Score: {j['analysis']['score']}/10</span>
          </div>
          <div style="font-size:17px;font-weight:600;color:#111;margin:6px 0 2px;">{j['title']}</div>
          <div style="font-size:13px;color:#333;margin-bottom:12px;">{j['analysis']['match_summary']}</div>
          {"<div style='font-size:12px;color:#92400e;background:#fef9c3;padding:8px 12px;border-radius:6px;margin-bottom:12px;'><strong>Gaps:</strong> "+j['analysis']['gaps']+"</div>" if is_caveat else ""}
          <a href="{j['link']}" style="display:inline-block;background:{color};color:white;text-decoration:none;padding:7px 18px;border-radius:6px;font-size:13px;font-weight:600;">Aplicar en LinkedIn →</a>
        </div>"""

    valid_cards = "".join(job_card(j, i+1) for i, j in enumerate(valid))
    caveat_cards = "".join(job_card(j, i+1, True) for i, j in enumerate(with_caveats))

    return f"""
<!DOCTYPE html>
<html>
<body style="font-family:sans-serif;max-width:600px;margin:0 auto;padding:20px;background:#f9fafb;">
  <div style="background:white;border-radius:12px;padding:24px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">
    <h1 style="font-size:20px;color:#111;border-bottom:1px solid #eee;padding-bottom:12px;">🚀 Informe de Empleo - {date_str}</h1>
    <p style="font-size:14px;color:#666;">Hemos analizado las últimas ofertas en Madrid para tu perfil.</p>
    
    <h2 style="font-size:16px;color:#166534;margin-top:24px;">✅ Ofertas Válidas ({len(valid)})</h2>
    {valid_cards if valid_cards else '<p style="color:#888;">No se han encontrado matches directos hoy.</p>'}
    
    <h2 style="font-size:16px;color:#92400e;margin-top:24px;">⚠️ Con Matices ({len(with_caveats)})</h2>
    {caveat_cards if caveat_cards else '<p style="color:#888;">No hay ofertas con matices.</p>'}
    
    <div style="margin-top:30px;font-size:11px;color:#aaa;text-align:center;border-top:1px solid #eee;padding-top:12px;">
      Generado por Borja's AI Agent 2026.
    </div>
  </div>
</body>
</html>"""
