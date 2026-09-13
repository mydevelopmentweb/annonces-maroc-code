"""
Scraper minimal — Concours de recrutement (emploi-public.ma)
--------------------------------------------------------------
Ce script :
1. Récupère la liste des concours depuis la version tableau du site
2. Compare avec les concours déjà connus (fichier data/seen_concours.json)
3. Ajoute les nouveaux concours dans data/a_valider.json (en attente de validation)

Il ne publie rien automatiquement — la validation humaine reste une étape séparée.
"""

import json
import os
import sys
from datetime import datetime

import pandas as pd
import requests

URL = "https://m.emploi-public.ma/fr/concoursListe.asp"

DATA_DIR = "data"
SEEN_FILE = os.path.join(DATA_DIR, "seen_concours.json")
QUEUE_FILE = os.path.join(DATA_DIR, "a_valider.json")

# Colonnes attendues sur la page (dans cet ordre approximatif)
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


def charger_json(chemin, defaut):
    if os.path.exists(chemin):
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    return defaut


def sauvegarder_json(chemin, contenu):
    os.makedirs(os.path.dirname(chemin), exist_ok=True)
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(contenu, f, ensure_ascii=False, indent=2)


def recuperer_tableau():
    """Télécharge la page et retourne le tableau des concours sous forme de liste de dictionnaires."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; AnnoncesMarocBot/1.0; +contact-du-projet)"
    }
    reponse = requests.get(URL, headers=headers, timeout=30)
    reponse.raise_for_status()

    tableaux = pd.read_html(reponse.text)
    if not tableaux:
        raise RuntimeError("Aucun tableau trouvé sur la page — la structure du site a peut-être changé.")

    # On prend le tableau qui a le plus de colonnes en commun avec ce qu'on attend
    meilleur = max(tableaux, key=lambda df: len(set(df.columns) & set(EXPECTED_COLUMNS)))

    if len(set(meilleur.columns) & set(EXPECTED_COLUMNS)) < 3:
        raise RuntimeError(
            "Le tableau trouvé ne correspond pas à ce qu'on attend — vérification manuelle nécessaire."
        )

    return meilleur.fillna("").to_dict(orient="records")


def identifiant_unique(ligne):
    """Construit un identifiant stable pour repérer si un concours a déjà été vu."""
    base = f"{ligne.get('Administration organisatrice', '')}|{ligne.get('Grade', '')}|{ligne.get('Date publication', '')}"
    return base.strip().lower()


def main():
    print(f"[{datetime.now().isoformat()}] Démarrage du scraper concours...")

    try:
        lignes = recuperer_tableau()
    except Exception as erreur:
        print(f"ERREUR pendant la récupération : {erreur}")
        sys.exit(1)

    print(f"{len(lignes)} concours trouvés sur la page.")

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
