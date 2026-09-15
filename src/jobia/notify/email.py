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
import smtplib
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from jinja2 import BaseLoader, Environment, select_autoescape

_DIGEST_TEMPLATE = """\
<!doctype html>
<html lang="es">
<body style="margin:0;padding:0;background:#f4f4f5;font-family:-apple-system,
  Segoe UI,Roboto,Arial,sans-serif;color:#18181b;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:#f4f4f5;padding:24px 0;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:8px;overflow:hidden;">
        <tr><td style="padding:24px 28px 8px 28px;">
          <h1 style="margin:0;font-size:18px;">job-ia · {{ today }}</h1>
          <p style="margin:4px 0 0 0;color:#71717a;font-size:13px;">
            {{ n }} oferta{{ "s" if n != 1 else "" }} nueva{{ "s" if n != 1 else "" }}
          </p>
        </td></tr>
        {% for j in jobs %}
        <tr><td style="padding:16px 28px;border-top:1px solid #e4e4e7;">
          <div style="display:flex;justify-content:space-between;align-items:baseline;">
            <strong style="font-size:15px;">{{ j.title }}</strong>
            <span style="font-size:13px;color:#71717a;">score {{ j.score }}</span>
          </div>
          <div style="font-size:13px;color:#52525b;margin-top:2px;">
            {{ j.company }}{% if j.location %} · {{ j.location }}{% endif %}
          </div>
          {% if j.match %}
          <ul style="margin:8px 0 0 0;padding-left:18px;font-size:13px;color:#27272a;">
            {% for m in j.match[:2] %}<li>{{ m }}</li>{% endfor %}
          </ul>
          {% endif %}
          {% if j.gaps %}
          <div style="font-size:13px;color:#a16207;margin-top:6px;">
            Gap: {{ j.gaps[0] }}
          </div>
          {% endif %}
          {% if j.señal_roja %}
          <div style="font-size:13px;color:#b91c1c;margin-top:6px;">
            ⚠ {{ j.señal_roja }}
          </div>
          {% endif %}
          <div style="margin-top:10px;">
            <a href="{{ j.url }}" style="font-size:13px;color:#2563eb;
               text-decoration:none;">Ver en LinkedIn →</a>
          </div>
        </td></tr>
        {% endfor %}
        <tr><td style="padding:16px 28px;border-top:1px solid #e4e4e7;
                       font-size:11px;color:#a1a1aa;">
          job-ia v2
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

_ALERT_TEMPLATE = """\
<!doctype html>
<html lang="es">
<body style="margin:0;padding:0;background:#fef2f2;font-family:-apple-system,
  Segoe UI,Roboto,Arial,sans-serif;color:#18181b;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="background:#fef2f2;padding:24px 0;">
    <tr><td align="center">
      <table role="presentation" width="600" cellpadding="0" cellspacing="0"
             style="background:#ffffff;border-radius:8px;overflow:hidden;
                    border:1px solid #fecaca;">
        <tr><td style="padding:24px 28px;">
          <h1 style="margin:0 0 8px 0;font-size:18px;color:#b91c1c;">
            ⚠ job-ia · posible bloqueo · {{ today }}
          </h1>
          <p style="margin:0;font-size:14px;color:#3f3f46;">
            Ninguna query devolvio resultados hoy. Una lista vacia nunca es
            "no hay ofertas": es bloqueo hasta que se demuestre lo contrario.
          </p>
          <p style="margin:12px 0 0 0;font-size:13px;color:#71717a;">
            Motivo registrado: {{ reason }}
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>
"""

_env = Environment(loader=BaseLoader(), autoescape=select_autoescape(["html"]))


def render_digest(jobs: list[dict], today: str) -> str:
    return _env.from_string(_DIGEST_TEMPLATE).render(jobs=jobs, today=today, n=len(jobs))


def render_alert(reason: str, today: str) -> str:
    return _env.from_string(_ALERT_TEMPLATE).render(reason=reason, today=today)


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
