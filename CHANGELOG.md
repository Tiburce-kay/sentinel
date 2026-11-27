# Historique des versions

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) ;
numérotation [SemVer](https://semver.org/lang/fr/).

## [0.2.5] — 2026-11-27

### Corrigé
- **Installer Sentinel sur un PC sans GNOME tirait GNOME Shell et GDM.** Signalé sous
  Kubuntu (KDE Plasma) : le paquet recommandait `gnome-shell`, et `apt` installe les
  recommandations par défaut, soit 853 paquets au lieu de 155, écran de connexion de
  GNOME compris. La recommandation est retirée, et le test du paquet vérifie qu'aucun
  bureau ni écran de connexion n'est installé.
- Assistant, sur un autre bureau que GNOME : le message nomme le bureau (« Pas encore
  disponible avec KDE Plasma ») et précise qu'aucune installation supplémentaire n'est
  nécessaire. L'ancien texte laissait croire qu'il fallait installer GNOME.

### Changé
- **Message précis quand le visage est vu mais inexploitable.** Avant, l'utilisateur
  lisait « aucun visage détecté » alors que la caméra le voyait. Il lit désormais le
  motif et un conseil : « visage trop loin : rapprochez-vous de la caméra », « image
  floue : restez immobile un instant », etc. Le journal compte les images écartées par
  motif (`rejets=`). Observé sur un vrai PC le 27/11 : 34 images en 4 s, aucune analysée.

## [0.2.4] — 2026-11-27 — paquet retiré, remplacé par la 0.2.5

### Corrigé
- **Clé non placée dans la puce TPM lors d'une première installation.** Observé sur un
  vrai PC : la TPM a mis 4,2 s à ouvrir la clé, et le contrôle, lancé trop tôt, a annulé
  la protection. La clé restait dans un fichier réservé à root, sans aucune perte. Le
  contrôle attend désormais que le service réponde (jusqu'à 60 s), et s'arrête tout de
  suite si le service échoue.

## [0.2.3] — 2026-11-27 — paquet retiré, remplacé par la 0.2.4

### Ajouté
- **Licence PolyForm Noncommercial 1.0.0** : usage gratuit à titre personnel, pour la
  recherche, l'enseignement et les organismes sans but lucratif ; usage commercial sous
  licence de l'auteur. Le texte de la licence est installé avec le paquet
  (`/usr/share/doc/sentinel/copyright`).

### Changé
- **Un seul mot de passe dans l'assistant.** Le paquet branche Sentinel dans PAM à
  l'installation. C'est sans effet tant qu'un compte n'a ni visage enregistré ni usage
  choisi, et chaque utilisateur active ses usages avec son seul mot de passe. La fenêtre
  de mot de passe administrateur n'apparaît plus que si un administrateur a désactivé
  Sentinel pour tout le PC (`sentinel-pam sync --sudo off --screen off`, choix conservé
  lors des mises à jour).

### Corrigé
- La purge efface aussi les copies des fichiers PAM d'origine (`/etc/pam.d/*.sentinel-backup`),
  sauf si un fichier contient encore une ligne de Sentinel.

## [0.2.2] — 2026-11-27 — paquet retiré (publié sans licence), remplacé par la 0.2.3

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

## [0.2.0] — 2026-11-27 — ⚠️ paquet retiré (perte de clé à la réinstallation)

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

[0.2.5]: https://github.com/Tiburce-kay/sentinel/releases/tag/v0.2.5
[0.2.4]: https://github.com/Tiburce-kay/sentinel/tree/v0.2.4
[0.2.3]: https://github.com/Tiburce-kay/sentinel/tree/v0.2.3
[0.2.2]: https://github.com/Tiburce-kay/sentinel/tree/v0.2.2
[0.2.0]: https://github.com/Tiburce-kay/sentinel/tree/v0.2.0
