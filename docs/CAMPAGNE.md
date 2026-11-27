# Campagne de tests à plusieurs personnes

Objectif : mesurer, avec des personnes et des webcams variées, ce que Sentinel vaut
réellement — confort (vrais visages refusés), sécurité (attaques acceptées) et
**équité** (aucun groupe défavorisé, notamment selon la couleur de peau). Les chiffres
obtenus remplacent les promesses dans le README.

Tous les essais passent par le **vrai moteur de décision** (fenêtre glissante, défi
adaptatif, relief 3D) : ce sont les taux qu'un utilisateur vivrait.

## Ce qu'il faut

- Un PC avec Sentinel installé (paquet `.deb`) ; idéalement **2 ou 3 webcams** différentes
  (intégrée, USB d'entrée de gamme, USB de meilleure qualité).
- **8 participants au minimum**, 15 à 20 si possible, variés : couleurs de peau, âges,
  lunettes, barbe, coiffures, voile…
- Pour les attaques : un téléphone (photo et vidéo du participant), une imprimante pour
  les photos papier (format A4 couleur ou 10×15).
- Un **formulaire de consentement** signé par participant (`docs/CONSENTEMENT.md`).
- 15 à 20 minutes par participant.

## Confidentialité

- Chaque participant reçoit un **identifiant** (P01, P02…) : son nom n'est jamais saisi
  dans l'ordinateur. La correspondance nom ↔ identifiant reste sur les formulaires papier.
- **Aucune image n'est enregistrée.** `essais.csv` ne contient que des nombres et des
  résultats ; la webcam n'y figure que sous une empreinte non réversible.
- Les photos et vidéos d'attaque, prises sur le téléphone, sont **effacées devant le
  participant** à la fin de sa séance.
- À la fin de la séance : `sentinel campagne effacer P07` supprime son visage enregistré.
- Tout est stocké dans `~/sentinel-campagne`, séparé des comptes du PC, avec sa propre clé.

## Déroulement par participant

Les commandes se lancent dans un terminal, sans `sudo`. Remplacer `P07` par l'identifiant.

| Étape | Commande | Ce que fait le participant |
|---|---|---|
| 1. Consentement | — | lit et signe le formulaire |
| 2. Inscription | `sentinel campagne inscrire P07` | suit les consignes (face, gauche, droite, haut, bas) |
| 3. Vrai visage | `sentinel campagne essai P07 --type authentique` | se présente normalement, 10 fois ; suit les défis s'il y en a |
| 4. Autre personne | `sentinel campagne essai P07 --type imposteur --essais 5` | **un autre participant** se présente à sa place |
| 5. Photo sur écran | `sentinel campagne essai P07 --type photo-ecran` | l'opérateur présente une photo du participant sur le téléphone, en cherchant à tromper (distance, angle, luminosité max.) |
| 6. Photo papier | `sentinel campagne essai P07 --type photo-papier` | idem avec la photo imprimée |
| 7. Vidéo | `sentinel campagne essai P07 --type video` | vidéo du participant (tournant la tête si possible) sur le téléphone |
| 8. Fin | `sentinel campagne effacer P07` | photos et vidéos effacées du téléphone, devant lui |

**Conditions d'éclairage** : refaire l'étape 3 avec `--condition sombre` (lampe éteinte) et
`--condition contre-jour` (fenêtre derrière la personne) quand c'est possible.
**Autres webcams** : brancher l'autre webcam, régler `[camera] device` dans
`/etc/sentinel/config.toml` si besoin, puis refaire l'étape 3. L'inscription est liée à la
webcam utilisée : réinscrire le participant sur chaque webcam.

## Groupes (facultatif, pour l'équité)

Avec l'accord explicite du participant (case du formulaire), créer
`~/sentinel-campagne/participants.csv` :

```csv
participant,groupe
P01,peau foncée
P02,peau claire / lunettes
```

Ces informations sont sensibles : uniquement des catégories larges, jamais de nom.

## Résultats

```bash
sentinel campagne rapport
```

| Indicateur | Signification | Objectif indicatif |
|---|---|---|
| **BPCER** | part des vrais visages refusés (confort) | < 5 % |
| **APCER** par type d'attaque | part des attaques acceptées (sécurité, ISO/IEC 30107-3) | 0 % |
| **FAR** | part des autres personnes acceptées | 0 % |
| BPCER par groupe | équité : écarts entre groupes | écart faible |
| BPCER par webcam | robustesse au matériel | écart faible |

Ces objectifs sont indicatifs pour un projet de cette taille ; la norme ISO/IEC 30107-3
décrit la méthode d'évaluation, pas des seuils obligatoires. Avec peu de participants, un
« 0 % » signifie « aucune attaque réussie sur N essais » : toujours indiquer N.

## Après la campagne

Les seuils de sécurité (`[liveness] floor`, `challenge_floor`) ont été fixés sur une seule
personne et une seule webcam (`docs/CALIBRATION.md`). La campagne dira s'ils tiennent ;
consigner les résultats et les éventuels réglages dans `docs/CALIBRATION.md`.
