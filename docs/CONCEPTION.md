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
| Vidéo préenregistrée de l'utilisateur | Défi aléatoire (cligner, tourner la tête), reflet d'une séquence de couleurs | Phase 2b |
| Caméra virtuelle qui diffuse une vidéo | Pilote uvcvideo exigé, identité épinglée | ✔ |
| Gabarit d'un autre compte copié (Face Swap) | AAD liée au nom d'utilisateur | ✔ testé |
| Gabarit fabriqué ailleurs (Faceplant) | Clé locale ; tout gabarit chiffré avec une autre clé est refusé | ✔ testé |
| Modèle d'IA remplacé | Vérification SHA-256 avant chargement | ✔ testé |
| Configuration affaiblie (seuil à 0) | Fichier de configuration exigé root, non modifiable par d'autres | ✔ |
| Essais répétés | Verrouillage après 5 échecs, mot de passe exigé | Phase 3 |
| Fuite de la biométrie | Aucune image stockée ; gabarits chiffrés ; service sans réseau | ✔ / phase 3 |
| Traversée de chemin via le nom d'utilisateur | Validation stricte du nom | ✔ testé |

### Limites assumées

- **Un attaquant déjà root** peut lire la clé et fabriquer un gabarit. C'est vrai de
  tout système qui garde sa clé sur le disque, y compris Windows Hello sans ESS.
  Prochaine étape : sceller la clé dans la **puce TPM 2.0** du ThinkPad.
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
2. **Premier déverrouillage après démarrage : mot de passe.** Il déverrouille aussi
   le trousseau GNOME. Le visage sert ensuite.
3. **Après 5 échecs consécutifs**, le visage est refusé jusqu'au prochain mot de passe.
4. **Installation progressive** : `sudo` d'abord, avec une session root ouverte en
   secours, puis l'écran de verrouillage, puis GDM.

## 6. Feuille de route

| Phase | Contenu | État |
|---|---|---|
| 1 | Conception, modèle de menaces | ✔ |
| 2 | Moteur : caméra, détection, empreinte, vivant (texture), stockage chiffré, CLI, calibration | ✔ (calibré sur le T570) |
| 2b | Défi aléatoire actif ; reflet d'une séquence de couleurs de l'écran | à faire |
| 3a | Service `sentineld` : socket Unix, identité par SO_PEERCRED + logind, mot de passe via PAM, verrouillage, « mot de passe au démarrage » | ✔ |
| 3b | Assistant graphique d'enrôlement (GTK4) : 5 poses, relief, calibration, vérification, confirmation par mot de passe | ✔ (à essayer avec la caméra) |
| 3c | Module `pam_sentinel.so`, paquet, service systemd isolé ; `sudo` | à faire |
| 4 | Écran de verrouillage, GDM, verrouillage automatique en cas d'absence | à faire |
| 5 | Campagne d'attaques et mesures ISO/IEC 30107-3 | à faire |
| 6 | Clé scellée dans le TPM, mise à jour adaptative des gabarits | à faire |
