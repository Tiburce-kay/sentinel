# Politique de sécurité

## Signaler une vulnérabilité

Merci de **ne pas** ouvrir de ticket public pour une vulnérabilité. Utilisez le
[signalement privé de GitHub](https://github.com/Tiburce-kay/sentinel/security/advisories/new)
(onglet *Security* → *Report a vulnerability*).

Merci d'indiquer :
- la version de Sentinel (`sentinel --version`), la distribution et la version de GNOME ;
- les étapes pour reproduire le problème ;
- l'impact : contournement de l'authentification, accès aux gabarits, déni de
  l'accès par mot de passe…

Délais visés : un accusé de réception sous 7 jours, et un correctif ou une mesure de
contournement sous 30 jours pour une faille sérieuse.

## Versions suivies

| Version | Correctifs de sécurité |
|---|---|
| 0.3.0 et suivantes | ✅ |
| 0.2.5 et antérieures | ❌ paquets retirés : passer à la dernière version |

## Périmètre

Font partie du périmètre :
- un moyen d'être accepté sans être l'utilisateur enregistré : photo, vidéo, masque,
  autre personne, gabarit fabriqué ou déplacé ;
- un moyen d'empêcher l'accès par mot de passe ;
- l'accès aux gabarits, à la clé ou au mot de passe conservé sans être root ;
- une élévation de privilèges via le service `sentineld` ou le module PAM.

Le modèle de menaces et les **limites assumées** sont décrits dans
[docs/CONCEPTION.md](docs/CONCEPTION.md#4-modèle-de-menaces). Par exemple, un
attaquant déjà root sur la machine allumée, ou un masque 3D réaliste, sont hors de
portée d'une webcam ordinaire. Ces limites connues ne sont pas des vulnérabilités, mais
toute idée pour les réduire est bienvenue.
