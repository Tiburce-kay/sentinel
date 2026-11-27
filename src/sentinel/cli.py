"""Ligne de commande : sentinel init | doctor | enroll | test | list | remove."""

from __future__ import annotations

import argparse
from pathlib import Path
import os
import pwd
import sys

from . import __version__
from .camera import Camera, CameraError, list_cameras, select_camera
from .config import Config, ConfigError, load_config
from .models import ModelError, load_verified, manifest
from .store import StoreError, TemplateStore, Enrollment, validate_username


def _target_user(args) -> str:
    if args.user:
        return validate_username(args.user)
    # Sous sudo, on agit pour la personne qui a lancé sudo, pas pour root.
    return validate_username(os.environ.get("SUDO_USER") or pwd.getpwuid(os.getuid()).pw_name)


def _engine(cfg: Config):
    from .auth import Engine
    from .vision import Detector, Recognizer, TextureLiveness

    live = TextureLiveness(cfg.models_dir, cfg.liveness) if cfg.liveness.enabled else None
    return Engine(cfg, Detector(cfg.models_dir), Recognizer(cfg.models_dir), live)


def _camera(cfg: Config):
    info = select_camera(cfg.camera.device, cfg.camera.require_uvc)
    return Camera(info, cfg.camera.width, cfg.camera.height, cfg.camera.warmup_frames, cfg.camera.warmup_max_s)


def cmd_init(cfg: Config, args) -> int:
    store = TemplateStore(cfg.data_dir, cfg.strict_permissions)
    store.init()
    print(f"Stockage prêt : {cfg.data_dir}")
    return 0


def cmd_doctor(cfg: Config, args) -> int:
    ok = True

    def line(good: bool, msg: str) -> None:
        nonlocal ok
        ok &= good
        print(f"  [{'OK' if good else '!!'}] {msg}")

    print("Modèles d'IA (empreintes SHA-256) :")
    for role, spec in manifest().items():
        try:
            load_verified(cfg.models_dir, role)
            line(True, f"{role:<11}{spec.file}  ({spec.license})")
        except ModelError as exc:
            line(False, str(exc))

    print("Caméras :")
    cams = list_cameras()
    if not cams:
        line(False, "aucune caméra détectée")
    for c in cams:
        kind = "webcam USB" if c.is_uvc else f"NON USB (pilote {c.driver or 'aucun'}) : refusée"
        role = "flux vidéo" if c.index == 0 else "métadonnées"
        print(f"       {c.device:<12}{c.name}  [{c.usb_id or '-'}] {kind}, {role}")
    try:
        chosen = select_camera(cfg.camera.device, cfg.camera.require_uvc)
        line(True, f"caméra retenue : {chosen.device}")
        line(os.access(chosen.device, os.R_OK | os.W_OK), f"accès en lecture à {chosen.device}")
    except CameraError as exc:
        line(False, str(exc))

    print("Stockage des gabarits :")
    try:
        TemplateStore(cfg.data_dir, cfg.strict_permissions).check_permissions()
        line(True, f"{cfg.data_dir} protégé")
    except (StoreError, OSError) as exc:
        line(False, str(exc))
    if not cfg.strict_permissions:
        line(False, "strict_permissions = false : réservé au développement")
    return 0 if ok else 1


def cmd_enroll(cfg: Config, args) -> int:
    from .enrollment import EnrollmentSession

    user = _target_user(args)
    store = TemplateStore(cfg.data_dir, cfg.strict_permissions)
    store.check_permissions()
    session = EnrollmentSession(_engine(cfg))
    print(f"Enregistrement du visage de « {user} ». Suivez les consignes.")
    last = ""
    with _camera(cfg) as cam:
        for frame in cam.frames(cfg.enroll.timeout_s):
            ev = session.feed(frame)
            msg = f"[{ev.progress:4.0%}] {ev.instruction}" + (f" — {ev.hint}" if ev.hint else "")
            if msg != last:
                print(f"  {msg}")
                last = msg
            if ev.done:
                break
        camera_id = cam.info.identity
    out = session.finish()
    s = out.stats
    if "relief" in s:
        print(f"  relief mesuré {s['relief']:.3f} (minimum {cfg.enroll.min_parallax})")
    if "liveness_median" in s:
        print(f"  vivant : médiane {s['liveness_median']:+.2f}, 10e centile {s['liveness_p10']:+.2f}")
    if not out.ok:
        print(f"Échec : {out.reason}. Rien n'a été enregistré.")
        return 1
    existing = store.load(user)
    if existing is not None and cfg.camera.pin_identity and existing.camera and existing.camera != camera_id \
            and not args.replace:
        print("Refusé : caméra différente de celle de l'enrôlement initial (utilisez --replace).")
        return 1
    store.save(Enrollment(user=user, embeddings=out.embeddings, camera=camera_id,
                          liveness_threshold=out.liveness_threshold, stats=out.stats))
    print(f"Visage enregistré : {len(out.embeddings)} empreintes chiffrées, aucune image conservée.")
    print(f"Seuil de vivant personnel : {out.liveness_threshold:+.2f}"
          f" (bornes {cfg.liveness.floor:+.1f} à {cfg.liveness.ceiling:+.1f})")
    return 0


def _no_store_hint(cfg: Config) -> str:
    if cfg.data_dir.exists():
        return ""
    return (f"\n  Le stockage {cfg.data_dir} n'existe pas. En développement, avez-vous oublié"
            f"\n  « export SENTINEL_CONFIG=config.dev.toml » dans ce terminal ?")


def cmd_test(cfg: Config, args) -> int:
    user = _target_user(args)
    store = TemplateStore(cfg.data_dir, cfg.strict_permissions)
    enr = store.load(user)
    if enr is None:
        print(f"Aucun visage enregistré pour « {user} ».{_no_store_hint(cfg)}")
        return 1
    engine = _engine(cfg)
    with _camera(cfg) as cam:
        if cfg.camera.pin_identity and enr.camera and cam.info.identity != enr.camera:
            print("Refusé : ce n'est pas la caméra utilisée à l'enrôlement.")
            return 1
        res = engine.authenticate(cam.frames(cfg.auth.timeout_s + cfg.auth.challenge_timeout_s),
                                  enr.embeddings, enr.liveness_threshold, enr.stats.get("reference_pose"),
                                  on_challenge=lambda m: print(f"  >>> {m}", flush=True))
    lv = res.liveness_scores
    print(f"Résultat : {res.status.value}")
    print(f"  similarité max {res.best_similarity:.3f} (seuil {cfg.recognition.threshold})")
    if lv:
        win = f"{res.window_liveness:+.2f}" if res.window_liveness > float("-inf") else "fenêtre incomplète"
        print(f"  vivant : moyenne sur {cfg.auth.window_frames} images {win} (seuil {res.liveness_threshold:+.2f});"
              f" image par image de {min(lv):+.2f} à {max(lv):+.2f}")
    print(f"  {res.frames} images en {res.elapsed_s:.2f} s"
          f" (caméra {res.camera_s:.2f} s, calcul {res.compute_s:.2f} s"
          f", soit {1000 * res.compute_s / max(res.frames, 1):.0f} ms par image)")
    if res.skipped_pose:
        print(f"  {res.skipped_pose} images ignorées : tête trop tournée")
    return 0 if res.ok else 1


def cmd_calibrate(cfg: Config, args) -> int:
    from . import calibrate as cal

    csv_path = cfg.data_dir / "calibration.csv"
    if args.report:
        if not csv_path.exists():
            print("Aucune mesure : lancer d'abord « sentinel calibrate reel ».")
            return 1
        print(cal.report(cal.load(csv_path), cfg.recognition.threshold,
                         cfg.liveness.threshold, cfg.auth.window_frames))
        return 0
    if not args.label:
        print("Préciser une étiquette (reel, autre, photo-ecran, photo-papier, video…) ou --report.")
        return 2
    user = _target_user(args)
    enr = TemplateStore(cfg.data_dir, cfg.strict_permissions).load(user)
    if enr is None:
        print(f"Aucun visage enregistré pour « {user} » : lancer d'abord « sentinel enroll ».")
        return 1
    engine = _engine(cfg)
    print(f"Mesure « {args.label} » pendant {args.duration:.0f} s (aucune image n'est conservée)…")
    with _camera(cfg) as cam:
        rows = cal.record(engine, cam.frames(args.duration), enr.embeddings, args.label)
    cal.append(csv_path, rows)
    faces = sum(r.face for r in rows)
    print(f"{len(rows)} images mesurées, dont {faces} avec un visage. Résultats : sentinel calibrate --report")
    return 0


def cmd_list(cfg: Config, args) -> int:
    store = TemplateStore(cfg.data_dir, cfg.strict_permissions)
    for user in store.users():
        enr = store.load(user)
        print(f"{user}  ({len(enr.embeddings)} empreintes)" if enr else user)
    return 0


def cmd_remove(cfg: Config, args) -> int:
    user = _target_user(args)
    removed = TemplateStore(cfg.data_dir, cfg.strict_permissions).remove(user)
    print(f"Visage de « {user} » supprimé." if removed else f"Aucun visage pour « {user} ».")
    return 0


def cmd_campagne(cfg: Config, args) -> int:
    from . import campaign as cp
    from .enrollment import EnrollmentSession

    root = Path(args.dossier).expanduser()
    store = TemplateStore(root / "visages", strict_permissions=False, credentials_dir=None,
                         uses_system_vault=False)
    csv_path, groups_path = root / "essais.csv", root / "participants.csv"
    if args.action == "rapport":
        if not csv_path.exists():
            print(f"Aucun essai dans {csv_path}.")
            return 1
        print(cp.report(cp.load(csv_path), cp.load_groups(groups_path)))
        return 0
    pid = cp.participant_id(args.participant or "")
    if args.action == "effacer":
        print(f"Visage de {pid.upper()} supprimé." if store.remove(pid) else f"Aucun visage pour {pid.upper()}.")
        return 0
    store.init()
    engine = _engine(cfg)
    if args.action == "inscrire":
        session = EnrollmentSession(engine)
        print(f"Inscription de {pid.upper()} : suivez les consignes.")
        last = ""
        with _camera(cfg) as cam:
            for frame in cam.frames(cfg.enroll.timeout_s):
                ev = session.feed(frame)
                msg = f"[{ev.progress:4.0%}] {ev.instruction}" + (f" — {ev.hint}" if ev.hint else "")
                if msg != last:
                    print(f"  {msg}")
                    last = msg
                if ev.done:
                    break
            camera_id = cam.info.identity
        out = session.finish()
        if not out.ok:
            print(f"Échec : {out.reason}.")
            return 1
        store.save(Enrollment(user=pid, embeddings=out.embeddings, camera=camera_id,
                              liveness_threshold=out.liveness_threshold, stats=out.stats))
        print(f"{pid.upper()} inscrit (aucune image conservée).")
        return 0
    # essai
    enr = store.load(pid)
    if enr is None:
        print(f"{pid.upper()} n'est pas inscrit : sentinel campagne inscrire {pid.upper()}")
        return 1
    print(f"{args.essais} essais « {args.type} » pour {pid.upper()} : {cp.TYPES[args.type]}.")
    for i in range(1, args.essais + 1):
        try:
            input(f"\nEssai {i}/{args.essais} : préparez la présentation, puis appuyez sur Entrée… ")
        except EOFError:
            return 0
        with _camera(cfg) as cam:
            res = engine.authenticate(
                cam.frames(cfg.auth.timeout_s + cfg.auth.challenge_timeout_s), enr.embeddings,
                enr.liveness_threshold, enr.stats.get("reference_pose"),
                on_challenge=lambda m: print(f"  >>> {m}", flush=True))
            tag = cp.camera_tag(cam.info.identity)
        cp.append(csv_path, cp.Attempt(pid, tag, args.condition, args.type, res.ok, res.status.name.lower(),
                                       res.elapsed_s, res.best_similarity, res.window_liveness, res.challenge))
        verdict = "ACCEPTÉ" if res.ok else "refusé"
        print(f"  {verdict} : {res.status.value} ({res.elapsed_s:.1f} s)")
    print(f"\nRésultats ajoutés à {csv_path} — synthèse : sentinel campagne rapport")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sentinel", description="Authentification faciale locale et résistante à l'usurpation.")
    p.add_argument("--version", action="version", version=f"sentinel {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="prépare le stockage chiffré")
    sub.add_parser("doctor", help="vérifie modèles, caméra et droits")
    e = sub.add_parser("enroll", help="enregistre votre visage")
    e.add_argument("--replace", action="store_true", help="accepte une caméra différente de l'enrôlement initial")
    sub.add_parser("test", help="teste la reconnaissance, sans rien déverrouiller")
    c = sub.add_parser("calibrate", help="mesure les scores réels (aucune image conservée)")
    c.add_argument("label", nargs="?", help="reel, autre, photo-ecran, photo-papier, video…")
    c.add_argument("--duration", type=float, default=20.0, help="durée en secondes (défaut 20)")
    c.add_argument("--report", action="store_true", help="affiche l'analyse des mesures")
    cg = sub.add_parser("campagne", help="campagne de tests à plusieurs personnes (docs/CAMPAGNE.md)")
    cg.add_argument("action", choices=("inscrire", "essai", "rapport", "effacer"))
    cg.add_argument("participant", nargs="?", help="identifiant : P01, P02… (jamais de nom)")
    cg.add_argument("--type", choices=("authentique", "imposteur", "photo-papier", "photo-ecran", "video"),
                    default="authentique")
    cg.add_argument("--essais", type=int, default=10)
    cg.add_argument("--condition", default="normale", help="éclairage : normale, sombre, contre-jour…")
    cg.add_argument("--dossier", default="~/sentinel-campagne", help="dossier de la campagne")
    sub.add_parser("list", help="liste les utilisateurs enrôlés")
    sub.add_parser("remove", help="supprime votre visage")
    for name in ("enroll", "test", "remove", "calibrate"):
        sub.choices[name].add_argument("--user", help="utilisateur visé (root uniquement)")
    args = p.parse_args(argv)

    if getattr(args, "user", None) and os.geteuid() != 0:
        print("--user est réservé à root.", file=sys.stderr)
        return 2
    try:
        cfg = load_config()
        return {"init": cmd_init, "doctor": cmd_doctor, "enroll": cmd_enroll, "test": cmd_test,
                "calibrate": cmd_calibrate, "campagne": cmd_campagne, "list": cmd_list, "remove": cmd_remove}[args.cmd](cfg, args)
    except (ConfigError, StoreError, ModelError, CameraError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
