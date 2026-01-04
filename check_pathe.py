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
                page.wait_for_timeout(500)
                return
            except Exception:
                pass
        page.wait_for_timeout(700)


def select_cinema_brumath(page) -> None:
    """
    Sélectionne le cinéma Pathé Brumath via /cinemas (plus fiable que sur la page film).
    """
    try:
        log("🏢 Ouverture page cinémas…")
        page.goto("https://www.pathe.fr/cinemas", wait_until="networkidle")
        page.wait_for_timeout(1500)
        accept_cookies(page)

        log("🔎 Recherche du cinéma Brumath…")

        # Champ de recherche (priorité type=search, sinon placeholder "Recherch", sinon premier input)
        search_input = page.locator("input[type='search'], input[placeholder*='Recherch' i], input").first
        search_input.click(timeout=7000)
        search_input.fill("Brumath")

        page.wait_for_timeout(800)

        # Cliquer sur “Pathé Brumath” si visible, sinon “Brumath”
        try:
            page.get_by_text(re.compile(r"Pathé\s+Brumath", re.I)).first.click(timeout=8000)
        except Exception:
            page.get_by_text(re.compile(r"Brumath", re.I)).first.click(timeout=8000)

        page.wait_for_timeout(1500)
        log("✅ Cinéma Pathé Brumath sélectionné")
    except Exception as e:
        log(f"⚠️ Impossible de sélectionner le cinéma via /cinemas : {e}")


def check_availability() -> tuple[bool, dict]:
    """
    Retourne (available, debug_info).
    available = Brumath présent ET (signal réservation OU horaires HH:MM).
    """
    debug_info = {
        "brumath_present": False,
        "reservation_signal": False,
        "nb_horaires": 0,
        "error": None,
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.set_default_timeout(30000)

            # 1) Sélectionner le cinéma (cookie/pref) via /cinemas
            select_cinema_brumath(page)

            # 2) Ouvrir la page du film après sélection du cinéma
            log(f"🎬 Ouverture page film: {FILM_URL}")
            page.goto(FILM_URL, wait_until="networkidle")
            page.wait_for_timeout(1500)
            accept_cookies(page)
            page.wait_for_timeout(1000)

            body_text = page.inner_text("body")
            browser.close()

        # 1) Présence Brumath
        brumath_present = CINEMA_KEYWORD.lower() in body_text.lower()
        debug_info["brumath_present"] = brumath_present

        # 2) Signaux réservation
        reservation_keywords = ["réserver", "reserver", "e-billet", "billetterie"]
        reservation_signal = any(k in body_text.lower() for k in reservation_keywords)
        debug_info["reservation_signal"] = reservation_signal

        # 3) Horaires HH:MM
        horaire_pattern = r"\b(?:[01]\d|2[0-3]):[0-5]\d\b"
        horaires = re.findall(horaire_pattern, body_text)
        debug_info["nb_horaires"] = len(horaires)

        available = brumath_present and (reservation_signal or debug_info["nb_horaires"] > 0)

        log(
            f"🔎 brumath_present={debug_info['brumath_present']} | "
            f"reservation_signal={debug_info['reservation_signal']} | "
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
            f"URL: {FILM_URL}\n\n"
            f"Détails:\n"
            f"- brumath_present: {debug['brumath_present']}\n"
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
