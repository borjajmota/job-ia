"""Email HTML sobrio via Jinja2. JOBIA_DRY_RUN=1 imprime en consola en vez de
enviar -- para iterar en la plantilla sin gastar cuota SMTP.

Dos plantillas:
  - digest: 3-8 ofertas con score, 2 razones de encaje, 1 gap, senal roja
    si la hay, y enlace directo. La rutina de las 09:00 (DISENO.md, S5).
  - alert: "todas las queries volvieron vacias" no es "no hay ofertas", es
    bloqueo. Es la linea de codigo mas valiosa del proyecto (DISENO.md, 4.1).
"""
from __future__ import annotations

import os
import re
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from jinja2 import BaseLoader, Environment, select_autoescape

# Autoescape de Jinja ya impide inyectar tags/comillas via titulo, empresa,
# match, gaps o señal_roja (dato de LinkedIn/LLM, no confiable). Pero un
# href="{{ j.url }}" no valida el ESQUEMA: un "javascript:..." no rompe
# ninguna comilla y aun asi queda como enlace clicable. j.url hoy siempre
# se reconstruye en linkedin.py como https://www.linkedin.com/jobs/view/
# <digitos>, pero esa garantia vive en otro archivo -- se revalida aqui,
# en el punto de renderizado, en vez de confiar en que siga siendo asi.
_SAFE_JOB_URL_RE = re.compile(r"^https://www\.linkedin\.com/jobs/view/\d+/?$")


def _safe_url(url: object) -> str | None:
    return url if isinstance(url, str) and _SAFE_JOB_URL_RE.match(url) else None

_FONT = ("-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,"
         "sans-serif")

def _header(accent: str) -> str:
    """Cabecera compartida: wordmark + fecha, misma tipografia en las tres
    plantillas. Nada de flex -- una tabla de una fila, dos celdas. `accent`
    se fija en Python al definir cada plantilla (no viaja como variable de
    Jinja: aqui no hace falta y evita escapar llaves dos veces)."""
    return f"""\
<tr><td style="padding:0 6px 22px 6px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
    <td style="font-size:13px;font-weight:700;letter-spacing:.06em;
               color:{accent};text-transform:uppercase;font-family:{_FONT};">
      job·ia
    </td>
    <td align="right" style="font-size:12px;color:#9c9a96;font-family:{_FONT};">
      {{{{ today }}}}
    </td>
  </tr></table>
</td></tr>"""

_DIGEST_TEMPLATE = f"""\
<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f6f5f3;font-family:{_FONT};">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">
    {{% if jobs %}}{{{{ jobs[0].title }}}}{{% if n > 1 %}} y {{{{ n - 1 }}}} mas{{% endif %}}{{% endif %}}
  </div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f5f3;">
    <tr><td align="center" style="padding:36px 16px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
        {_header("#141416")}
        <tr><td style="padding:0 6px 22px 6px;">
          <div style="font-size:21px;font-weight:600;color:#141416;letter-spacing:-.01em;font-family:{_FONT};">
            {{{{ n }}}} oferta{{{{ "s" if n != 1 else "" }}}} nueva{{{{ "s" if n != 1 else "" }}}}
          </div>
        </td></tr>

        {{% for j in jobs %}}
        <tr><td style="padding-bottom:12px;">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                 style="background:#ffffff;border:1px solid #e9e7e2;border-radius:14px;">
            <tr><td style="padding:20px 22px;">

              <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
                <td style="font-size:16px;font-weight:600;color:#141416;line-height:1.4;font-family:{_FONT};">
                  {{{{ j.title }}}}
                </td>
                <td align="right" valign="top" style="padding-left:14px;white-space:nowrap;">
                  <span style="display:inline-block;background:#141416;color:#ffffff;font-family:{_FONT};
                               font-size:12px;font-weight:700;padding:4px 11px;border-radius:100px;">
                    {{{{ j.score }}}}
                  </span>
                </td>
              </tr></table>

              <div style="font-size:13px;color:#83817c;margin-top:4px;font-family:{_FONT};">
                {{{{ j.company }}}}{{% if j.location %}} · {{{{ j.location }}}}{{% endif %}}
              </div>

              {{% if j.match %}}
              <table role="presentation" cellpadding="0" cellspacing="0" style="margin-top:14px;">
                {{% for m in j.match[:2] %}}
                <tr><td style="font-size:13.5px;color:#141416;line-height:1.55;padding-top:3px;
                               font-family:{_FONT};" width="18" valign="top">
                  <span style="color:#188a4c;">✓</span>
                </td><td style="font-size:13.5px;color:#3a393e;line-height:1.55;padding-top:3px;
                                font-family:{_FONT};">{{{{ m }}}}</td></tr>
                {{% endfor %}}
              </table>
              {{% endif %}}

              {{% if j.gaps %}}
              <div style="margin-top:12px;background:#fbf3e6;border-radius:9px;padding:9px 13px;">
                <span style="font-size:10.5px;font-weight:700;letter-spacing:.05em;color:#9a6a12;
                             text-transform:uppercase;font-family:{_FONT};">Gap</span>
                <div style="font-size:13px;color:#7a5610;margin-top:2px;line-height:1.45;
                            font-family:{_FONT};">{{{{ j.gaps[0] }}}}</div>
              </div>
              {{% endif %}}

              {{% if j.señal_roja %}}
              <div style="margin-top:8px;background:#fdf1f0;border-radius:9px;padding:9px 13px;">
                <span style="font-size:10.5px;font-weight:700;letter-spacing:.05em;color:#b91c1c;
                             text-transform:uppercase;font-family:{_FONT};">⚠ Señal roja</span>
                <div style="font-size:13px;color:#9f1d1d;margin-top:2px;line-height:1.45;
                            font-family:{_FONT};">{{{{ j.señal_roja }}}}</div>
              </div>
              {{% endif %}}

              {{% if j.url %}}
              <div style="margin-top:17px;">
                <a href="{{{{ j.url }}}}" style="display:inline-block;font-size:13px;font-weight:600;
                   color:#ffffff;background:#141416;text-decoration:none;padding:9px 18px;
                   border-radius:100px;font-family:{_FONT};">
                  Ver oferta →
                </a>
              </div>
              {{% endif %}}

            </td></tr>
          </table>
        </td></tr>
        {{% endfor %}}

        {{% if notes %}}
        <tr><td style="padding:6px 6px 0 6px;">
          {{% for note in notes %}}
          <div style="font-size:12px;color:#9a6a12;margin-top:5px;font-family:{_FONT};">⚠ {{{{ note }}}}</div>
          {{% endfor %}}
        </td></tr>
        {{% endif %}}

        <tr><td style="padding:22px 6px 0 6px;border-top:1px solid #e9e7e2;margin-top:4px;">
          <div style="font-size:11px;color:#adaba6;padding-top:16px;font-family:{_FONT};">
            job-ia · corrida diaria
          </div>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

_NOTICE_TEMPLATE = f"""\
<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f6f5f3;font-family:{_FONT};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f5f3;">
    <tr><td align="center" style="padding:36px 16px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
        {_header("#141416")}
        <tr><td>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                 style="background:#ffffff;border:1px solid #e9e7e2;border-radius:14px;">
            <tr><td style="padding:22px 24px;">
              <div style="font-size:15px;font-weight:600;color:#141416;font-family:{_FONT};">
                Sin ofertas por encima del umbral hoy
              </div>
              <p style="margin:6px 0 12px 0;font-size:13px;color:#83817c;font-family:{_FONT};">
                Pero hay avisos que conviene revisar:
              </p>
              {{% for note in notes %}}
              <div style="font-size:13px;color:#9a6a12;margin-top:6px;line-height:1.5;
                          font-family:{_FONT};">⚠ {{{{ note }}}}</div>
              {{% endfor %}}
            </td></tr>
          </table>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

_ALERT_TEMPLATE = f"""\
<!doctype html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f6f5f3;font-family:{_FONT};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f6f5f3;">
    <tr><td align="center" style="padding:36px 16px;">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;">
        {_header("#b91c1c")}
        <tr><td>
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
                 style="background:#ffffff;border:1px solid #f3c9c4;border-radius:14px;">
            <tr><td style="padding:22px 24px;">
              <div style="font-size:15px;font-weight:700;color:#b91c1c;font-family:{_FONT};">
                ⚠ Posible bloqueo de LinkedIn
              </div>
              <p style="margin:8px 0 0 0;font-size:13.5px;color:#3a393e;line-height:1.55;font-family:{_FONT};">
                Ninguna query devolvio resultados hoy. Una lista vacia nunca es
                "no hay ofertas": es bloqueo hasta que se demuestre lo contrario.
              </p>
              <div style="margin-top:14px;background:#fdf1f0;border-radius:9px;padding:9px 13px;">
                <span style="font-size:10.5px;font-weight:700;letter-spacing:.05em;color:#b91c1c;
                             text-transform:uppercase;font-family:{_FONT};">Motivo</span>
                <div style="font-size:13px;color:#9f1d1d;margin-top:2px;font-family:{_FONT};">{{{{ reason }}}}</div>
              </div>
            </td></tr>
          </table>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

_env = Environment(loader=BaseLoader(), autoescape=select_autoescape(["html"]))


def render_digest(jobs: list[dict], today: str, notes: list[str] | None = None) -> str:
    safe_jobs = [{**j, "url": _safe_url(j.get("url"))} for j in jobs]
    return _env.from_string(_DIGEST_TEMPLATE).render(
        jobs=safe_jobs, today=today, n=len(jobs), notes=notes or [])


def render_alert(reason: str, today: str) -> str:
    return _env.from_string(_ALERT_TEMPLATE).render(reason=reason, today=today)


def render_notice(notes: list[str], today: str) -> str:
    return _env.from_string(_NOTICE_TEMPLATE).render(notes=notes, today=today)


def send(html: str, subject: str) -> None:
    if os.environ.get("JOBIA_DRY_RUN") == "1":
        output = f"\n=== JOBIA_DRY_RUN: {subject} ===\n{html}\n"
        try:
            print(output)
        except UnicodeEncodeError:
            # Consola Windows en cp1252: reintenta forzando la salida a utf-8.
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            print(output)
        return

    user = os.environ["GMAIL_USER"]
    password = os.environ["GMAIL_APP_PASSWORD"]
    to = os.environ["EMAIL_TO"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = to
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(user, password)
        smtp.sendmail(user, [to], msg.as_string())
