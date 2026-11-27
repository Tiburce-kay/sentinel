# Développer Sentinel

## Organisation du code

```
src/sentinel/
  auth.py         décision : fenêtre de 10 images, défi adaptatif, abandons anticipés
  enrollment.py   enrôlement en 5 poses, relief 3D, calibration du seuil
  vision.py       YuNet (détection), SFace (empreinte), MiniFASNet (vivant), qualité
  geometry.py     pose (lacet, tangage) et parallaxe à partir des 5 points du visage
  camera.py       webcams USB : choix, identité, exposition, lecture continue
  store.py        gabarits chiffrés (AES-256-GCM), clé, compteur d'échecs
  models.py       modèles vérifiés par SHA-256 (models.toml) avant chargement
  daemon.py       service sentineld : socket Unix, autorisations, opérations
  peer.py         identité de l'appelant (SO_PEERCRED, logind)
  pamauth.py      vérification du mot de passe par PAM (service sentinel-confirm)
  pamconf.py      sentinel-pam : activation réversible dans /etc/pam.d
  gui.py          assistant GTK4 / libadwaita (lancé avec le Python du système)
  cli.py          commande sentinel (doctor, enroll, test, calibrate, campagne…)
  calibrate.py    mesures de calibration (nombres seulement)
  campaign.py     campagne de tests à plusieurs personnes
pam/              module pam_sentinel.so (C) et programme de test
packaging/        service systemd, PAM, extension GNOME, réglages GDM, scripts du paquet
scripts/          fabrication et tests des paquets, installation depuis les sources
tests/            tests automatisés (pytest)
docs/             conception, calibration, rapport, guides
```

## Mettre en place l'environnement

```bash
sudo apt install python3-venv gcc make libpam0g-dev python3-gi gir1.2-gtk-4.0 gir1.2-adw-1
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
scripts/fetch_models.sh                        # télécharge et vérifie les modèles
.venv/bin/python -m pytest                     # tests automatisés
```

## Essayer sans rien installer

```bash
export SENTINEL_CONFIG=config.dev.toml         # stockage local, sans root
.venv/bin/sentinel doctor                      # modèles, caméra, droits
.venv/bin/sentinel enroll                      # enregistrer son visage
.venv/bin/sentinel test                        # tester, sans rien déverrouiller
.venv/bin/sentinel calibrate reel              # mesures (docs/CALIBRATION.md)

.venv/bin/sentineld                            # service, terminal 1
scripts/sentinel-setup --socket dev-data/sentinel.sock   # assistant, terminal 2
scripts/sentinel-setup --demo                  # assistant en démonstration, sans caméra
```

## Fabriquer et tester le paquet

```bash
scripts/build-all-debs.sh                      # Docker requis, environ 30 minutes
cat dist/rapport.txt
```

Procédure complète de publication : [docs/MAINTENANCE.md](docs/MAINTENANCE.md).

## Installation depuis les sources (développement)

`sudo scripts/install.sh` installe le service, le module PAM et l'assistant à partir
du dépôt. Il n'installe que les paquets manquants, et s'arrête si des paquets du système
seraient mis à jour ou retirés. Retrait : `sudo scripts/uninstall.sh`. Les utilisateurs
passent par le paquet `.deb` ([docs/INSTALLATION.md](docs/INSTALLATION.md)).

## Licence des contributions

Sentinel est distribué sous [PolyForm Noncommercial 1.0.0](LICENSE), et l'auteur
propose des licences commerciales. Pour que cela reste possible, toute contribution
(code, documentation, modèles) suppose la signature d'un **accord de contribution**
(CLA). Celui-ci laisse à l'auteur le droit de distribuer la contribution sous ces deux
formes. Merci de le demander avant d'ouvrir une demande de fusion.

## Règles du projet

- **Le mot de passe doit toujours rester possible** : tout changement touchant PAM doit
  être couvert par `tests/test_pam_module.py`, qui utilise une vraie pile PAM.
- **Aucune image écrite sur le disque**, et aucune donnée personnelle dans les journaux,
  seulement des mesures.
- **Un changement de modèle ou de règle de décision** demande une nouvelle calibration
  documentée dans `docs/CALIBRATION.md`.
- **Messages en français**, clairs pour un utilisateur sans connaissance technique.
- Chaque correctif d'un bug réel s'accompagne d'un test qui le reproduit.
