# Sentinel : authentification faciale locale et résistante à l'usurpation sur webcam ordinaire

*Rapport technique, version 0.2.3 (novembre 2026).*

## Résumé

L'authentification faciale sur PC repose aujourd'hui soit sur du matériel spécialisé
(caméras infrarouges de Windows Hello, capteur de profondeur de Face ID), soit, sous
Linux, sur des outils qui se laissent tromper par une simple photo dès que la caméra
est une webcam ordinaire. Sentinel est un système d'authentification faciale pour Linux
qui vise une **résistance à l'usurpation avec une webcam RGB ordinaire**, en
fonctionnant **entièrement en local**.

Il combine quatre éléments :
- une **détection du vivant par texture** (réseau MiniFASNet), décidée sur une moyenne
  glissante de 10 images plutôt qu'image par image ;
- un **défi adaptatif au risque** : tourner la tête dans une direction tirée au sort,
  avec contrôle du relief 3D, demandé uniquement quand le score est ambigu ;
- un **enrôlement en 5 poses** qui mesure le relief du visage ;
- une **architecture à privilèges séparés** : un module PAM minimal en C, et un service
  isolé sans accès réseau dont les gabarits sont chiffrés et la clé scellée dans la
  puce TPM 2.0.

Sur le matériel de développement (ThinkPad T570, webcam 640×480 sans infrarouge),
aucune des 324 images de photo présentée sur téléphone ni la vidéo n'ont produit de
fenêtre acceptée, et aucune autre personne n'a été reconnue (similarité ≤ 0,32 contre
≥ 0,69 pour l'utilisateur). L'utilisateur est authentifié en **1,2 à 1,4 s**. Ces
résultats portent sur **une seule personne et une seule webcam**. Un protocole de
campagne à plusieurs personnes (ISO/IEC 30107-3) et son outillage sont fournis pour
les généraliser.

## 1. Problème

S'authentifier en regardant l'écran est plus rapide que saisir un mot de passe, et
rend le mot de passe moins exposé à l'observation. Sur un PC Linux équipé d'une webcam
ordinaire, cette commodité se paie aujourd'hui par une sécurité faible :

1. **Usurpation par photo ou vidéo.** Sans capteur infrarouge ni de profondeur, une
   simple reconnaissance du visage accepte une photo de l'utilisateur.
2. **Fuite de la biométrie.** Certains outils enregistrent des images du visage à
   chaque essai.
3. **Surface d'attaque en root.** L'analyse d'images, avec ses bibliothèques complexes,
   s'exécute souvent directement dans la pile PAM, avec les droits root.
4. **Gabarits remplaçables.** Une empreinte faciale non liée au compte peut être
   remplacée par celle d'un attaquant.

**Objectif :** une authentification faciale qui reste sûre avec une webcam ordinaire,
ne fait sortir aucune donnée de la machine, et ne bloque jamais l'accès par mot de passe.

## 2. Travaux existants

| Système | Capteur | Vivant | Faiblesses documentées |
|---|---|---|---|
| Windows Hello | caméra infrarouge | image infrarouge | caméra usurpée qui rejoue des images (CVE-2021-34466) ; remplacement du gabarit sans *Enhanced Sign-in Security* (Black Hat 2025) |
| Face ID (Apple) | projecteur de points, profondeur | relief | matériel dédié, hors PC Linux |
| Howdy (Linux) | webcam ou infrarouge | aucun avec une webcam RGB | photo acceptée ; images enregistrées ; Python exécuté en root dans PAM |
| Biopass, Gaze (Linux) | webcam | limité ou absent | projets peu maintenus |

Le détail par faiblesse et la réponse de Sentinel sont dans [CONCEPTION.md](CONCEPTION.md#2-ce-qui-ne-va-pas-dans-lexistant).

## 3. Contributions

1. **Règle de décision fondée sur des mesures** : la moyenne du score de vivant sur une
   fenêtre de 10 images consécutives reconnues. Sur le matériel de mesure, elle fait
   passer l'acceptation de l'utilisateur de 8,6 % à 87,3 % des fenêtres, avec 0 %
   d'attaques acceptées (section 5.2).
2. **Défi adaptatif au risque** : le défi n'est demandé que dans une zone de score
   ambiguë, ce qui garde la plupart des authentifications sans geste. Il vérifie la
   direction, le relief (parallaxe du nez) et l'identité pendant le mouvement.
3. **Enrôlement mesurant le relief 3D** en 5 poses, avec calibration personnelle du
   seuil bornée par un plancher de sécurité : la calibration peut assouplir le seuil
   pour une webcam difficile, jamais le rendre plus strict que le seuil validé.
4. **Architecture sûre par construction** : module PAM en C, service isolé par systemd
   (sans réseau, seule la caméra est accessible), gabarits chiffrés et liés au compte,
   clé scellée dans la TPM 2.0. Le mot de passe reste toujours disponible.
5. **Intégration à l'écran de connexion GNOME** : le champ du mot de passe est masqué
   pendant la vérification, un bouton permet de passer au mot de passe, et le trousseau
   s'ouvre lors d'une connexion par le visage.

## 4. Méthode

### 4.1 Chaîne de traitement

Pour chaque image (640×480) :

1. **Détection** : YuNet [1], sur une image réduite à 320 px de large, renvoie le
   visage et 5 points (yeux, nez, coins de la bouche).
2. **Qualité** : taille, netteté, luminosité et inclinaison. Pose estimée à partir des
   5 points : lacet par l'asymétrie du nez, tangage par le rapport des distances
   nez–yeux et bouche–yeux. Une tête trop tournée par rapport à la pose de référence
   est ignorée, plutôt que jugée.
3. **Empreinte** : SFace [2], vecteur de 128 dimensions normalisé, comparé aux gabarits
   par similarité cosinus (seuil 0,50).
4. **Vivant** : MiniFASNet V2 SE quantifié en INT8 [3], sur un recadrage élargi de 1,5
   fois et mis à 128×128. Le score est la différence des logits « vrai » et « faux ».

Les étapes 3 et 4 s'exécutent en parallèle.

### 4.2 Décision

L'accès est accordé quand **10 images consécutives** sont reconnues et que la
**moyenne** de leurs scores de vivant atteint le seuil *t*. Une image non reconnue
remet la fenêtre à zéro, ce qui empêche de mélanger des images de deux personnes.

Le seuil *t* est personnel : *t* = clip(p10 − 0,5, −2,0, −1,0), où p10 est le 10e
centile des moyennes de fenêtre mesurées pendant l'enrôlement.

**Défi adaptatif.** Si la moyenne tombe dans [*t*, *t* + 1], ou dans la zone grise
[max(*t* − 1, −2,5), *t*) pendant plus de 1,5 s, Sentinel demande de tourner la tête vers
une direction tirée au sort (`secrets.choice`). Le défi est réussi si, en 3 s au plus :
- le lacet change dans la bonne direction ;
- la parallaxe du nez dépasse 0,05 : le nez se déplace par rapport au plan
  yeux–bouche, ce qui est impossible pour une image plate ;
- l'identité reste reconnue pendant tout le geste.

Une mauvaise direction n'est retenue que si elle est nette (1,5 fois l'amplitude
requise) sur deux images consécutives.

**Abandons anticipés**, pour que le mot de passe arrive vite :
- 1,0 s d'images trop sombres ;
- 1,5 s sans visage d'affilée ;
- demande de l'utilisateur (bouton clé).

Un visage qui s'en va ne permet pas d'échapper au compteur d'échecs : le statut final
reste établi sur ce qui a été vu.

### 4.3 Enrôlement

L'utilisateur se place de face, puis tourne la tête à gauche, à droite, vers le haut et
vers le bas, avec un retour de face entre chaque pose. La position de référence est la
médiane de 5 images de face. L'identité est ancrée sur la première image : une autre
personne ne peut pas compléter l'enrôlement.

Le relief mesuré (parallaxe) doit dépasser 0,05, sinon l'enrôlement est refusé : une
photo ne peut pas être enregistrée. Pendant l'enrôlement, l'écran devient blanc pour
éclairer le visage.

## 5. Évaluation

### 5.1 Protocole

Matériel : Lenovo ThinkPad T570, Intel Core i5-6300U, webcam intégrée 04f2:b5ab
(640×480, sans infrarouge). Les mesures ont été faites avec `sentinel calibrate`, qui
n'enregistre que des nombres. Attaques : photo et vidéo de l'utilisateur sur un
téléphone, présentées à des distances et des angles variés. Données complètes :
[CALIBRATION.md](CALIBRATION.md).

### 5.2 Séparation des populations

| Population | Images | Vivant (p5 / médiane / p95) | Similarité |
|---|---|---|---|
| Utilisateur | 72 | −3,02 / +0,51 / +3,41 | 0,81 – 0,91 |
| Autre personne | 33 | −2,03 / +0,48 / +1,71 | 0,15 – 0,32 |
| Photo sur téléphone | 324 | −9,38 / −6,70 / −4,01 | 0,33 – 0,82 |
| Vidéo sur téléphone | 6 | −6,70 / −5,12 / −2,56 | 0,50 – 0,65 |

Le score de vivant d'un vrai visage, pris image par image, est trop dispersé pour une
règle à seuil fixe. La moyenne sur une fenêtre de 10 images sépare en revanche nettement
les deux populations :

| Règle | Utilisateur accepté (par fenêtre) | Attaques acceptées | Pire fenêtre d'attaque |
|---|---|---|---|
| 3 images, chacune ≥ 0,5 | 8,6 % | 0 % | — |
| 5 images, moyenne ≥ −1,0 | 82,4 % | 0,1 % | −0,92 |
| **10 images, moyenne ≥ −1,0** | **87,3 %** | **0 %** | **−3,45** |

L'acceptation par fenêtre sous-estime le confort réel : pendant une tentative, la
fenêtre glisse, et il suffit qu'une seule soit acceptée.

### 5.3 Usage réel

| Situation | Essais | Résultat |
|---|---|---|
| Utilisateur de face, y compris grimaces | 9 | 9 succès, 1,15 à 1,44 s |
| Utilisateur, tête tournée en permanence | 4 | « tête trop tournée » ou délai ; aucune fausse usurpation |
| Autre personne | 4 | 4 refus (similarité 0,16 à 0,23) |
| `sudo`, écran de verrouillage, connexion au démarrage | usage quotidien | succès en 0,7 à 2,5 s ; trousseau ouvert |

### 5.4 Performances

| Étape | Temps par image (i5-6300U) |
|---|---|
| Détection YuNet (320 px) | 25 ms |
| Empreinte SFace, en parallèle du vivant | 127 ms |
| Vivant MiniFASNet INT8 | ~45 ms |
| **Total** | **~150 ms (~6 images/s)** |

### 5.5 Vérification logicielle

- **191 tests automatisés**, dont :
  - le vrai module PAM chargé par une vraie pile PAM, face à un faux service : réponses
    falsifiées, service muet ou arrêté, 14 cas de secours par mot de passe, module
    supprimé mais ligne restée ;
  - le service de bout en bout sur un vrai socket ;
  - les 8 scénarios du coffre à clé, dont un incident réel de réinstallation ;
  - le choix entre plusieurs webcams.
- **Paquet testé à chaque version** en conteneur vierge, sur Ubuntu 24.04, Debian 13 et
  Kali : installation, calcul réel des trois modèles, `sudo`, mise à jour, assistant
  graphique, retrait (PAM restauré, `sudo` au mot de passe) et purge. Le refus de
  l'installation est vérifié sur Ubuntu 22.04 et Debian 12.

## 6. Discussion et limites

- **Portée des mesures.** Une seule personne, une webcam, un téléphone et pas de photo
  imprimée : les taux ci-dessus ne sont pas des taux d'erreur généraux.
  L'exactitude annoncée de MiniFASNet (98,2 % sur CelebA-Spoof [4]) est un chiffre de
  l'auteur, obtenu sur un autre jeu de données.
- **Équité.** Les performances selon la couleur de peau, l'âge ou le port de lunettes
  ne sont pas encore mesurées. La campagne prévoit un taux d'erreur par groupe.
- **Attaques hors de portée** d'une webcam RGB : masques 3D réalistes, jumeaux.
  Sentinel est un facteur de confort, pas un remplaçant du mot de passe pour les
  comptes les plus sensibles.
- **Attaquant déjà root** sur la machine allumée : il peut faire ce que fait le service,
  y compris déchiffrer la clé. C'est aussi le cas de Windows Hello sans ESS.
- **Faux périphérique USB** reprenant l'identité de la vraie webcam : le contrôle
  d'identité est contourné, et la détection du vivant reste la défense.
- **Pénombre** : aucune image exploitable, et le mot de passe est demandé en 1 s.

## 7. Travaux futurs

1. **Campagne à plusieurs personnes et webcams** (outil `sentinel campagne`, protocole
   [CAMPAGNE.md](CAMPAGNE.md)) : APCER par type d'attaque, BPCER, FAR, résultats par
   groupe et par webcam, selon ISO/IEC 30107-3 [5].
2. **Second défi par reflet de couleurs** de l'écran, pendant l'assistant.
3. **Mise à jour adaptative des gabarits** après des succès sûrs (vieillissement,
   barbe, lunettes).
4. **Nouvelles versions de GNOME** (52+) : procédure dans [MAINTENANCE.md](MAINTENANCE.md).
5. **Autres distributions** (`.rpm`, AUR) et autres écrans de connexion (SDDM, LightDM).

## 8. Reproductibilité

```bash
git clone https://github.com/Tiburce-kay/sentinel && cd sentinel
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
scripts/fetch_models.sh                        # modèles vérifiés par SHA-256
.venv/bin/python -m pytest                     # tests automatisés
export SENTINEL_CONFIG=config.dev.toml
.venv/bin/sentinel calibrate reel              # mesures sur votre webcam
scripts/build-all-debs.sh                      # paquet + tests sur 3 distributions
```

Les modèles sont épinglés par leur empreinte SHA-256 (`src/sentinel/models.toml`) et
le Python embarqué par la sienne (`scripts/build-deb.sh`).

## Références

1. W. Wu, H. Peng, S. Yu. *YuNet: A Tiny Millisecond-level Face Detector.* Machine
   Intelligence Research, 2023. Modèle : OpenCV Zoo, licence MIT.
2. Y. Zhong, W. Deng *et al.* *SFace: Sigmoid-Constrained Hypersphere Loss for Robust
   Face Recognition.* IEEE Transactions on Image Processing, 2021. Modèle : OpenCV Zoo,
   licence Apache-2.0.
3. Minivision, *Silent-Face-Anti-Spoofing* (architecture MiniFASNet) ; export ONNX
   quantifié : [facenox/face-antispoof-onnx](https://github.com/facenox/face-antispoof-onnx),
   licence Apache-2.0.
4. Y. Zhang *et al.* *CelebA-Spoof: Large-Scale Face Anti-Spoofing Dataset with Rich
   Annotations.* ECCV 2020.
5. ISO/IEC 30107-3, *Information technology — Biometric presentation attack detection —
   Part 3: Testing and reporting.*
6. CVE-2021-34466, contournement de Windows Hello par une caméra USB usurpée.
7. systemd, *systemd-creds(1)* et *Credentials* : chiffrement par TPM 2.0 et
   `LoadCredentialEncrypted=`.
