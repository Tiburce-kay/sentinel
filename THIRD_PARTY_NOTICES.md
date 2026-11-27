# Composants tiers

Sentinel s'appuie sur les composants suivants. Le paquet `.deb` les inclut avec leurs
licences ; un résumé de cette liste y est installé (`/usr/share/doc/sentinel/THIRD-PARTY`).

## Modèles d'IA (inclus dans le paquet, vérifiés par SHA-256)

| Modèle | Rôle | Source | Licence |
|---|---|---|---|
| YuNet (2023mar) | détection du visage et 5 points | [OpenCV Zoo](https://github.com/opencv/opencv_zoo) | MIT |
| SFace (2021dec) | empreinte faciale 128-d | [OpenCV Zoo](https://github.com/opencv/opencv_zoo) | Apache-2.0 |
| MiniFASNet V2 SE, INT8 | détection du vivant | [facenox/face-antispoof-onnx](https://github.com/facenox/face-antispoof-onnx) | Apache-2.0 |

## Logiciels inclus dans le paquet

| Composant | Rôle | Licence |
|---|---|---|
| CPython 3.12 ([python-build-standalone](https://github.com/astral-sh/python-build-standalone)) | interpréteur du service | PSF-2.0 (CPython) ; scripts de fabrication MPL-2.0 |
| NumPy | calcul | BSD-3-Clause |
| OpenCV (`opencv-python-headless`) | traitement d'image, inférence YuNet et SFace | Apache-2.0 |
| ONNX Runtime | inférence MiniFASNet | MIT |
| cryptography | AES-256-GCM | Apache-2.0 ou BSD-3-Clause |

## Composants du système, non inclus

Linux-PAM, systemd, GTK 4, libadwaita, PyGObject, GNOME Shell et GDM sont fournis par
la distribution et gardent leurs propres licences.

Toutes les licences des composants inclus autorisent un usage commercial. Sentinel
lui-même est distribué sous [PolyForm Noncommercial 1.0.0](LICENSE) : son usage
commercial demande une licence de l'auteur.
