# Conception et modèle de menaces

## 1. Objectif

Permettre de s'authentifier sur Linux (`sudo`, écran de verrouillage, connexion
GDM) en regardant une **webcam ordinaire**, sans caméra infrarouge, avec trois
exigences :

1. **Local** : aucune donnée ne quitte la machine, et ce n'est pas une promesse
   mais une impossibilité technique (service sans accès réseau).
2. **Résistant à l'usurpation** : une photo, un écran ou une vidéo de
   l'utilisateur ne doivent pas suffire.
3. **Sûr par construction** : le mot de passe reste toujours disponible, et une
   erreur de Sentinel ne doit jamais bloquer l'accès à la machine.

## 2. Ce qui ne va pas dans l'existant

| Faiblesse constatée | Produit | Réponse de Sentinel |
|---|---|---|
| Une photo imprimée ou sur téléphone suffit avec une webcam classique | Howdy, Gaze ; Biopass fragile sans IR | Détection du vivant en couches : texture (IA), défi aléatoire, reflet de l'écran |
| Des photos de l'utilisateur sont enregistrées lors de l'authentification, réutilisables par un attaquant | Howdy | Aucune image n'est jamais écrite sur le disque |
| Remplacement du gabarit facial par celui de l'attaquant (« Face Swap », « Faceplant », Black Hat 2025) | Windows Hello sans ESS | Gabarits chiffrés et liés au compte (AES-256-GCM avec données associées) |
| Caméra usurpée qui rejoue des images (CVE-2021-34466) | Windows Hello | Caméras virtuelles refusées ; identité de la caméra épinglée à l'enrôlement |
| Trousseau GNOME non déverrouillé après une connexion par le visage | Howdy + GNOME | Règle « mot de passe au démarrage, visage ensuite » (comme Face ID) |
| Interpréteur Python lancé avec les droits root à chaque authentification | Howdy | Module PAM minimal en C ; analyse dans un service isolé (phase 3) |
| Projets sans IR abandonnés | Biopass | — |

## 3. Architecture

```
          sudo / GDM / écran de verrouillage
                       │  PAM
                       ▼
            pam_sentinel.so  (C, minimal)          ← phase 3
                       │  socket Unix, protocole fixe
                       ▼
     sentineld  (service systemd, sans réseau, modèles chargés en mémoire)
        │
        ├─ camera.py    webcam USB uniquement, identité vérifiée
        ├─ vision.py    YuNet (détection) · SFace (empreinte) · MiniFASNet (vivant)
        ├─ auth.py      décision : N images consécutives, chacune reconnue ET vivante
        ├─ store.py     gabarits chiffrés, compteur d'échecs
        └─ models.py    modèles vérifiés par SHA-256 avant chargement
```

### Décision d'authentification

Une image est « utilisable » si un visage de qualité suffisante est détecté (taille,
netteté, éclairage, inclinaison) **et** reconnu (similarité cosinus ≥ 0,50 avec un
gabarit enrôlé).

L'accès est accordé quand **10 images consécutives** sont utilisables **et** que la
**moyenne** de leurs scores de vivant atteint le seuil (−1,0). Une seule image non
reconnue remet la fenêtre à zéro. La moyenne, plutôt qu'un seuil image par image, est
un choix issu des mesures : le score d'un vrai visage est trop bruité sur une webcam
ordinaire, alors que la moyenne sépare nettement vrai visage et attaque (détail dans
[`CALIBRATION.md`](CALIBRATION.md)). Sans succès avant le délai (4 s), PAM passe la
main au mot de passe.

### Modèles d'IA

| Rôle | Modèle | Licence | Taille |
|---|---|---|---|
| Détection + 5 points du visage | YuNet (OpenCV Zoo) | MIT | 0,2 Mo |
| Empreinte (128 dimensions) | SFace (OpenCV Zoo) | Apache-2.0 | 37 Mo |
| Détection du vivant (texture) | MiniFASNet V2 SE, INT8 | Apache-2.0 | 0,6 Mo |

Toutes les licences autorisent un usage commercial. InsightFace et dlib ont été
écartés : poids sous licence non commerciale pour le premier, installation fragile
et modèle ancien pour le second.

Chaque modèle est lu en mémoire, son empreinte SHA-256 comparée au manifeste
(`src/sentinel/models.toml`), puis ce sont **ces octets vérifiés** qui sont chargés.
Il n'y a donc pas d'intervalle entre vérification et utilisation (TOCTOU).

## 4. Modèle de menaces

### Ce que Sentinel doit arrêter

| Attaque | Défense | État |
|---|---|---|
| Photo imprimée | Texture (MiniFASNet) | Phase 2 ✔ |
| Photo ou vidéo sur un écran | Texture + défi aléatoire | Texture ✔, défi : phase 2b |
| Vidéo préenregistrée de l'utilisateur | Défi adaptatif au risque : si le vivant est « limite », tourner la tête dans une direction tirée au sort, avec relief 3D et identité vérifiés pendant le geste | ✔ testé (reflet de couleurs : assistant seulement, à venir) |
| Caméra virtuelle qui diffuse une vidéo | Pilote uvcvideo exigé, identité épinglée | ✔ |
| Gabarit d'un autre compte copié (Face Swap) | AAD liée au nom d'utilisateur | ✔ testé |
| Gabarit fabriqué ailleurs (Faceplant) | Clé locale ; tout gabarit chiffré avec une autre clé est refusé | ✔ testé |
| Modèle d'IA remplacé | Vérification SHA-256 avant chargement | ✔ testé |
| Configuration affaiblie (seuil à 0) | Fichier de configuration exigé root, non modifiable par d'autres | ✔ |
| Essais répétés | Verrouillage après 5 échecs, mot de passe exigé | Phase 3 |
| Fuite de la biométrie | Aucune image stockée ; gabarits chiffrés ; service sans réseau | ✔ / phase 3 |
| Traversée de chemin via le nom d'utilisateur | Validation stricte du nom | ✔ testé |

### Limites assumées

- **Un attaquant déjà root** sur la machine allumée peut obtenir la clé (il peut faire
  ce que fait le service). C'est vrai aussi de Windows Hello sans ESS.
- **Clé protégée par le coffre de systemd** (`systemd-creds`, `LoadCredentialEncrypted`) :
  plus de clé en clair à côté des gabarits. Avec une puce **TPM 2.0**, elle est scellée
  dans la puce (sans lien avec l'état du démarrage, pour survivre aux mises à jour du
  BIOS) : une copie complète du disque ne suffit pas à la déchiffrer ailleurs. Sans TPM
  2.0, elle est liée au secret du système
  (`/var/lib/systemd/credential.secret`). Avant systemd 250 : fichier root 0600.
- **Un jumeau ou un masque 3D réaliste** : hors de portée d'une webcam RGB seule.
  La biométrie faciale reste un facteur de **confort**, pas un remplaçant du mot de
  passe pour les comptes les plus sensibles.
- **Un faux périphérique USB** qui recopie les identifiants de la vraie caméra passe
  le contrôle d'identité ; la détection du vivant reste alors la défense.
- **Exactitude annoncée du modèle de texture** (98,2 % sur CelebA-Spoof) : chiffre
  de l'auteur, sur un jeu de données qui n'est pas ta webcam. La phase 5 mesure les
  taux réels sur ce matériel (ISO/IEC 30107-3 : APCER, BPCER).

## 5. Règles de sécurité de fonctionnement

1. **Le mot de passe reste toujours possible.** Sentinel est déclaré `sufficient`
   dans PAM, jamais `required` : en cas d'échec, de panne ou de délai dépassé, la
   demande de mot de passe s'affiche normalement.
2. **Démarrage : au choix de l'utilisateur.** Par défaut, le premier déverrouillage
   après le démarrage se fait par mot de passe (comme Face ID). Avec l'option
   « connexion au démarrage », Sentinel ouvre la session par le visage, comme Windows
   Hello. Le trousseau GNOME, chiffré par le mot de passe de connexion, est alors
   ouvert grâce au mot de passe conservé **chiffré** (AES-256-GCM, lié au compte),
   rendu uniquement au module PAM après un succès du visage, pour une nouvelle
   session, et tenu à jour à chaque mot de passe saisi (après vérification PAM).
   Limite assumée : un attaquant déjà root peut le déchiffrer ; la puce TPM du PC
   servira de coffre plus solide.
3. **Après 5 échecs consécutifs**, le visage est refusé jusqu'au prochain mot de passe.
4. **Installation progressive** : `sudo` d'abord, avec une session root ouverte en
   secours, puis l'écran de verrouillage, puis GDM.

## 6. Feuille de route

| Phase | Contenu | État |
|---|---|---|
| 1 | Conception, modèle de menaces | ✔ |
| 2 | Moteur : caméra, détection, empreinte, vivant (texture), stockage chiffré, CLI, calibration | ✔ (calibré sur le T570) |
| 2b | Défi adaptatif au risque (tourner la tête, direction tirée au sort, relief + identité) ; reflet de couleurs dans l'assistant | défi ✔, reflet à faire |
| 3a | Service `sentineld` : socket Unix, identité par SO_PEERCRED + logind, mot de passe via PAM, verrouillage, « mot de passe au démarrage » | ✔ |
| 3b | Assistant graphique d'enrôlement (GTK4) : 5 poses, relief, calibration, vérification, confirmation par mot de passe | ✔ (à essayer avec la caméra) |
| 3c | Module `pam_sentinel.so` (C), service systemd isolé (sans réseau, seule la caméra), installateur, activation PAM réversible ; `sudo` | ✔ en usage réel sur le T570 |
| 4 | Écran de verrouillage GNOME (gdm-password) : déverrouillage par le visage, connexion au démarrage toujours par mot de passe | ✔ (1,89 s sur le T570) |
| 4a | Visage dès le démarrage (option) + trousseau ouvert automatiquement ; abandon immédiat si trop sombre / aucun visage ; extension GNOME (écrans de connexion et de verrouillage) : champ du mot de passe masqué pendant la vérification, bouton « utiliser le mot de passe » | ✔ en usage réel sur le T570 (26/11) |
| 4b | Verrouillage automatique en cas d'absence, alerte de regard indiscret | abandonné : le système verrouille déjà après inactivité, et une alerte instantanée exigerait la caméra allumée en permanence |
| 5 | Campagne de tests à plusieurs personnes et webcams : outil `sentinel campagne`, protocole (docs/CAMPAGNE.md), consentement (docs/CONSENTEMENT.md) ; mesures ISO/IEC 30107-3 | outillage ✔, campagne à mener |
| 7 | Paquet .deb unique (Python embarqué) pour GNOME 45+ : Ubuntu 24.04+, Debian 13+, Kali ; testé en conteneur (IA, sudo, mise à jour, retrait) et refus vérifié sur Ubuntu 22.04 / Debian 12 ; activation depuis l'assistant (pkexec) ; écran de verrouillage et connexion essayés sur un vrai PC | ✔ |
| 6 | Clé confiée au coffre systemd, scellée dans la TPM 2.0 ; migration vérifiée ; mise à jour adaptative des gabarits | ✔ validé sur vraie TPM 2.0 (Intel PTT, T570, 26/11) ; gabarits adaptatifs à faire |
