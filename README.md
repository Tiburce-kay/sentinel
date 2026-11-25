# Sentinel

**Authentification faciale pour Linux, 100 % locale, résistante à l'usurpation sur une webcam ordinaire.**

Les outils existants (Howdy, Gaze…) se laissent tromper par une photo dès qu'il n'y a
pas de caméra infrarouge, enregistrent parfois des images de l'utilisateur, et
stockent des gabarits qu'un attaquant peut remplacer. Sentinel part de ces faiblesses
documentées : voir [`docs/CONCEPTION.md`](docs/CONCEPTION.md).

> ⚠️ **Projet en développement (phase 2/6).** Le branchement sur `sudo` et l'écran de
> connexion n'est pas encore fait. Le mot de passe restera toujours disponible.

## Ce qui le distingue

- **Détection du vivant** : un modèle d'IA distingue la peau d'une photo ou d'un écran.
  Défi aléatoire et reflet de couleurs de l'écran en préparation.
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

.venv/bin/python -m pytest                    # 59 tests
```

## Modèles

| Rôle | Modèle | Licence |
|---|---|---|
| Détection | YuNet (OpenCV Zoo) | MIT |
| Reconnaissance | SFace (OpenCV Zoo) | Apache-2.0 |
| Vivant | MiniFASNet V2 SE ([facenox/face-antispoof-onnx](https://github.com/facenox/face-antispoof-onnx)) | Apache-2.0 |
