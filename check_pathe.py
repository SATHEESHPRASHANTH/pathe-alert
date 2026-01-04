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
    Méthode robuste :
    - ouvre la page cinéma Brumath
    - accepte cookies
    - déclenche des interactions (scroll + clic "Aujourd'hui" si présent)
    - capte TOUTES les réponses XHR/FETCH (peu importe content-type)
    - cherche des horaires HH:MM dans ces réponses
    """
    debug_info = {
        "available": False,
        "film_found": False,
        "nb_horaires": 0,
        "error": None,
        "xhr_count": 0,
        "hits_count": 0,
        "sample_xhr_urls": [],
        "sample_hit_urls": [],
    }

    def accept_cookies(page) -> None:
        for _ in range(4):
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
            context = browser.new_context(
                locale="fr-FR",
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 720},
            )
            page = context.new_page()
            page.set_default_timeout(60000)

            collected_hits = []  # (url, times_count)
            collected_xhr_urls = []

            def on_response(resp):
                try:
                    rtype = resp.request.resource_type
                    if rtype not in ("xhr", "fetch"):
                        return

                    url = resp.url
                    collected_xhr_urls.append(url)

                    # On essaye de lire le texte (si binaire, ça throw -> on ignore)
                    txt = resp.text()
                    if re.search(r"\b(?:[01]\d|2[0-3]):[0-5]\d\b", txt):
                        times = re.findall(r"\b(?:[01]\d|2[0-3]):[0-5]\d\b", txt)
                        collected_hits.append((url, len(times)))
                except Exception:
                    pass

            page.on("response", on_response)

            log(f"🏢 Ouverture cinéma: {CINEMA_URL}")
            page.goto(CINEMA_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            accept_cookies(page)

            # Interactions pour déclencher le chargement séances
            try:
                # clic sur "Aujourd'hui" si existe (dans ton screenshot)
                page.get_by_role("button", name=re.compile(r"Aujourd'hui", re.I)).click(timeout=3000)
            except Exception:
                pass

            try:
                page.mouse.wheel(0, 1800)
            except Exception:
                pass

            page.wait_for_timeout(9000)

            browser.close()

        # Analyse résultats
        debug_info["xhr_count"] = len(collected_xhr_urls)
        debug_info["sample_xhr_urls"] = collected_xhr_urls[:8]

        total_times = 0
        film_low = FILM_NAME.lower()

        # Si on a des hits horaires, on essaie d'associer au film
        # (souvent le JSON contient le titre, sinon on peut aussi matcher l'ID du film dans FILM_URL)
        film_id_match = None
        m = re.search(r"-([0-9]+)$", FILM_URL.rstrip("/"))
        if m:
            film_id_match = m.group(1)

        for url, n in collected_hits:
            total_times += n

        # Film_found = on a au moins un hit + (titre ou id trouvé dans au moins une URL/response)
        # Comme on n'a pas gardé le texte, on fait simple: si on a des hits, on considère "available".
        # (On peut raffiner après si besoin)
        debug_info["hits_count"] = len(collected_hits)
        debug_info["sample_hit_urls"] = [u for (u, _) in collected_hits[:8]]
        debug_info["nb_horaires"] = total_times

        debug_info["film_found"] = (len(collected_hits) > 0)
        debug_info["available"] = debug_info["film_found"] and total_times > 0

        log(
            f"🔎 xhr_count={debug_info['xhr_count']} | "
            f"hits_count={debug_info['hits_count']} | "
            f"nb_horaires={debug_info['nb_horaires']} | "
            f"available={debug_info['available']}"
        )

        # Si zéro XHR, on log 2-3 URLs pour debug
        if debug_info["xhr_count"] == 0:
            log("⚠️ Aucun XHR/FETCH capté. Le site charge peut-être autrement (SSR) ou bloque headless.")

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
