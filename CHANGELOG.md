# Historique des versions

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ;
numérotation [SemVer](https://semver.org/lang/fr/).

## [0.2.2] — 2026-11-27

### Changé
- **Un seul paquet `.deb`**, réservé aux distributions livrées avec GNOME 45 ou plus
  récent : Ubuntu 24.04+, Debian 13+, Kali. Le paquet embarque son propre Python
  (python-build-standalone 3.12, épinglé par SHA-256) et ne dépend plus du Python de la
  distribution. `apt` refuse l'installation sur Ubuntu 22.04, Debian 12 et les versions
  plus anciennes (`libc6 >= 2.39`, `Breaks: gnome-shell (<< 45)`).
- Plusieurs webcams : Sentinel les essaie dans l'ordre (celle de l'enrôlement d'abord,
  les caméras infrarouges en dernier) et passe une webcam en panne ou muette.
- Abandon après 1,5 s sans visage d'affilée, y compris quand un visage s'en va. Un
  imposteur qui se retire tôt reste compté comme un échec.
- Assistant : les options d'écran sont grisées et expliquées sans écran de connexion
  GNOME (KDE, Xfce, Cinnamon).

### Ajouté
- `sentinel campagne` : campagne de tests à plusieurs personnes et webcams (BPCER, APCER
  par type d'attaque, FAR, par groupe et par webcam), protocole `docs/CAMPAGNE.md`,
  formulaire `docs/CONSENTEMENT.md`.
- Journal : tentatives refusées et verrouillages au niveau avertissement
  (`journalctl -u sentineld -p warning`).
- `scripts/check-gnome-compat.sh` : contrôle d'une nouvelle version de GNOME Shell
  (`docs/MAINTENANCE.md`).
- Tests :
  - IA réellement exécutée dans chaque paquet ;
  - mise à jour du paquet ;
  - 14 cas de secours par mot de passe ;
  - module PAM supprimé mais ligne restée ;
  - deux comptes sur le même PC ;
  - systemd trop ancien.
- Documentation : guide d'installation, rapport technique, maintenance, contribution,
  sécurité, citation.

## [0.2.1] — 2026-11-27 (non publiée)

### Corrigé
- **Perte de la clé de chiffrement à la réinstallation.** Installer le paquet après une
  désinstallation qui conservait les données pouvait faire créer une nouvelle clé par le
  service, puis la sceller à la place de la bonne : le visage enregistré devenait
  illisible. Désormais :
  - aucune clé n'est créée là où des gabarits ou un coffre existent ;
  - la clé du coffre n'est jamais remplacée ;
  - chaque changement est vérifié avant tout effacement ;
  - un gabarit illisible est signalé dans l'assistant, qui propose de réenregistrer
    le visage.

## [0.2.0] — 2026-11-27 — ⚠️ obsolète, ne plus utiliser

### Ajouté
- Paquets `.deb` par distribution, activation depuis l'assistant par la fenêtre
  d'administration (pkexec), sans terminal.
- Clé de chiffrement confiée au coffre de systemd, scellée dans la puce TPM 2.0 quand
  elle est présente.
- Connexion au démarrage par le visage (option), avec ouverture du trousseau GNOME.
- Extension GNOME : champ du mot de passe masqué pendant la vérification, bouton clé
  « Utiliser le mot de passe », aux écrans de connexion et de verrouillage.
- Défi adaptatif au risque : tourner la tête dans une direction tirée au sort.

## [0.1.0] — 2026-11-25 (non publiée)

### Ajouté
- Moteur : détection YuNet, empreinte SFace, vivant MiniFASNet, décision sur la moyenne
  de 10 images calibrée sur du matériel réel.
- Enrôlement guidé en 5 poses avec contrôle du relief 3D et calibration bornée.
- Service `sentineld` isolé, module PAM en C, `sudo` et écran de verrouillage.
- Assistant graphique GTK4 / libadwaita.

[0.2.2]: https://github.com/Tiburce-kay/sentinel/releases/tag/v0.2.2
[0.2.0]: https://github.com/Tiburce-kay/sentinel/releases/tag/v0.2.0
