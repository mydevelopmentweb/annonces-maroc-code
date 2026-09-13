"""
Scraper minimal — Concours de recrutement (emploi-public.ma)
--------------------------------------------------------------
Ce script :
1. Récupère la liste des concours (essaie plusieurs pages/méthodes en cas de blocage)
2. Compare avec les concours déjà connus (fichier data/seen_concours.json)
3. Ajoute les nouveaux concours dans data/a_valider.json (en attente de validation)

Il ne publie rien automatiquement — la validation humaine reste une étape séparée.
"""

import json
import os
import re
import sys
import time
from datetime import datetime

import pandas as pd
import requests
import urllib3
from bs4 import BeautifulSoup

# Le site source a un certificat SSL mal configuré (auto-signé) sur certaines
# pages — on désactive la vérification stricte, sans risque ici car on ne fait
# que lire des pages publiques (aucune donnée sensible envoyée).
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

URL_MOBILE_TABLEAU = "https://m.emploi-public.ma/fr/concoursListe.asp"
URL_DESKTOP_LISTE = "https://www.emploi-public.ma/fr/concours-liste"
URL_ACCUEIL = "https://www.emploi-public.ma/fr/"

DATA_DIR = "data"
SEEN_FILE = os.path.join(DATA_DIR, "seen_concours.json")
QUEUE_FILE = os.path.join(DATA_DIR, "a_valider.json")

EXPECTED_COLUMNS = [
    "Administration organisatrice",
    "Grade",
    "Nombre postes",
    "Délai dépôt",
    "Date concours",
    "Date publication",
    "Candidats convoqués pour l'examen écrit",
    "Candidats convoqués pour l'entretien oral",
    "Résultats",
    "Désistements",
]

HEADERS_NAVIGATEUR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "fr-FR,fr;q=0.9,ar;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}


def charger_json(chemin, defaut):
    if os.path.exists(chemin):
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    return defaut


def sauvegarder_json(chemin, contenu):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(contenu, f, ensure_ascii=False, indent=2)


def nouvelle_session():
    """Crée une session qui visite d'abord la page d'accueil (comme un vrai
    visiteur), avant de demander la page cible — certains sites bloquent les
    requêtes qui arrivent directement sans ce passage."""
    session = requests.Session()
    session.headers.update(HEADERS_NAVIGATEUR)
    try:
        session.get(URL_ACCUEIL, timeout=30, verify=False)
        time.sleep(1)
    except requests.RequestException:
        pass  # si l'accueil échoue, on tente quand même la page cible
    return session


def essayer_avec_reprises(fonction, tentatives=3, attente=5):
    derniere_erreur = None
    for essai in range(1, tentatives + 1):
        try:
            return fonction()
        except Exception as erreur:
            derniere_erreur = erreur
            print(f"  Tentative {essai}/{tentatives} échouée : {erreur}")
            if essai < tentatives:
                time.sleep(attente)
    raise derniere_erreur


def recuperer_via_tableau_mobile(session):
    """Méthode 1 : version tableau (mobile), la plus simple à lire."""
    reponse = session.get(URL_MOBILE_TABLEAU, timeout=30, verify=False)
    reponse.raise_for_status()

    tableaux = pd.read_html(reponse.text)
    if not tableaux:
        raise RuntimeError("Aucun tableau trouvé sur la page mobile.")

    meilleur = max(tableaux, key=lambda df: len(set(df.columns) & set(EXPECTED_COLUMNS)))
    if len(set(meilleur.columns) & set(EXPECTED_COLUMNS)) < 3:
        raise RuntimeError("Le tableau mobile ne correspond pas à la structure attendue.")

    return meilleur.fillna("").to_dict(orient="records")


def recuperer_via_liste_desktop(session):
    """Méthode 2 (repli) : version desktop, en cas de blocage de la version mobile."""
    reponse = session.get(URL_DESKTOP_LISTE, timeout=30, verify=False)
    reponse.raise_for_status()

    soup = BeautifulSoup(reponse.text, "html.parser")
    liens = soup.find_all("a", href=re.compile(r"/concours/details/"))
    if not liens:
        raise RuntimeError("Aucun lien de concours trouvé sur la page desktop.")

    resultats = []
    for lien in liens:
        bloc_texte = lien.get_text(separator=" | ", strip=True)
        bloc_texte = re.sub(r"\s+", " ", bloc_texte)  # nettoie les retours à la ligne/espaces multiples
        if not bloc_texte:
            continue

        parties = [p.strip() for p in bloc_texte.split(" | ") if p.strip()]

        limite = re.search(r"Limite de dépôt\s*:\s*([^|]+)", bloc_texte)
        date_concours = re.search(r"Date du concours\s*:\s*([^|]+)", bloc_texte)
        postes = re.search(r"Annonce\s*(\d+)\s*poste", bloc_texte)

        # Le titre du poste est généralement répété au début (une fois en gras, une fois en texte
        # normal) ; on déduplique, puis on suppose que la première partie restante est le titre,
        # et la deuxième (si elle ne contient pas "Limite"/"Date"/"Annonce") est l'organisme.
        titre = parties[0] if parties else ""
        organisme = ""
        motif_badge = re.compile(r"^\d+\s*jours?\s*restants?$", re.IGNORECASE)
        for partie in parties[1:]:
            if any(mot in partie for mot in ["Limite de dépôt", "Date du concours", "Annonce"]):
                break
            if motif_badge.match(partie.strip()):
                continue  # ignore les badges type "2 jours restants"
            if partie.strip().lower() != titre.strip().lower():
                organisme = partie
                break

        def nettoyer_date(texte_date):
            if not texte_date:
                return ""
            return re.sub(r"\s+", " ", texte_date).strip()

        resultats.append({
            "Administration organisatrice": organisme or "À vérifier manuellement",
            "Grade": titre[:300],
            "Nombre postes": postes.group(1) if postes else "",
            "Délai dépôt": nettoyer_date(limite.group(1) if limite else ""),
            "Date concours": nettoyer_date(date_concours.group(1) if date_concours else ""),
            "Date publication": "",
            "lien": "https://www.emploi-public.ma" + lien["href"] if lien["href"].startswith("/") else lien["href"],
        })

    if not resultats:
        raise RuntimeError("Impossible d'extraire des données exploitables des liens trouvés.")

    return resultats


def recuperer_concours():
    session = nouvelle_session()

    print("Tentative via la version tableau (mobile)...")
    try:
        return essayer_avec_reprises(lambda: recuperer_via_tableau_mobile(session))
    except Exception as erreur_mobile:
        print(f"Méthode mobile indisponible : {erreur_mobile}")
        print("Tentative via la version liste (desktop)...")
        return essayer_avec_reprises(lambda: recuperer_via_liste_desktop(session))


def identifiant_unique(ligne):
    base = (
        f"{ligne.get('Administration organisatrice', '')}|"
        f"{ligne.get('Grade', '')}|"
        f"{ligne.get('Date publication', '') or ligne.get('lien', '')}"
    )
    return base.strip().lower()


def main():
    print(f"[{datetime.now().isoformat()}] Démarrage du scraper concours...")

    try:
        lignes = recuperer_concours()
    except Exception as erreur:
        print(f"ERREUR pendant la récupération (les deux méthodes ont échoué) : {erreur}")
        sys.exit(1)

    print(f"{len(lignes)} concours trouvés.")

    deja_vus = set(charger_json(SEEN_FILE, []))
    file_attente = charger_json(QUEUE_FILE, [])

    nouveaux = 0
    for ligne in lignes:
        uid = identifiant_unique(ligne)
        if uid in deja_vus:
            continue

        deja_vus.add(uid)
        file_attente.append({
            "id": uid,
            "date_detection": datetime.now().isoformat(),
            "statut": "en_attente_validation",
            "donnees": ligne,
        })
        nouveaux += 1

    sauvegarder_json(SEEN_FILE, list(deja_vus))
    sauvegarder_json(QUEUE_FILE, file_attente)

    print(f"{nouveaux} nouveau(x) concours ajouté(s) à la file d'attente de validation.")

    if nouveaux == 0:
        print("Aucune nouveauté — comportement normal si rien n'a changé depuis le dernier passage.")


if __name__ == "__main__":
    main()
