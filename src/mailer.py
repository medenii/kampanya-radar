"""HTML e-posta oluşturma ve SMTP ile gönderme (Gmail App Password uyumlu)."""
from __future__ import annotations

import html
import logging
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate
from pathlib import Path

from .config import settings
from .llm import CampaignSummary

log = logging.getLogger(__name__)

BADGE = {5: "#0f9d58", 4: "#34a853", 3: "#f4b400", 2: "#9aa0a6", 1: "#9aa0a6"}


def _card(s: CampaignSummary) -> str:
    e = html.escape
    conditions = "".join(
        f'<li style="margin:0 0 4px 0;">{e(c)}</li>' for c in (s.sartlar or [])
    )
    conditions_block = (
        f'<p style="margin:12px 0 4px;font-size:12px;color:#5f6368;font-weight:600;'
        f'text-transform:uppercase;letter-spacing:.4px;">Şartlar</p>'
        f'<ul style="margin:0;padding-left:18px;font-size:14px;color:#3c4043;">{conditions}</ul>'
        if conditions else ""
    )
    color = BADGE.get(s.onem_puani, "#9aa0a6")
    return f"""
    <tr><td style="padding:0 0 18px 0;">
      <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
             style="border:1px solid #e3e6ea;border-radius:12px;background:#ffffff;
                    border-left:4px solid {color};">
        <tr><td style="padding:20px 22px;">
          <div style="font-size:11px;font-weight:700;color:{color};letter-spacing:.6px;
                      text-transform:uppercase;margin-bottom:6px;">
            {e(s.source)} &nbsp;•&nbsp; Önem {s.onem_puani}/5
          </div>
          <h2 style="margin:0 0 10px;font-size:18px;line-height:1.35;color:#202124;">
            {e(s.kampanya_adi)}
          </h2>
          <p style="margin:0 0 14px;font-size:14px;line-height:1.6;color:#3c4043;">
            {e(s.ozet)}
          </p>
          <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
                 style="font-size:13px;color:#3c4043;background:#f8f9fa;border-radius:8px;">
            <tr><td style="padding:10px 12px;width:110px;color:#5f6368;">Kazanım</td>
                <td style="padding:10px 12px;font-weight:600;">{e(s.kazanim)}</td></tr>
            <tr><td style="padding:0 12px 10px;color:#5f6368;">Bitiş</td>
                <td style="padding:0 12px 10px;font-weight:600;">{e(s.bitis_tarihi)}</td></tr>
            <tr><td style="padding:0 12px 10px;color:#5f6368;">Kimler</td>
                <td style="padding:0 12px 10px;">{e(s.hedef_kitle)}</td></tr>
          </table>
          {conditions_block}
          <div style="margin-top:18px;">
            <a href="{e(s.url)}"
               style="display:inline-block;background:#1a73e8;color:#ffffff;
                      text-decoration:none;font-size:14px;font-weight:600;
                      padding:10px 20px;border-radius:8px;">Kampanyaya Git →</a>
          </div>
        </td></tr>
      </table>
    </td></tr>"""


def build_html(summaries: list[CampaignSummary], errors: list[str] | None = None) -> str:
    today = datetime.now().strftime("%d.%m.%Y")
    cards = "".join(_card(s) for s in summaries)
    error_block = ""
    if errors:
        rows = "".join(f"<li>{html.escape(x)}</li>" for x in errors)
        error_block = f"""
        <tr><td style="padding:6px 0 18px;">
          <div style="border:1px solid #fdd;background:#fff8f8;border-radius:10px;
                      padding:14px 16px;font-size:12px;color:#a50e0e;">
            <strong>Taranamayan kaynaklar</strong>
            <ul style="margin:8px 0 0;padding-left:18px;">{rows}</ul>
          </div></td></tr>"""

    return f"""<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f1f3f4;
             font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" role="presentation"
         style="background:#f1f3f4;padding:28px 12px;">
    <tr><td align="center">
      <table width="640" cellpadding="0" cellspacing="0" role="presentation"
             style="max-width:640px;width:100%;">
        <tr><td style="padding:0 0 22px;">
          <h1 style="margin:0 0 4px;font-size:22px;color:#202124;">
            🏦 Yeni Banka Kampanyaları
          </h1>
          <p style="margin:0;font-size:13px;color:#5f6368;">
            {today} • {len(summaries)} yeni kampanya tespit edildi
          </p>
        </td></tr>
        {cards}
        {error_block}
        <tr><td style="padding:10px 0 0;text-align:center;font-size:11px;color:#80868b;">
          Bu e-posta otomatik oluşturuldu • Kampanya Radar<br>
          Özetler yapay zekâ tarafından üretilmiştir; koşulları resmî sayfadan doğrulayın.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>"""


def build_text(summaries: list[CampaignSummary]) -> str:
    lines = [f"Yeni Banka Kampanyaları — {datetime.now():%d.%m.%Y}", ""]
    for s in summaries:
        lines += [
            f"[{s.source}] {s.kampanya_adi}",
            f"  Özet   : {s.ozet}",
            f"  Kazanım: {s.kazanim}",
            f"  Bitiş  : {s.bitis_tarihi}",
            f"  Link   : {s.url}",
            "",
        ]
    return "\n".join(lines)


def send_email(subject: str, html_body: str, text_body: str,
               attachment: "Path | None" = None) -> bool:
    missing = settings.validate_mail()
    if missing:
        log.error("E-posta gönderilemedi, eksik ayar: %s", ", ".join(missing))
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.mail_from_name, settings.smtp_user))
    msg["To"] = ", ".join(settings.recipients)
    msg["Date"] = formatdate(localtime=True)
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    if attachment:
        attachment = Path(attachment)
        if attachment.exists():
            size_mb = attachment.stat().st_size / 1_048_576
            if size_mb > 20:
                log.warning("Ek çok büyük (%.1f MB), atlanıyor.", size_mb)
            else:
                msg.add_attachment(
                    attachment.read_bytes(),
                    maintype="application",
                    subtype="vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    filename=attachment.name,
                )
                log.info("Ek eklendi: %s (%.2f MB)", attachment.name, size_mb)
        else:
            log.warning("Ek bulunamadı: %s", attachment)

    context = ssl.create_default_context()
    try:
        if settings.smtp_port == 465:
            with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                                  context=context, timeout=45) as server:
                server.login(settings.smtp_user, settings.smtp_pass)
                server.send_message(msg)
        else:  # 587 STARTTLS
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=45) as server:
                server.starttls(context=context)
                server.login(settings.smtp_user, settings.smtp_pass)
                server.send_message(msg)
        log.info("E-posta gönderildi -> %s", msg["To"])
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("SMTP hatası: %s", exc)
        return False


def send_campaigns(summaries: list[CampaignSummary], errors: list[str] | None = None,
                   attachment: Path | None = None, total_rows: int = 0) -> bool:
    if not summaries:
        log.info("Yeni kampanya yok — e-posta gönderilmiyor.")
        return False
    subject = f"🏦 {len(summaries)} yeni kampanya — {datetime.now():%d.%m.%Y}"
    html = build_html(summaries, errors)
    if attachment:
        html = html.replace(
            "Bu e-posta otomatik oluşturuldu",
            f"📎 Ekteki Excel'de {total_rows} kampanya kategorilere ayrılmış halde<br>"
            "Bu e-posta otomatik oluşturuldu",
        )
    return send_email(subject, html, build_text(summaries), attachment)


def send_daily_digest(attachment: Path | None, total_rows: int, new_count: int,
                      errors: list[str] | None = None) -> bool:
    """Yeni kampanya olmasa bile günlük Excel'i gönderir."""
    today = datetime.now().strftime("%d.%m.%Y")
    error_note = ""
    if errors:
        rows = "".join(f"<li>{html.escape(e)}</li>" for e in errors)
        error_note = (
            "<div style='border:1px solid #fdd;background:#fff8f8;border-radius:10px;"
            "padding:12px 14px;font-size:12px;color:#a50e0e;margin-top:16px;'>"
            f"<strong>Taranamayan kaynaklar</strong><ul style='margin:6px 0 0;"
            f"padding-left:18px;'>{rows}</ul></div>"
        )
    body = f"""<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8"></head>
<body style="margin:0;padding:28px 12px;background:#f1f3f4;
             font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;">
  <div style="max-width:560px;margin:0 auto;background:#fff;border-radius:12px;
              padding:26px 28px;border:1px solid #e3e6ea;">
    <h1 style="margin:0 0 6px;font-size:20px;color:#202124;">🏦 Günlük Kampanya Raporu</h1>
    <p style="margin:0 0 18px;font-size:13px;color:#5f6368;">{today}</p>
    <p style="margin:0 0 8px;font-size:14px;color:#3c4043;">
      Bugün yeni kampanya bulunmadı. Takip listesi güncel.
    </p>
    <p style="margin:0;font-size:14px;color:#3c4043;">
      📎 Ekteki Excel'de toplam <strong>{total_rows}</strong> kampanya kategorilere
      ayrılmış halde bulunuyor.
    </p>
    {error_note}
    <p style="margin:20px 0 0;font-size:11px;color:#80868b;">
      Kampanya Radar • otomatik oluşturuldu
    </p>
  </div>
</body></html>"""
    text = (f"Günlük Kampanya Raporu — {today}\n"
            f"Bugün yeni kampanya yok. Excel'de toplam {total_rows} kampanya var.")
    return send_email(f"📊 Günlük kampanya raporu — {today}", body, text, attachment)


def send_error_report(errors: list[str]) -> bool:
    if not errors or not settings.send_error_report:
        return False
    rows = "".join(f"<li>{html.escape(e)}</li>" for e in errors)
    body = (
        f"<h2 style='font-family:sans-serif'>Kampanya Radar — tarama hataları</h2>"
        f"<ul style='font-family:sans-serif;font-size:14px'>{rows}</ul>"
        f"<p style='font-size:12px;color:#666'>Selector veya site yapısı değişmiş olabilir.</p>"
    )
    return send_email(
        f"⚠️ Kampanya Radar: {len(errors)} kaynak taranamadı",
        body,
        "\n".join(errors),
    )
