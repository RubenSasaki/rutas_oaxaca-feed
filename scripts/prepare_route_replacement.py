#!/usr/bin/env python3
"""Stage explicit replacements against an immutable Git commit; never publish."""
from __future__ import annotations

import argparse
import hashlib
import json
import copy
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

try:
    from .validate_feed import Report, compare_with_base, load_snapshot, parse_json
except ImportError:
    from validate_feed import Report, compare_with_base, load_snapshot, parse_json


class PreparationError(ValueError):
    pass


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def document(raw: bytes, label: str):
    report = Report()
    try:
        value = parse_json(raw.decode("utf-8"), label, report)
    except UnicodeError:
        raise PreparationError(f"{label}: UTF-8 inválido") from None
    if report.errors:
        raise PreparationError("\n".join(report.errors))
    return value


def check_snapshot(root: Path):
    report = Report()
    snapshot = load_snapshot(root, report)
    if snapshot is None or report.errors:
        raise PreparationError("\n".join(report.errors) or "Snapshot inválido")
    return snapshot, report


def hashes(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise PreparationError("No se permiten enlaces en snapshots")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    return result


def export_base(repo: Path, commit: str, target: Path) -> None:
    """Read only blobs at the exact commit, never the possibly dirty worktree."""
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise PreparationError("baseCommit debe ser el SHA completo de 40 caracteres")
    verified = subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "--verify", commit + "^{commit}"],
        text=True,
    ).strip()
    if verified != commit:
        raise PreparationError("La base no coincide con el commit solicitado")
    listing = subprocess.check_output(
        ["git", "-C", str(repo), "ls-tree", "-rz", commit, "--", "rutas",
         "eventos.json", "eventos/imagenes", "places"]
    )
    for item in listing.split(b"\0"):
        if not item:
            continue
        header, encoded_name = item.split(b"\t", 1)
        mode, kind, blob = header.decode("ascii").split()
        name = encoded_name.decode("utf-8")
        relative = Path(name)
        if (mode not in {"100644", "100755"} or kind != "blob"
                or relative.is_absolute() or ".." in relative.parts):
            raise PreparationError("La base contiene un objeto no permitido")
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(subprocess.check_output(
            ["git", "-C", str(repo), "cat-file", "blob", blob]))



def require_text(value, label):
    if not isinstance(value, str) or not value or value != value.strip():
        raise PreparationError(f"{label}: requiere texto explícito sin espacios externos")
    return value


def require_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise PreparationError(f"{label}: campos exactos requeridos: {sorted(keys)}")


def stop_diff(original, replacement):
    """Report exact identity changes, never infer a remapping or merge points."""
    old = {s["id"]: s for s in original["paradas"]}
    new = {s["id"]: s for s in replacement["paradas"]}
    comparisons = []
    for position, stop in enumerate(original["paradas"]):
        same_id = new.get(stop["id"])
        identity = ["nombre", "lat", "lng"]
        changed = same_id is None or any(stop[k] != same_id[k] for k in identity)
        comparisons.append({
            "id": stop["id"], "oldPosition": position,
            "before": {k: stop[k] for k in identity},
            "sameIdAfter": None if same_id is None else {k: same_id[k] for k in identity},
            "status": "REMOVED" if same_id is None else
                      "SAME_ID_CHANGED_ATTRIBUTES_REVIEW_REQUIRED" if changed else "UNCHANGED_ATTRIBUTES",
            "reviewRequired": changed,
            "oldDeclaredRoutes": stop["rutasQueParanAqui"],
        })
    return {"oldStopCount": len(old), "newStopCount": len(new),
            "addedIds": sorted(set(new) - set(old)), "removedIds": sorted(set(old) - set(new)),
            "comparisons": comparisons, "schedulesArchivedNotTransferred": original["horarios"],
            "oldSharedAssociationsNotTransferred": [s for s in original["paradas"]
                                                     if s["rutasQueParanAqui"] != [original["id"]]],
            "metadataChanges": {key: {"before": original.get(key), "after": replacement.get(key)}
                                for key in ("nombre", "nombreEN", "origen", "origenEN", "destino", "destinoEN", "tipo", "color", "esGratuita", "notaEspecial")
                                if original.get(key) != replacement.get(key)},
            "geometryChanged": original["trayecto"] != replacement["trayecto"] or
                               original["trayectosPorSegmento"] != replacement["trayectosPorSegmento"]}


def references_in_snapshot(base, ids):
    result = []
    def walk(value, location, file):
        if isinstance(value, str) and value in ids:
            result.append({"file": file, "path": location, "stopId": value})
        elif isinstance(value, list):
            for i, item in enumerate(value):
                walk(item, f"{location}[{i}]", file)
        elif isinstance(value, dict):
            for key, item in value.items():
                if key in ids:
                    result.append({"file": file, "path": location + ".<key>", "stopId": key})
                walk(item, f"{location}.{key}", file)
    for path in sorted(base.rglob("*.json")):
        walk(document(path.read_bytes(), path.name), "$", path.relative_to(base).as_posix())
    return result


def review_capture(base, capture):
    """Metadata-free inspection: never produces a feed candidate."""
    route_id = capture.get("id")
    baseline, _ = check_snapshot(base)
    if route_id not in baseline.routes:
        raise PreparationError("La captura no corresponde a una ruta publicada")
    original = baseline.routes[route_id]
    # Only compare fields actually supplied by capture; metadata is explicitly absent.
    temporary = {**original, **capture, "horarios": []}
    result = stop_diff(original, temporary)
    result["metadataChanges"] = None
    affected = {s["id"] for s in result["comparisons"] if s["reviewRequired"]}
    result.update(routeId=route_id, status="CAPTURE_ONLY_METADATA_PENDING_NOT_A_FEED_CANDIDATE",
                  references=references_in_snapshot(base, affected),
                  limitation="Igualdad exacta de atributos no certifica identidad física; no se propone remapeo.")
    return result


def read_proposal(directory, change):
    if directory.is_symlink() or not directory.is_dir():
        raise PreparationError("La propuesta debe ser un directorio regular")
    required = {"route.json", "provenance.json", "metadata.json", "capture.json", "capture-plan.json"}
    if {p.name for p in directory.iterdir()} != required:
        raise PreparationError("Propuesta incompleta o con archivos extra")
    raw = {}
    for name in required:
        p = directory / name
        if p.is_symlink() or not p.is_file():
            raise PreparationError("La propuesta no admite enlaces ni subdirectorios")
        raw[name] = p.read_bytes()
    prov = document(raw["provenance.json"], "provenance")
    if digest(raw["provenance.json"]) != change["proposalSha256"]:
        raise PreparationError("Hash de propuesta no coincide con el plan editorial")
    if (not isinstance(prov, dict) or prov.get("operation") != "replace-published-route"
            or prov.get("status") != "CANDIDATE_FOR_REVIEW_NOT_PUBLISHED"
            or prov.get("routeId") != change["id"]
            or prov.get("version") != change["version"]
            or prov.get("schedulePolicy") != "none-phase1"
            or prov.get("relationshipPolicy") != "own-route-only-no-inference"):
        raise PreparationError("La propuesta no acredita reemplazo explícito con metadata")
    for name, key in [("route.json", "routeSha256"), ("metadata.json", "metadataSha256"),
                      ("capture.json", "captureSha256"), ("capture-plan.json", "capturePlanSha256")]:
        if digest(raw[name]) != prov.get(key):
            raise PreparationError(f"{name}: bytes distintos a la propuesta")
    capture_plan = document(raw["capture-plan.json"], "capture-plan")
    require_keys(capture_plan, {"schemaVersion", "operation", "routeId", "layers", "expectedStops"}, "capture-plan")
    if (type(capture_plan["schemaVersion"]) is not int or capture_plan["schemaVersion"] != 1 or capture_plan["operation"] != "replace-published-route"
            or capture_plan["routeId"] != change["id"] or capture_plan["expectedStops"] != prov.get("expectedStops")):
        raise PreparationError("Plan de captura incompatible")
    require_keys(capture_plan["expectedStops"], {"ida", "regreso"}, "expectedStops")
    if any(type(value) is not int or value <= 0 for value in capture_plan["expectedStops"].values()):
        raise PreparationError("Conteos explícitos positivos requeridos")
    require_keys(capture_plan["layers"], {"idaStops", "idaLine", "regresoStops", "regresoLine"}, "layers")
    for role, layer in capture_plan["layers"].items():
        require_keys(layer, {"path", "sha256"}, role)
        if not isinstance(layer["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", layer["sha256"]) or layer["sha256"] != prov.get("sourceHashes", {}).get(role):
            raise PreparationError("Huella de capa inconsistente")
    route = document(raw["route.json"], "route")
    metadata = document(raw["metadata.json"], "metadata")
    capture = document(raw["capture.json"], "capture")
    require_keys(metadata, {"approval", "route"}, "metadata")
    require_keys(metadata["approval"], {"approvedBy", "reference"}, "metadata.approval")
    for key in ("approvedBy", "reference"):
        require_text(metadata["approval"][key], f"approval.{key}")
    if metadata["approval"] != prov.get("approval"):
        raise PreparationError("La aprobación de metadata no coincide")
    required_metadata = {"id", "nombre", "nombreEN", "origen", "origenEN", "destino", "destinoEN",
                         "tipo", "color", "esGratuita", "notaEspecial", "horarios", "version"}
    require_keys(metadata["route"], required_metadata, "metadata.route")
    for key in required_metadata:
        expected = change["version"] if key == "version" else route.get(key)
        if metadata["route"][key] != expected:
            raise PreparationError(f"Metadata no coincide con ruta: {key}")
    for key in ("id", "paradas", "trayecto", "trayectosPorSegmento"):
        if capture.get(key) != route.get(key):
            raise PreparationError(f"Se alteró la captura: {key}")
    if (route.get("id") != change["id"] or route.get("horarios") != []
            or prov.get("stopCount") != len(route.get("paradas", []))):
        raise PreparationError("ID, conteo u horarios incompatibles")
    counts = {"ida": sum("_IDA_" in s["id"] for s in route["paradas"]),
              "regreso": sum("_REG_" in s["id"] for s in route["paradas"])}
    if counts != prov["expectedStops"]:
        raise PreparationError("Conteos por sentido no coinciden con captura")
    return raw["route.json"], route, prov


def build_package(base: Path, proposals: Path, plan: dict, staging: Path) -> dict:
    require_keys(plan, {"schemaVersion", "operation", "baseCommit", "reviewedBy", "reference",
                        "version", "generatedAt", "routes"}, "plan editorial")
    if type(plan["schemaVersion"]) is not int or plan["schemaVersion"] != 1 or plan["operation"] != "replace-published-routes":
        raise PreparationError("Operación explícita requerida")
    if not isinstance(plan["baseCommit"], str) or not re.fullmatch(r"[0-9a-f]{40}", plan["baseCommit"]):
        raise PreparationError("baseCommit exacto requerido")
    for key in ("reviewedBy", "reference", "version", "generatedAt"):
        require_text(plan[key], key)
    baseline, _ = check_snapshot(base)
    before_hashes = hashes(base)
    entries = {e["id"]: e for e in baseline.index["rutas"]}
    if not isinstance(plan["routes"], list) or not plan["routes"]:
        raise PreparationError("Lista explícita de reemplazos requerida")
    updates, details = {}, {}
    for change in plan["routes"]:
        require_keys(change, {"id", "version", "expectedBaseSha256", "proposalSha256"}, "reemplazo")
        route_id = change["id"]
        if not isinstance(route_id, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{1,15}", route_id) or route_id not in entries or route_id in updates:
            raise PreparationError("ID desconocido/nuevo/duplicado no permitido")
        if change["expectedBaseSha256"] != before_hashes.get(f"rutas/{route_id}.json"):
            raise PreparationError(f"{route_id}: la base cambió desde la revisión")
        version = require_text(change["version"], "version de ruta")
        if version == str(entries[route_id]["version"]).strip():
            raise PreparationError("Una sustitución exige versión editorial nueva")
        raw, route, provenance = read_proposal(proposals / route_id, change)
        if digest(raw) == change["expectedBaseSha256"]:
            raise PreparationError("Sin cambios para reemplazar")
        updates[route_id] = (raw, route, provenance)
        details[route_id] = stop_diff(baseline.routes[route_id], route)
    if staging.exists() or staging.is_symlink():
        raise PreparationError("Staging debe ser nuevo")
    staging.mkdir()
    before, candidate = staging / "before", staging / "candidate"
    shutil.copytree(base, before)
    shutil.copytree(base, candidate)
    index = copy.deepcopy(baseline.index)
    index.update(version=plan["version"], generatedAt=plan["generatedAt"])
    for entry in index["rutas"]:
        route_id = entry["id"]
        if route_id not in updates:
            continue
        raw, route, provenance = updates[route_id]
        (candidate / "rutas" / f"{route_id}.json").write_bytes(raw)
        # Preserve entry order, active/temporary states and visibility windows.
        entry.update(version=provenance["version"], nombre=route["nombre"], color=route["color"], sha256=digest(raw))
    (candidate / "rutas" / "indice.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    current, report = check_snapshot(candidate)
    compare_with_base(current, baseline, candidate, report)
    if report.errors:
        raise PreparationError("\n".join(report.errors))
    after = hashes(candidate)
    changed = {p for p in before_hashes.keys() | after.keys() if before_hashes.get(p) != after.get(p)}
    allowed = {"rutas/indice.json"} | {f"rutas/{rid}.json" for rid in updates}
    if changed != allowed or set(before_hashes) != set(after) or hashes(base) != before_hashes or hashes(before) != before_hashes:
        raise PreparationError("Cambio fuera de allowlist o base alterada")
    affected = {c["id"] for d in details.values() for c in d["comparisons"] if c["reviewRequired"]}
    review = {"schemaVersion": 1, "status": "VALIDATED_FOR_REVIEW_NOT_PUBLISHED",
              "baseCommit": plan["baseCommit"], "plan": plan,
              "before": before_hashes, "candidate": after, "changed": sorted(changed),
              "routeCount": len(current.routes), "stopCount": sum(len(r["paradas"]) for r in current.routes.values()),
              "routes": details, "affectedReferences": references_in_snapshot(base, affected),
              "warnings": report.warnings,
              "pending": ["Aprobación de diff y referencias antes de publicar",
                          "Las referencias locales del dispositivo (p. ej. favoritos) no están en el feed y requieren revisión",
                          "No se certifica identidad física por coincidencia de ID o proximidad"]}
    shutil.copytree(proposals, staging / "proposals", ignore=lambda directory, names: [n for n in names if Path(directory) == proposals and n not in updates])
    (staging / "review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (staging / "READY_FOR_REVIEW").write_text("Validación local; publicación NO autorizada por este archivo.\n", encoding="utf-8")
    return review


def prepare(repo: Path, proposals: Path, plan_file: Path, output: Path) -> dict:
    plan = document(plan_file.read_bytes(), "plan")
    output = output.parent.resolve() / output.name
    if output.exists() or output.is_symlink() or output.is_relative_to(repo.resolve()) or output.is_relative_to(proposals.resolve()):
        raise PreparationError("La salida debe ser nueva y quedar fuera del feed y propuestas")
    for parent in output.parents:
        if (parent / ".git").exists() and ((parent / "pubspec.yaml").exists() or (parent / "rutas/indice.json").exists()):
            raise PreparationError("La salida debe quedar fuera de repositorios de producto/feed")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="route-replacement-", dir=output.parent) as directory:
        temp = Path(directory)
        export_base(repo, plan["baseCommit"], temp / "base")
        review = build_package(temp / "base", proposals, plan, temp / "package")
        # Reserve destination exclusively. An interrupted transfer has no READY.
        output.mkdir()
        for child in (temp / "package").iterdir():
            if child.name != "READY_FOR_REVIEW":
                shutil.move(str(child), str(output / child.name))
        shutil.move(str(temp / "package/READY_FOR_REVIEW"), str(output / "READY_FOR_REVIEW"))
    return review


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--proposals", type=Path, required=True)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = prepare(args.repo, args.proposals, args.plan, args.output)
    except (PreparationError, OSError, KeyError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Reemplazo rechazado: {error}\n")
    print(f"{result['routeCount']} rutas validadas. Sólo paquete local; no publicación.")


if __name__ == "__main__":
    main()
