#!/usr/bin/env python3
"""
Surveillance des séances Pathé pour le cinéma Brumath.
Détecte la disponibilité et envoie un email uniquement lors de la transition indisponible -> disponible.
"""

import os
import json
import re
import smtplib
from datetime import datetime, timezone
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError  # type: ignore

# --- Constantes ---
FILM_NAME = "Avatar : De feu et de cendres"
FILM_URL = "https://www.pathe.fr/films/avatar-de-feu-et-de-cendres-11387"
CINEMA_KEYWORD = "Brumath"
CINEMA_URL = "https://www.pathe.fr/cinemas/cinema-pathe-brumath"
STATE_FILE = "state.json"

# --- SMTP Brevo ---
SMTP_HOST = "smtp-relay.brevo.com"
SMTP_PORT = 587


def log(message: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {message}", flush=True)


def read_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"⚠️ Impossible de lire {STATE_FILE} ({e}). État par défaut.")
    return {"last_status": "unavailable", "last_seen_at": None}


def write_state(state: dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"❌ Impossible d'écrire {STATE_FILE}: {e}")


def accept_cookies(page) -> None:
    """
    Essaie de fermer/valider le bandeau cookies Pathé.
    Ne plante jamais si absent.
    """
    candidates = [
        ("button", r"Tout accepter"),
        ("button", r"Accepter( et fermer)?"),
        ("button", r"J'?accepte"),
        ("button", r"Continuer"),
        ("button", r"OK"),
        ("button", r"Fermer"),
        ("link", r"Tout accepter"),
        ("link", r"Accepter"),
    ]

    # Le bandeau peut apparaître après le chargement => plusieurs tentatives
    for _ in range(3):
        for role, pattern in candidates:
            try:
                page.get_by_role(role, name=re.compile(pattern, re.I)).click(timeout=1500)
                log("🍪 Cookies acceptés/fermés")
                page.wait_for_timeout(400)
                return
            except Exception:
                pass
        page.wait_for_timeout(700)


def check_availability() -> tuple[bool, dict]:
    """
    Vérifie la disponibilité en passant par la page du cinéma Pathé Brumath
    (pas besoin de sélectionner le cinéma).
    Ensuite ouvre la page du film depuis la page cinéma, puis détecte horaires/réserver.

    Retourne (available, debug_info).
    """
    debug_info = {
        "brumath_present": True,  # on est sur la page Brumath
        "reservation_signal": False,
        "nb_horaires": 0,
        "error": None,
        "film_found_on_cinema_page": False,
        "film_page_url": None,
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(45000)

            # 1) Ouvrir la page du CINÉMA Brumath
            log(f"🏢 Ouverture cinéma: {CINEMA_URL}")
            page.goto(CINEMA_URL, wait_until="networkidle")
            page.wait_for_timeout(1500)
            accept_cookies(page)

            # 2) Vérifier si le film est listé sur la page du cinéma
            try:
                page.get_by_role("link", name=re.compile(re.escape(FILM_NAME), re.I)).first.wait_for(timeout=8000)
                debug_info["film_found_on_cinema_page"] = True
                log("✅ Film trouvé sur la page cinéma")
            except Exception:
                log("ℹ️ Film non trouvé sur la page cinéma (pas encore programmé à Brumath)")
                browser.close()
                return False, debug_info

            # 3) Cliquer sur le film depuis la page cinéma
            page.get_by_role("link", name=re.compile(re.escape(FILM_NAME), re.I)).first.click(timeout=8000)
            page.wait_for_load_state("networkidle")
            page.wait_for_timeout(1500)

            debug_info["film_page_url"] = page.url
            log(f"🎬 Page film ouverte depuis cinéma: {page.url}")

            accept_cookies(page)

            # 4) Lire le texte et détecter réservation/horaires
            body_text = page.inner_text("body")
            browser.close()

        reservation_keywords = ["réserver", "reserver", "e-billet", "billetterie"]
        reservation_signal = any(k in body_text.lower() for k in reservation_keywords)
        debug_info["reservation_signal"] = reservation_signal

        horaire_pattern = r"\b(?:[01]\d|2[0-3]):[0-5]\d\b"
        horaires = re.findall(horaire_pattern, body_text)
        debug_info["nb_horaires"] = len(horaires)

        available = reservation_signal or debug_info["nb_horaires"] > 0

        log(
            f"🔎 reservation_signal={debug_info['reservation_signal']} | "
            f"nb_horaires={debug_info['nb_horaires']} | available={available}"
        )
        return available, debug_info

    except PlaywrightTimeoutError as e:
        debug_info["error"] = f"Timeout Playwright: {e}"
        log(f"❌ {debug_info['error']}")
        return False, debug_info
    except Exception as e:
        debug_info["error"] = f"Erreur scraping: {e}"
        log(f"❌ {debug_info['error']}")
        return False, debug_info


def send_email_brevo(subject: str, body: str) -> bool:
    smtp_user = os.environ.get("BREVO_SMTP_USER")
    smtp_pass = os.environ.get("BREVO_SMTP_KEY")
    from_email = os.environ.get("BREVO_FROM_EMAIL")
    to_email = os.environ.get("ALERT_TO_EMAIL", "satheeshprashanth2002@gmail.com")

    if not all([smtp_user, smtp_pass, from_email, to_email]):
        log("❌ Variables SMTP manquantes")
        return False

    msg = MIMEMultipart()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        log("✉️ Connexion SMTP Brevo…")
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, [to_email], msg.as_string())
        log(f"✅ Email envoyé à {to_email}")
        return True
    except Exception as e:
        log(f"❌ Erreur envoi email: {e}")
        return False


def main():
    log("===== START =====")
    state = read_state()
    last_status = state.get("last_status", "unavailable")

    available, debug = check_availability()
    new_status = "available" if available else "unavailable"

    if new_status == "available" and last_status != "available":
        subject = f"🎬 Pathé Brumath: séances dispo - {FILM_NAME}"
        body = (
            f"Film: {FILM_NAME}\n"
            f"Cinéma: Pathé {CINEMA_KEYWORD}\n"
            f"URL film: {FILM_URL}\n"
            f"URL cinéma: {CINEMA_URL}\n\n"
            f"Détails:\n"
            f"- film_found_on_cinema_page: {debug.get('film_found_on_cinema_page')}\n"
            f"- film_page_url: {debug.get('film_page_url')}\n"
            f"- reservation_signal: {debug['reservation_signal']}\n"
            f"- nb_horaires: {debug['nb_horaires']}\n"
            f"- error: {debug.get('error')}\n\n"
            f"Date (UTC): {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        send_email_brevo(subject, body)
    else:
        log("ℹ️ Pas de transition indispo->dispo")

    state["last_status"] = new_status
    state["last_seen_at"] = datetime.now(timezone.utc).isoformat()
    write_state(state)

    log("===== END =====")


if __name__ == "__main__":
    main()
