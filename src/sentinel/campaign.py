"""Campagne de tests à plusieurs personnes et plusieurs webcams (docs/CAMPAGNE.md).

Chaque essai passe par le VRAI moteur de décision (fenêtre glissante, défi adaptatif,
contrôle du relief) : les taux mesurés sont ceux qu'un utilisateur vivrait.

    sentinel campagne inscrire P01                    enregistre le visage du participant
    sentinel campagne essai P01 --type photo-ecran    série d'essais d'un type donné
    sentinel campagne rapport                         taux d'erreur (ISO/IEC 30107-3)
    sentinel campagne effacer P01                     supprime le visage du participant

Rien n'est mêlé aux visages des comptes du PC : la campagne a son propre dossier
(par défaut ~/sentinel-campagne), avec sa propre clé. Seuls des nombres sont écrits
dans essais.csv : aucune image, aucun nom (identifiants P01, P02…).
"""

from __future__ import annotations

import csv
import hashlib
import re
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

ID_RE = re.compile(r"p[0-9]{2,3}")
TYPES = {
    "authentique": "le participant lui-même, de face",
    "imposteur": "une AUTRE personne, vivante, devant la caméra",
    "photo-papier": "photo imprimée du participant",
    "photo-ecran": "photo du participant sur un téléphone ou une tablette",
    "video": "vidéo du participant (téléphone, tablette, écran)",
}
ATTACKS = ("photo-papier", "photo-ecran", "video")
FIELDS = ["date", "participant", "camera", "condition", "type", "resultat", "duree_s",
          "similarite", "vivant_fenetre", "defi"]


def participant_id(raw: str) -> str:
    """P01 -> p01 (sert aussi de nom de gabarit dans le dossier de campagne)."""
    pid = raw.strip().lower()
    if not ID_RE.fullmatch(pid):
        raise ValueError("identifiant de participant attendu : P01, P02… (jamais de nom)")
    return pid


def camera_tag(identity: str) -> str:
    """Identifiant court et non réversible de la webcam (empreinte de son identité USB)."""
    return hashlib.sha256(identity.encode()).hexdigest()[:8]


@dataclass
class Attempt:
    participant: str
    camera: str
    condition: str
    type: str
    accepted: bool
    status: str
    elapsed: float
    similarity: float
    window_liveness: float
    challenge: str


def append(path: Path, a: Attempt) -> None:
    new = not path.exists()
    with path.open("a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(FIELDS)
        w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), a.participant, a.camera, a.condition, a.type,
                    a.status, f"{a.elapsed:.2f}", f"{a.similarity:.3f}",
                    "" if a.window_liveness == float("-inf") else f"{a.window_liveness:.2f}", a.challenge])


def load(path: Path) -> list[Attempt]:
    out = []
    with path.open(newline="") as fh:
        for d in csv.DictReader(fh):
            out.append(Attempt(d["participant"], d["camera"], d["condition"], d["type"],
                               d["resultat"] == "success", d["resultat"], float(d["duree_s"]),
                               float(d["similarite"]),
                               float(d["vivant_fenetre"]) if d["vivant_fenetre"] else float("-inf"),
                               d["defi"]))
    return out


def _rate(xs: list[bool]) -> str:
    return f"{100 * sum(xs) / len(xs):5.1f} % ({sum(xs)}/{len(xs)})" if xs else "    —"


def report(attempts: list[Attempt], groups: dict[str, str] | None = None) -> str:
    """Taux d'erreur, au niveau de la décision (un essai = une tentative complète) :

    BPCER  présentations authentiques refusées          (confort)
    APCER  attaques acceptées, par type d'attaque        (sécurité, ISO/IEC 30107-3)
    FAR    autres personnes acceptées                     (reconnaissance)
    """
    if not attempts:
        return "Aucun essai enregistré."
    by_type: dict[str, list[Attempt]] = defaultdict(list)
    for a in attempts:
        by_type[a.type].append(a)
    participants = sorted({a.participant for a in attempts})
    cameras = sorted({a.camera for a in attempts})
    lines = [f"{len(attempts)} essais, {len(participants)} participant(s), {len(cameras)} webcam(s)", ""]
    auth = by_type.get("authentique", [])
    lines.append(f"BPCER (vrai visage refusé)       {_rate([not a.accepted for a in auth])}")
    if auth:
        ok = [a.elapsed for a in auth if a.accepted]
        challenged = [a for a in auth if a.challenge]
        if ok:
            lines.append(f"  temps moyen d'acceptation      {sum(ok) / len(ok):.2f} s")
        lines.append(f"  essais avec défi               {_rate([bool(a.challenge) for a in auth])}")
        if challenged:
            lines.append(f"  défis réussis                  {_rate([a.accepted for a in challenged])}")
    for t in ATTACKS:
        xs = by_type.get(t, [])
        lines.append(f"APCER {t:<13} (accepté)  {_rate([a.accepted for a in xs])}")
    lines.append(f"FAR (autre personne acceptée)    {_rate([a.accepted for a in by_type.get('imposteur', [])])}")
    if groups:
        lines += ["", "BPCER par groupe (équité) :"]
        per: dict[str, list[bool]] = defaultdict(list)
        for a in auth:
            per[groups.get(a.participant, "non renseigné")].append(not a.accepted)
        for g in sorted(per):
            lines.append(f"  {g:<28} {_rate(per[g])}")
    lines += ["", "BPCER par webcam :"]
    for cam in cameras:
        lines.append(f"  {cam:<28} {_rate([not a.accepted for a in auth if a.camera == cam])}")
    return "\n".join(lines)


def load_groups(path: Path) -> dict[str, str]:
    """participants.csv facultatif : participant,groupe (ex. P01,peau foncée / lunettes)."""
    if not path.exists():
        return {}
    with path.open(newline="") as fh:
        return {participant_id(r["participant"]): r["groupe"] for r in csv.DictReader(fh)}
