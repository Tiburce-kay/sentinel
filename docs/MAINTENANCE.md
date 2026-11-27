# Maintenance : versions, paquets et nouvelles versions de GNOME

## 1. Publier une version

1. Mettre à jour la version dans `pyproject.toml` et `src/sentinel/__init__.py`, puis
   compléter `CHANGELOG.md`.
2. Lancer les tests : `.venv/bin/python -m pytest`, qui doit tout réussir.
3. Fabriquer et tester le paquet (environ 30 minutes, Docker requis) :

   ```bash
   scripts/build-all-debs.sh
   cat dist/rapport.txt
   ```
   Chaque ligne doit indiquer `OK` :
   - `RÉSULTAT PAQUET` pour Ubuntu 22.04 et 24.04, Debian 12 et 13, et Kali ;
   - `BUREAU NON TIRÉ` : aucun bureau ni écran de connexion installé avec Sentinel ;
   - `REFUS` pour Ubuntu 20.04 et Debian 11, qui doivent rester refusés.
4. Essayer sur un vrai PC ou en machine virtuelle ce que les conteneurs ne peuvent pas
   tester : écran de verrouillage, connexion au démarrage, bouton clé, TPM.
5. Publier :

   ```bash
   cd dist && sha256sum sentinel_<version>-1_amd64.deb > SHA256SUMS && cd ..
   git tag -a v<version> -m "Sentinel <version>" && git push origin v<version>
   gh release create v<version> dist/sentinel_<version>-1_amd64.deb dist/SHA256SUMS \
       --title "Sentinel <version>" --notes-file dist/NOTES-v<version>.md
   ```

Ce que vérifient les tests du paquet (`scripts/test-deb-docker.sh`), dans un conteneur
vierge :
1. installation par `apt` ;
2. calcul réel des trois modèles d'IA (`scripts/smoke-ia.py`) ;
3. activation et vrai `sudo` (bon mot de passe accepté, mauvais refusé) ;
4. réinstallation, après laquelle Sentinel doit rester actif ;
5. assistant graphique sous écran virtuel ;
6. retrait (PAM restauré, `sudo` au mot de passe) ;
7. purge (aucune donnée restante).

## 2. Prendre en charge une nouvelle version de GNOME (52 et suivantes)

**Ce qui dépend de la version de GNOME :** seule l'extension
`packaging/gnome-shell-extension/sentinel@tiburce-kay.github.io`, qui masque le champ du
mot de passe et ajoute le bouton clé.

**Ce qui n'en dépend pas :** la reconnaissance faciale elle-même, à l'écran de
verrouillage comme à l'écran de connexion. Elle passe par PAM et continue de
fonctionner.

GNOME Shell refuse une extension dont `metadata.json` ne mentionne pas sa version
(`shell-version`). Comme on ne peut pas y déclarer des versions qui n'existent pas
encore, il faut, à chaque nouvelle version de GNOME :

| Étape | Commande ou action |
|---|---|
| 1. Obtenir la nouvelle version | une machine virtuelle de la distribution en développement (Debian sid, Ubuntu en développement, Kali), ou la version publiée |
| 2. Contrôle automatique du code de GNOME Shell | `scripts/check-gnome-compat.sh` sur cette machine. Il vérifie la présence de `InjectionManager`, de la classe `AuthPrompt` et de ses méthodes `begin`, `setQuestion`, `setMessage`, `finish` et `reset`, du champ `_entry` et du conteneur `_inputWell`. |
| 3. Si un élément manque | lire `js/gdm/authPrompt.js` dans le [code de GNOME Shell](https://gitlab.gnome.org/GNOME/gnome-shell) pour cette version, puis adapter `extension.js` |
| 4. Déclarer la version | ajouter `"52"` à `shell-version` et incrémenter `version` dans `metadata.json` |
| 5. Essai réel | installer le paquet, puis vérifier : écran de verrouillage (Super+L), connexion après redémarrage, champ masqué, bouton clé, échec du visage (main devant la caméra) suivi du champ du mot de passe |
| 6. Journal | `journalctl -b -o cat /usr/bin/gnome-shell \| grep -i sentinel` ne doit afficher aucune erreur |
| 7. Publier | nouvelle version (section 1) |

**Si une version de GNOME casse l'extension avant la mise à jour**, l'utilisateur ne
perd pas l'accès :
- la reconnaissance faciale continue de fonctionner par PAM ;
- le champ du mot de passe reste simplement visible, comme sans Sentinel ;
- GNOME désactive d'office une extension qui provoque une erreur ;
- le minuteur de sécurité de 15 s de l'extension réaffiche le champ si aucune réponse
  n'arrive.

**Écran de connexion (GDM 49 et plus).** L'écran de connexion tourne sous un compte
temporaire, qui a sa propre base de réglages. L'extension y est activée par
`/usr/share/gdm/dconf/95-sentinel`, et ce réglage n'est retenu que s'il est
**verrouillé** (`/usr/share/gdm/dconf/locks/95-sentinel`). Si une version de GDM change
ce mécanisme, c'est le premier endroit à vérifier.

## 3. Mettre à jour une dépendance

| Composant | Où | Précaution |
|---|---|---|
| Modèles d'IA | `src/sentinel/models.toml` (URL + SHA-256) | refaire la calibration (`docs/CALIBRATION.md`) : un autre modèle, ou même la version FP32 du modèle de vivant, change les scores |
| Python embarqué | `PY_URL` et `PY_SHA256` dans `scripts/build-deb.sh` | prendre l'empreinte dans le fichier `SHA256SUMS` officiel de python-build-standalone |
| Bibliothèques Python | `pyproject.toml` | relancer les tests et la fabrication |
| Distribution minimale | `BUILDER` et `MIN_GLIBC` dans `scripts/build-deb.sh`, `IMAGES` et `REFUSED` dans `scripts/build-all-debs.sh` | toujours fabriquer sur la plus ancienne distribution prise en charge |

## 4. Règles à ne jamais enfreindre

Ces règles viennent d'incidents réels ; le détail est dans `CHANGELOG.md`.

1. **Ne jamais créer ni remplacer une clé de chiffrement là où des gabarits existent.**
   `protect-key.sh` refuse de remplacer la clé du coffre et vérifie chaque changement
   avant d'effacer quoi que ce soit (`tests/test_protect_key.py`).
2. **Ne jamais installer en aveugle.** L'installateur depuis les sources simule
   `apt-get install` et s'arrête si des paquets du système seraient mis à jour ou retirés.
3. **PAM : `sufficient`, jamais `required`.** Retirer les lignes PAM **avant** les
   fichiers (`packaging/deb/prerm`).
4. **Tester la désinstallation et la réinstallation** avant toute modification des
   données ou de la clé.
