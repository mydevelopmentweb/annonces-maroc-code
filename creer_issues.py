"""
Création d'Issues GitHub pour validation
------------------------------------------
Ce script :
1. Lit les concours en attente de validation (data/a_valider.json)
2. Crée une Issue GitHub pour chacun (si pas déjà fait)
3. Marque chaque concours comme "issue_creee" pour ne pas la recréer en double

Fermer l'Issue sur GitHub = valider ce concours pour publication.
"""

import json
import os
import sys

import requests

DATA_DIR = "data"
QUEUE_FILE = os.path.join(DATA_DIR, "a_valider.json")

# Fournis automatiquement par GitHub Actions, pas besoin de les configurer à la main
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPOSITORY"]  # ex: "mydevelopmentweb/annonces-maroc-code"

API_URL = f"https://api.github.com/repos/{REPO}/issues"
HEADERS = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
}


def charger_json(chemin, defaut):
    if os.path.exists(chemin):
        with open(chemin, "r", encoding="utf-8") as f:
            return json.load(f)
    return defaut


def sauvegarder_json(chemin, contenu):
    with open(chemin, "w", encoding="utf-8") as f:
        json.dump(contenu, f, ensure_ascii=False, indent=2)


def construire_titre(donnees):
    organisme = donnees.get("Administration organisatrice", "").strip() or "Organisme à vérifier"
    poste = donnees.get("Grade", "").strip() or "Poste à vérifier"
    return f"[À valider] {organisme} — {poste}"[:250]


def construire_corps(donnees):
    lignes = [
        "### Nouvelle annonce détectée — merci de vérifier avant de fermer cette Issue",
        "",
        f"- **Organisme** : {donnees.get('Administration organisatrice', '—')}",
        f"- **Poste / Grade** : {donnees.get('Grade', '—')}",
        f"- **Nombre de postes** : {donnees.get('Nombre postes') or 'non précisé'}",
        f"- **Date limite de dépôt** : {donnees.get('Délai dépôt') or 'non précisée'}",
        f"- **Date du concours** : {donnees.get('Date concours') or 'non précisée'}",
        f"- **Lien officiel** : {donnees.get('lien', '—')}",
        "",
        "---",
        "**Pour valider** : si tout est correct, corrige le texte ci-dessus si besoin, puis "
        "clique sur *Close issue*. Le concours sera publié automatiquement sur le site.",
        "",
        "**Pour rejeter** : ferme l'Issue en ajoutant le label `rejete` (ou écris juste "
        "\"rejeté\" en commentaire avant de fermer).",
    ]
    return "\n".join(lignes)


def creer_issue(donnees):
    payload = {
        "title": construire_titre(donnees),
        "body": construire_corps(donnees),
        "labels": ["a-valider"],
    }
    reponse = requests.post(API_URL, headers=HEADERS, json=payload, timeout=30)
    reponse.raise_for_status()
    return reponse.json()["number"]


def main():
    file_attente = charger_json(QUEUE_FILE, [])

    if not file_attente:
        print("Aucun concours en attente — rien à faire.")
        return

    modifie = False
    creees = 0

    for entree in file_attente:
        if entree.get("statut") != "en_attente_validation":
            continue  # déjà traité (issue créée, validé, ou rejeté)

        try:
            numero_issue = creer_issue(entree["donnees"])
        except Exception as erreur:
            print(f"ERREUR lors de la création de l'Issue pour {entree.get('id')} : {erreur}")
            continue

        entree["statut"] = "issue_creee"
        entree["numero_issue"] = numero_issue
        modifie = True
        creees += 1
        print(f"Issue #{numero_issue} créée pour : {entree['donnees'].get('Grade', '')}")

    if modifie:
        sauvegarder_json(QUEUE_FILE, file_attente)

    print(f"{creees} nouvelle(s) Issue(s) créée(s).")


if __name__ == "__main__":
    main()
