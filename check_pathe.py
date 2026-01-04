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
FILM_NAME = "Avatar : de feu et de cendres"
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
    Méthode fiable: on ouvre la page et on capte les réponses JSON (API)
    qui contiennent les séances. Ensuite on cherche des horaires HH:MM.
    """
    debug_info = {
        "available": False,
        "film_found": False,
        "nb_horaires": 0,
        "error": None,
        "matched_json_urls": [],
    }

    def accept_cookies(page) -> None:
        for _ in range(3):
            for txt in ["Tout accepter", "Accepter", "J'accepte", "OK", "Continuer"]:
                try:
                    page.get_by_role("button", name=re.compile(txt, re.I)).click(timeout=1500)
                    log("🍪 Cookies acceptés/fermés")
                    page.wait_for_timeout(400)
                    return
                except Exception:
                    pass
            page.wait_for_timeout(700)

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(locale="fr-FR")
            page = context.new_page()
            page.set_default_timeout(60000)

            collected = []  # liste de tuples (url, text)

            def on_response(resp):
                try:
                    ct = resp.headers.get("content-type", "")
                    if "application/json" not in ct:
                        return
                    url = resp.url
                    # On filtre un peu pour éviter trop de bruit
                    if "pathe.fr" not in url:
                        return
                    txt = resp.text()
                    # On garde seulement les JSON qui contiennent au moins un horaire
                    if re.search(r"\b(?:[01]\d|2[0-3]):[0-5]\d\b", txt):
                        collected.append((url, txt))
                except Exception:
                    pass

            page.on("response", on_response)

            log(f"🏢 Ouverture cinéma: {CINEMA_URL}")
            page.goto(CINEMA_URL, wait_until="networkidle")
            page.wait_for_timeout(2500)
            accept_cookies(page)

            # Laisser du temps au JS pour charger les séances + API
            page.wait_for_timeout(7000)

            # Si rien capté, on force un scroll (souvent déclenche un fetch)
            if not collected:
                try:
                    page.mouse.wheel(0, 2000)
                    page.wait_for_timeout(4000)
                except Exception:
                    pass

            browser.close()

        # Analyse des JSON captés
        film_low = FILM_NAME.lower()
        total_times = 0

        for url, txt in collected:
            low = txt.lower()
            # Le film peut apparaître soit par titre, soit par l'ID du film dans l'URL (11387)
            film_match = (film_low in low) or ("11387" in low)  # pour Avatar test
            if film_match:
                times = re.findall(r"\b(?:[01]\d|2[0-3]):[0-5]\d\b", txt)
                if times:
                    debug_info["film_found"] = True
                    total_times += len(times)
                    debug_info["matched_json_urls"].append(url)

        debug_info["nb_horaires"] = total_times
        debug_info["available"] = debug_info["film_found"] and total_times > 0

        log(
            f"🔎 film_found={debug_info['film_found']} | "
            f"nb_horaires={debug_info['nb_horaires']} | "
            f"json_hits={len(debug_info['matched_json_urls'])} | "
            f"available={debug_info['available']}"
        )

        return debug_info["available"], debug_info

    except PlaywrightTimeoutError as e:
        debug_info["error"] = f"Timeout Playwright: {e}"
        log(f"❌ {debug_info['error']}")
        return False, debug_info
    except Exception as e:
        debug_info["error"] = f"Erreur scraping/API: {e}"
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
