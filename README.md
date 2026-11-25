# Sentinel

**Authentification faciale pour Linux, 100 % locale, résistante à l'usurpation sur une webcam ordinaire.**

Les outils existants (Howdy, Gaze…) se laissent tromper par une photo dès qu'il n'y a
pas de caméra infrarouge, enregistrent parfois des images de l'utilisateur, et
stockent des gabarits qu'un attaquant peut remplacer. Sentinel part de ces faiblesses
documentées : voir [`docs/CONCEPTION.md`](docs/CONCEPTION.md).

> ⚠️ **Projet en développement.** Moteur, service et assistant graphique sont prêts ; le
> branchement sur `sudo` et l'écran de verrouillage est l'étape suivante. Le mot de passe
> restera toujours disponible.

| Accueil | Enregistrement guidé | Choix et confirmation |
|---|---|---|
| ![](docs/images/assistant-welcome.png) | ![](docs/images/assistant-capture.png) | ![](docs/images/assistant-confirm.png) |

## Ce qui le distingue

- **Détection du vivant** : un modèle d'IA distingue la peau d'une photo ou d'un écran,
  décidée sur une moyenne glissante calibrée sur du matériel réel.
- **Enregistrement guidé en 5 poses** (face, gauche, droite, haut, bas) qui mesure le
  **relief 3D** du visage : une photo, plate, ne peut pas être enregistrée.
- **Calibration automatique** du seuil pour chaque personne et chaque webcam, toujours
  bornée par un plancher de sécurité.
- **Aucune image stockée.** Uniquement des empreintes numériques chiffrées en AES-256-GCM,
  liées au compte : un gabarit déplacé, modifié ou fabriqué ailleurs est refusé.
- **Caméra vérifiée** : caméras virtuelles refusées, identité de la webcam épinglée.
- **Modèles d'IA vérifiés** par SHA-256 avant chargement.
- **Licences compatibles avec un usage commercial** (MIT, Apache-2.0).

## Essai (mode développement)

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
scripts/fetch_models.sh                       # télécharge et vérifie les modèles

export SENTINEL_CONFIG=config.dev.toml        # stockage local, sans root
.venv/bin/sentinel init
.venv/bin/sentinel doctor                     # vérifie modèles, caméra, droits
.venv/bin/sentinel enroll                     # enregistre votre visage
.venv/bin/sentinel test                       # teste, sans rien déverrouiller
.venv/bin/sentinel calibrate reel             # mesure les scores (voir docs/CALIBRATION.md)

.venv/bin/python -m pytest                    # 90 tests

# Service + assistant graphique (deux terminaux)
.venv/bin/sentineld                           # terminal 1
scripts/sentinel-setup --socket dev-data/sentinel.sock   # terminal 2
scripts/sentinel-setup --demo                 # démonstration, sans caméra
```

## Modèles

| Rôle | Modèle | Licence |
|---|---|---|
| Détection | YuNet (OpenCV Zoo) | MIT |
| Reconnaissance | SFace (OpenCV Zoo) | Apache-2.0 |
| Vivant | MiniFASNet V2 SE ([facenox/face-antispoof-onnx](https://github.com/facenox/face-antispoof-onnx)) | Apache-2.0 |
