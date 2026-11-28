# Calibration sur matériel réel

Mesures du 25/11/2026 sur un ThinkPad T570 (webcam intégrée 04f2:b5ab, 640×480,
sans infrarouge), avec `sentinel calibrate`. Seuls des nombres sont enregistrés,
jamais d'image.

## Données

| Étiquette | Séances | Images exploitables | Score de vivant (p5 / médiane / p95) | Similarité (min – max) |
|---|---|---|---|---|
| `reel` (utilisateur, éclairage normal) | 1 | 72 | −3,02 / 0,51 / 3,41 | 0,81 – 0,91 |
| `reel` (pénombre) | 1 | 0 / 118 | — (trop sombre) | — |
| `autre` (autre personne) | 1 | 33 | −2,03 / 0,48 / 1,71 | 0,15 – 0,32 |
| `photo-ecran` (photo sur téléphone) | 3 | 324 | −9,38 / −6,70 / −4,01 | 0,33 – 0,82 |
| `video` (vidéo sur téléphone) | 1 | 6 | −6,70 / −5,12 / −2,56 | 0,50 – 0,65 |

## Constats

1. **La reconnaissance sépare parfaitement** l'utilisateur (≥ 0,81) d'une autre
   personne (≤ 0,32). Le seuil de 0,50 laisse une large marge des deux côtés.
2. **Le score de vivant d'un vrai visage est très bruité image par image** (de −3,9 à
   +9,0). Avec la règle initiale (3 images consécutives, chacune ≥ 0,5), l'utilisateur
   n'était accepté que dans **8,6 %** des fenêtres : inutilisable.
3. **Une photo produit parfois une image isolée « vivante »** (jusqu'à +1,11). Une règle
   image par image devait donc choisir entre refuser l'utilisateur et laisser passer
   des images de photo.
4. **En moyenne sur plusieurs images, les deux populations se séparent nettement.**

## Choix de la règle

Simulation sur les séquences enregistrées (fenêtres glissantes ; pour les attaques,
on suppose que l'attaquant satisfait le contrôle de qualité : cas le plus défavorable) :

| Fenêtre | Agrégat | Seuil | Utilisateur accepté | Attaques acceptées | Pire fenêtre d'attaque |
|---|---|---|---|---|---|
| 3 images | chaque image ≥ seuil | 0,5 | 8,6 % | 0 % | — |
| 5 | moyenne | −1,0 | 82,4 % | **0,1 %** | −0,92 |
| 8 | moyenne | −1,0 | 84,6 % | 0 % | −2,13 |
| **10** | **moyenne** | **−1,0** | **87,3 %** | **0 %** | **−3,45** |
| 10 | moyenne | −1,5 | 96,8 % | 0 % | −3,45 |

**Retenu : 10 images consécutives, chacune reconnue, moyenne de vivant ≥ −1,0.**
Marge de 2,45 sous le seuil pour la pire fenêtre d'attaque observée. Le seuil −1,5,
plus confortable, réduirait cette marge : il ne sera envisagé qu'avec la deuxième
couche de détection du vivant (défi actif).

Le taux d'acceptation par fenêtre sous-estime le confort réel : pendant une
tentative de 4 s, la fenêtre glisse et il suffit qu'une seule soit acceptée.

## Performances

Mesurées sur Intel Core i5-6300U :

| Étape | Avant | Après |
|---|---|---|
| Détection (YuNet) | 148 ms (640×480) | 25 ms (image réduite à 320 px) |
| Empreinte (SFace) | 127 ms | 127 ms, en parallèle du vivant |
| Vivant (MiniFASNet INT8) | 78 ms | ~45 ms |
| **Total par image** | **~350 ms (2,8 im/s)** | **~150 ms (~6 im/s)** |

La version FP32 du modèle de vivant est plus rapide (26 ms) mais donne des scores
différents : elle invaliderait cette calibration. Elle n'est pas retenue pour l'instant.

## Limites de ces mesures

- Un seul utilisateur, une seule séance bien éclairée, un seul téléphone pour les
  attaques : à élargir avant toute conclusion générale.
- Aucune photo imprimée testée.
- La vidéo n'a été détectée comme visage que sur 18 images sur 225 : l'attaque doit
  être refaite plus près.
- **En pénombre, aucune image n'est exploitable** : l'éclairage par l'écran est prévu.

## Calibration automatique à l'enrôlement (mise à jour du 25/11/2026)

Deux enrôlements réels avec l'assistant graphique :

| Essai | Relief | Vivant (médiane / 10e centile) | Seuil personnel calculé |
|---|---|---|---|
| 1 (éclairage de la pièce) | 0,152 | +2,66 / +0,01 | −0,49 |
| 2 (écran blanc plein écran) | 0,101 | +2,68 / +0,64 | +0,14 |

**Constat :** l'enrôlement se fait en conditions idéales (de face, bien éclairé, écran
blanc), qui gonflent les scores. Un seuil personnel plus strict que le seuil validé
(−1,0) refuse ensuite l'utilisateur dans les conditions ordinaires : vérification en
3,5 s sur un maximum de 4 s, et, d'après la séance en éclairage normal, une grande part
des fenêtres refusées avec un seuil de +0,14.

**Règle retenue :** le seuil personnel est `10e centile − 0,5`, borné entre le plancher
(−2,0) et le **seuil validé (−1,0)**. La calibration n'assouplit que pour les webcams
qui donnent des scores bas ; elle ne durcit jamais.

Angles mesurés lors de l'essai 2 (écart à la position de face) : gauche +0,149,
droite −0,223, haut −0,083, bas +0,134 (lacet, puis tangage). Les quatre directions sont
reconnues dans le bon sens ; le seuil de tangage passe de 0,08 à 0,06, « haut » ayant
été atteint de justesse.

## Essais d'usage réel (25/11/2026, soir)

Après le réenrôlement (position de face mémorisée), `sentinel test` en conditions
ordinaires (terminal, sans écran blanc) :

| Situation | Essais | Résultat |
|---|---|---|
| Utilisateur de face, y compris grimaces | 9 | 9 succès, 1,15 à 1,44 s (2,56 s au premier essai, processeur « froid ») ; similarité min. 0,69 |
| Utilisateur, tête tournée en permanence | 4 | « tête trop tournée » ou délai ; aucune fausse « usurpation » |
| Autre personne | 4 | 4 refus ; similarité 0,16 à 0,23 |

Calcul : ~120 ms par image de face sur i5-6300U ; la caméra n'attend presque jamais.
Les lenteurs observées plus tôt (3,3 à 3,8 s) venaient d'un processeur occupé (vidéo).

## Deuxième utilisateur, autre webcam (27-28/11/2026)

Premier testeur extérieur : ThinkPad sous Kubuntu 22.04 (KDE Plasma 5.24), webcam
intégrée. Enregistrement fait avec l'assistant, sous écran blanc. Seuil personnel
calculé : −1,26. Données issues du journal du service : aucune image.

| Moment | Essais | Similarité | Meilleure fenêtre de vivant | Lumière du visage (0-255) | Résultat |
|---|---|---|---|---|---|
| Soir (22h39-22h41) | 6 | 0,62 – 0,70 | −2,99 à −2,16 | non mesurée | 4 « usurpation », 1 défi échoué, 1 succès après défi |
| Jour (13h02-13h03) | 4 | 0,73 – 0,77 | −2,79 à −1,73 | 59 – 69 | 3 « usurpation », 1 défi échoué |
| Jour (13h10) | 1 | 0,69 | −1,57 | 79 | succès après défi |

**Constats :**
1. **La reconnaissance tient** (similarité ≥ 0,62, contre ≤ 0,32 pour d'autres personnes
   chez le premier utilisateur).
2. **Le score de vivant de ce vrai visage est nettement plus bas** que chez le premier
   utilisateur, et suit la **lumière du visage** : 59-69 donne des refus, 79 un succès.
   Même en plein jour, le visage reste sombre. Hypothèse : la webcam règle son exposition
   sur toute l'image, avec une source de lumière derrière la personne. Un biais du
   modèle selon les visages n'est pas exclu, et la campagne devra le mesurer.
3. **L'écran blanc de l'enregistrement surestime les scores** : le seuil personnel
   (−1,26) était plus strict que les conditions réelles ne le permettaient.

**Mesures prises (0.3.0) :** la vérification de l'assistant se fait sans écran blanc,
et le seuil personnel en tient compte (−2,0 au plus bas, plancher inchangé) ; conseil
d'éclairage quand le visage est sombre ; luminosité du visage au journal. Sous ce
nouveau seuil, 4 des 11 essais ci-dessus seraient acceptés directement, et 3 autres
passeraient par un défi.

**À étudier :** compensation de contre-jour de la webcam (réglage UVC « backlight
compensation ») ou normalisation de la luminosité du visage, à valider d'abord contre
des photos et vidéos d'attaque avant toute mise en service.
