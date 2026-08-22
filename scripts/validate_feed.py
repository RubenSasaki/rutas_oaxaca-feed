#!/usr/bin/env python3
"""Read-only quality gate for the public YuuBus JSON feed.

The validator deliberately treats route geometry and stops as independent
collections. It never sorts coordinates, reconstructs a polyline from stop
IDs, or requires stop IDs to be globally unique across routes.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any


ROUTE_ID = re.compile(r"^[A-Z0-9]+$")
HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
MAX_NEW_JUMP_METERS = 3000.0
NEW_STOP_ID = re.compile(r"^(?P<route>[A-Z0-9]+)_(?P<direction>IDA|REG)_(?P<number>0[1-9]|[1-9]\d+)$")
NEW_ROUTE_TYPES = {"binnibus", "camion", "colectivo", "foraneo", "mototaxi", "taxi"}
ROUTE_KEYS = {
    "id", "nombre", "nombreEN", "origen", "origenEN", "destino", "destinoEN",
    "color", "tipo", "paradas", "trayecto", "trayectosPorSegmento", "horarios",
    "esGratuita", "notaEspecial",
}
STOP_KEYS = {
    "id", "nombre", "nombreEN", "lat", "lng", "rutasQueParanAqui",
    "esTianguis", "esEventoCultural",
}
SCHEDULE_KEYS = {
    "paradaId", "horariosLV", "horariosSab", "horariosDom", "servicioGratuito",
}
INDEX_ENTRY_REQUIRED = {"id", "version", "nombre", "color", "activa", "temporal"}
INDEX_ENTRY_ALLOWED = INDEX_ENTRY_REQUIRED | {"visibleDesde", "visibleHasta"}


class DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"clave JSON duplicada: {key}")
        result[key] = value
    return result


def _reject_non_finite(value: str) -> None:
    raise ValueError(f"número JSON no finito: {value}")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)


@dataclass
class FeedSnapshot:
    index: dict[str, Any]
    routes: dict[str, dict[str, Any]]
    route_raw: dict[str, str]
    events: dict[str, Any]
    events_raw: str


def parse_json(raw: str, label: str, report: Report) -> Any | None:
    try:
        return json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except (json.JSONDecodeError, DuplicateKeyError, ValueError) as error:
        report.error(f"{label}: JSON inválido ({error})")
        return None


def read_json(path: Path, report: Report) -> tuple[Any | None, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        report.error(f"{path}: no se pudo leer ({error})")
        return None, ""
    return parse_json(raw, str(path), report), raw


def parse_iso8601(value: Any, label: str, report: Report) -> datetime | None:
    if not isinstance(value, str) or not value:
        report.error(f"{label}: se esperaba una fecha ISO-8601 no vacía")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        report.error(f"{label}: fecha ISO-8601 inválida")
        return None
    if parsed.tzinfo is None:
        report.error(f"{label}: la fecha debe declarar zona horaria")
        return None
    return parsed


def valid_coordinate(value: Any, label: str, report: Report) -> bool:
    if (
        not isinstance(value, list)
        or len(value) != 2
        or any(not isinstance(number, (int, float)) or isinstance(number, bool) for number in value)
    ):
        report.error(f"{label}: se esperaba [lat, lng] numérico")
        return False
    latitude, longitude = value
    if (
        not math.isfinite(latitude)
        or not math.isfinite(longitude)
        or not -90 <= latitude <= 90
        or not -180 <= longitude <= 180
    ):
        report.error(f"{label}: coordenada fuera de WGS84")
        return False
    return True


def distance_meters(first: list[float], second: list[float]) -> float:
    """Great-circle distance for validation only; it never rewrites geometry."""
    earth_radius = 6_371_000.0
    first_latitude = math.radians(first[0])
    second_latitude = math.radians(second[0])
    latitude_delta = math.radians(second[0] - first[0])
    longitude_delta = math.radians(second[1] - first[1])
    haversine = (
        math.sin(latitude_delta / 2) ** 2
        + math.cos(first_latitude)
        * math.cos(second_latitude)
        * math.sin(longitude_delta / 2) ** 2
    )
    return 2 * earth_radius * math.asin(math.sqrt(haversine))


def maximum_geometry_jump(route: dict[str, Any]) -> float:
    segments = route.get("trayectosPorSegmento")
    paths = (
        [segments.get("ida"), segments.get("regreso")]
        if isinstance(segments, dict)
        else [route.get("trayecto")]
    )
    maximum = 0.0
    for path in paths:
        if not isinstance(path, list):
            continue
        for first, second in zip(path, path[1:]):
            if (
                isinstance(first, list)
                and isinstance(second, list)
                and len(first) == 2
                and len(second) == 2
                and all(
                    isinstance(value, (int, float)) and not isinstance(value, bool)
                    for value in first + second
                )
            ):
                maximum = max(maximum, distance_meters(first, second))
    return maximum


def validate_index(index: Any, label: str, report: Report) -> list[dict[str, Any]]:
    if not isinstance(index, dict):
        report.error(f"{label}: la raíz debe ser un objeto")
        return []
    if not isinstance(index.get("version"), str) or not index["version"]:
        report.error(f"{label}.version: debe ser texto no vacío")
    parse_iso8601(index.get("generatedAt"), f"{label}.generatedAt", report)
    entries = index.get("rutas")
    if not isinstance(entries, list) or not entries:
        report.error(f"{label}.rutas: debe ser una lista no vacía")
        return []

    seen: set[str] = set()
    valid_entries: list[dict[str, Any]] = []
    for position, entry in enumerate(entries):
        entry_label = f"{label}.rutas[{position}]"
        if not isinstance(entry, dict):
            report.error(f"{entry_label}: debe ser un objeto")
            continue
        keys = set(entry)
        if not INDEX_ENTRY_REQUIRED <= keys or not keys <= INDEX_ENTRY_ALLOWED:
            report.error(f"{entry_label}: no coincide con el esquema del índice")
        route_id = entry.get("id")
        if not isinstance(route_id, str) or not ROUTE_ID.fullmatch(route_id):
            report.error(f"{entry_label}.id: debe usar mayúsculas ASCII y dígitos")
            continue
        if route_id in seen:
            report.error(f"{entry_label}.id: ruta duplicada {route_id}")
        seen.add(route_id)
        valid_entries.append(entry)
        for key in ("version", "nombre", "color"):
            if not isinstance(entry.get(key), str):
                report.error(f"{entry_label}.{key}: debe ser texto")
        for key in ("activa", "temporal"):
            if not isinstance(entry.get(key), bool):
                report.error(f"{entry_label}.{key}: debe ser booleano")

        starts = entry.get("visibleDesde")
        ends = entry.get("visibleHasta")
        if starts is not None or ends is not None:
            start = parse_iso8601(starts, f"{entry_label}.visibleDesde", report)
            end = parse_iso8601(ends, f"{entry_label}.visibleHasta", report)
            if start is not None and end is not None and end < start:
                report.error(f"{entry_label}: visibleHasta precede visibleDesde")
        elif entry.get("temporal") is True:
            report.warning(
                f"{route_id}: ruta temporal heredada sin ventana visibleDesde/visibleHasta"
            )
    return valid_entries


def validate_route(route: Any, route_id: str, label: str, report: Report) -> None:
    if not isinstance(route, dict):
        report.error(f"{label}: la raíz debe ser un objeto")
        return
    if set(route) != ROUTE_KEYS:
        report.error(f"{label}: no coincide con el esquema canónico de ruta")
    if route.get("id") != route_id:
        report.error(f"{label}.id: no coincide con el índice ({route_id})")

    required_text = ("nombre", "nombreEN", "origen", "origenEN", "destino", "destinoEN", "color", "tipo")
    for key in required_text:
        if not isinstance(route.get(key), str):
            report.error(f"{label}.{key}: debe ser texto")
    if not isinstance(route.get("esGratuita"), bool):
        report.error(f"{label}.esGratuita: debe ser booleano")

    stops = route.get("paradas")
    stop_ids: set[str] = set()
    if not isinstance(stops, list) or not stops:
        report.error(f"{label}.paradas: debe ser una lista no vacía")
        stops = []
    for position, stop in enumerate(stops):
        stop_label = f"{label}.paradas[{position}]"
        if not isinstance(stop, dict):
            report.error(f"{stop_label}: debe ser un objeto")
            continue
        if set(stop) != STOP_KEYS:
            report.error(f"{stop_label}: no coincide con el esquema canónico de parada")
        stop_id = stop.get("id")
        if not isinstance(stop_id, str) or not stop_id:
            report.error(f"{stop_label}.id: debe ser texto no vacío")
        elif stop_id in stop_ids:
            report.error(f"{stop_label}.id: duplicado dentro de {route_id}: {stop_id}")
        else:
            stop_ids.add(stop_id)
        for key in ("nombre", "nombreEN"):
            if not isinstance(stop.get(key), str) or not stop[key].strip():
                report.error(f"{stop_label}.{key}: debe ser texto no vacío")
        valid_coordinate([stop.get("lat"), stop.get("lng")], stop_label, report)
        references = stop.get("rutasQueParanAqui")
        if not isinstance(references, list) or any(not isinstance(value, str) for value in references):
            report.error(f"{stop_label}.rutasQueParanAqui: debe ser una lista de IDs")
        elif route_id not in references:
            report.error(f"{stop_label}.rutasQueParanAqui: debe incluir {route_id}")
        for key in ("esTianguis", "esEventoCultural"):
            if not isinstance(stop.get(key), bool):
                report.error(f"{stop_label}.{key}: debe ser booleano")

    path = route.get("trayecto")
    if not isinstance(path, list) or len(path) < 2:
        report.error(f"{label}.trayecto: debe contener al menos dos coordenadas")
        path = []
    for position, coordinate in enumerate(path):
        valid_coordinate(coordinate, f"{label}.trayecto[{position}]", report)

    segments = route.get("trayectosPorSegmento")
    if isinstance(segments, dict) and segments:
        if set(segments) != {"ida", "regreso"}:
            report.error(f"{label}.trayectosPorSegmento: debe contener exactamente ida y regreso")
        outbound = segments.get("ida")
        inbound = segments.get("regreso")
        if not isinstance(outbound, list) or not isinstance(inbound, list):
            report.error(f"{label}.trayectosPorSegmento: ida y regreso deben ser listas")
        else:
            for direction, coordinates in (("ida", outbound), ("regreso", inbound)):
                for position, coordinate in enumerate(coordinates):
                    valid_coordinate(
                        coordinate,
                        f"{label}.trayectosPorSegmento.{direction}[{position}]",
                        report,
                    )
            # Exact comparison is intentional: drawing order comes from the
            # captured geometry, never from stop IDs or stop order.
            if path != outbound + inbound:
                report.error(
                    f"{label}: trayecto debe ser la concatenación exacta ida + regreso"
                )
    elif route_id == "RT01":
        report.warning("RT01: legado sin trayectosPorSegmento; no se usa como patrón")
    else:
        report.warning(f"{route_id}: no declara trayectosPorSegmento")

    jump = maximum_geometry_jump(route)
    if jump > MAX_NEW_JUMP_METERS:
        report.warning(
            f"{route_id}: trazo heredado contiene un salto geométrico "
            f"de {jump:.0f} m; requiere revisión de campo, no corrección automática"
        )

    schedules = route.get("horarios")
    if not isinstance(schedules, list):
        report.error(f"{label}.horarios: debe ser una lista")
        schedules = []
    seen_schedules: set[str] = set()
    for position, schedule in enumerate(schedules):
        schedule_label = f"{label}.horarios[{position}]"
        if not isinstance(schedule, dict):
            report.error(f"{schedule_label}: debe ser un objeto")
            continue
        if set(schedule) != SCHEDULE_KEYS:
            report.error(f"{schedule_label}: no coincide con el esquema canónico de horario")
        stop_id = schedule.get("paradaId")
        if stop_id not in stop_ids:
            report.error(f"{schedule_label}.paradaId: no existe en la misma ruta")
        elif stop_id in seen_schedules:
            report.error(f"{schedule_label}.paradaId: horario duplicado para {stop_id}")
        else:
            seen_schedules.add(stop_id)
        for key in ("horariosLV", "horariosSab", "horariosDom"):
            times = schedule.get(key)
            if not isinstance(times, list) or any(
                not isinstance(value, str) or not TIME.fullmatch(value) for value in times
            ):
                report.error(f"{schedule_label}.{key}: debe contener horas HH:mm válidas")
        if not isinstance(schedule.get("servicioGratuito"), bool):
            report.error(f"{schedule_label}.servicioGratuito: debe ser booleano")


def validate_new_route(
    route: dict[str, Any], entry: dict[str, Any], route_id: str, report: Report
) -> None:
    label = f"ruta nueva {route_id}"
    if entry.get("activa") is not True:
        report.error(f"{label}: debe publicarse activa")
    if not isinstance(entry.get("color"), str) or not HEX_COLOR.fullmatch(entry["color"]):
        report.error(f"{label}: color del índice debe usar #RRGGBB")
    if route.get("tipo") not in NEW_ROUTE_TYPES:
        report.error(f"{label}: tipo no canónico ({route.get('tipo')!r})")
    segments = route.get("trayectosPorSegmento")
    if not isinstance(segments, dict) or set(segments) != {"ida", "regreso"}:
        report.error(f"{label}: requiere segmentos ida y regreso")
    jump = maximum_geometry_jump(route)
    if jump > MAX_NEW_JUMP_METERS:
        report.error(
            f"{label}: contiene un salto geométrico de {jump:.0f} m; "
            "revisa el orden de captura"
        )

    directions: set[str] = set()
    numbers: dict[str, list[int]] = {"IDA": [], "REG": []}
    saw_inbound = False
    for stop in route.get("paradas") or []:
        if not isinstance(stop, dict):
            continue
        match = NEW_STOP_ID.fullmatch(str(stop.get("id", "")))
        if match is None or match.group("route") != route_id:
            report.error(
                f"{label}: parada {stop.get('id')!r} debe usar {route_id}_IDA_NN o {route_id}_REG_NN"
            )
            continue
        direction = match.group("direction")
        directions.add(direction)
        numbers[direction].append(int(match.group("number")))
        if direction == "REG":
            saw_inbound = True
        elif saw_inbound:
            report.error(f"{label}: las paradas IDA no pueden aparecer después de REG")
    if directions != {"IDA", "REG"}:
        report.error(f"{label}: requiere al menos una parada IDA y una REG")
    for direction, values in numbers.items():
        if values and values != list(range(1, len(values) + 1)):
            report.error(f"{label}: numeración {direction} debe iniciar en 01 y no tener saltos")

    version = entry.get("version")
    expected_suffix = f"-{len(route.get('paradas') or [])}"
    if not isinstance(version, str) or not version.endswith(expected_suffix):
        report.error(f"{label}: version debe terminar en {expected_suffix}")
    if entry.get("temporal") is True and (
        entry.get("visibleDesde") is None or entry.get("visibleHasta") is None
    ):
        report.error(f"{label}: una ruta temporal requiere ventana de visibilidad")


def validate_events(events_feed: Any, root: Path, known_routes: set[str], report: Report) -> set[tuple[str, str]]:
    if not isinstance(events_feed, dict):
        report.error("eventos.json: la raíz debe ser un objeto")
        return set()
    if not isinstance(events_feed.get("version"), (str, int)):
        report.error("eventos.json.version: debe ser texto o número")
    parse_iso8601(events_feed.get("updatedAt"), "eventos.json.updatedAt", report)
    events = events_feed.get("eventos")
    if not isinstance(events, list):
        report.error("eventos.json.eventos: debe ser una lista")
        return set()

    seen: set[str] = set()
    unknown_references: set[tuple[str, str]] = set()
    for position, event in enumerate(events):
        label = f"eventos.json.eventos[{position}]"
        if not isinstance(event, dict):
            report.error(f"{label}: debe ser un objeto")
            continue
        event_id = event.get("id")
        if not isinstance(event_id, str) or not event_id:
            report.error(f"{label}.id: debe ser texto no vacío")
            continue
        if event_id in seen:
            report.error(f"{label}.id: evento duplicado {event_id}")
        seen.add(event_id)
        references = event.get("rutas") or []
        special = event.get("transporteEspecial") or {}
        if not isinstance(references, list) or any(not isinstance(value, str) for value in references):
            report.error(f"{label}.rutas: debe ser una lista de IDs")
            references = []
        if not isinstance(special, dict):
            report.error(f"{label}.transporteEspecial: debe ser un objeto")
            special = {}
        special_routes = special.get("rutaIds") or []
        if not isinstance(special_routes, list) or any(
            not isinstance(value, str) for value in special_routes
        ):
            report.error(f"{label}.transporteEspecial.rutaIds: debe ser una lista de IDs")
            special_routes = []
        for route_id in set(references + special_routes) - known_routes:
            unknown_references.add((event_id, route_id))

        image_path = event.get("imagenPath")
        if image_path is not None:
            if not isinstance(image_path, str) or not image_path:
                report.error(f"{label}.imagenPath: debe ser texto no vacío")
            else:
                relative = PurePosixPath(image_path)
                if relative.is_absolute() or ".." in relative.parts:
                    report.error(f"{label}.imagenPath: ruta insegura")
                elif not (root / "eventos" / "imagenes" / Path(*relative.parts)).is_file():
                    report.error(f"{label}.imagenPath: archivo inexistente {image_path}")
        for key in ("fechaInicio", "fechaFin"):
            value = event.get(key)
            if value is not None:
                try:
                    datetime.fromisoformat(value)
                except (TypeError, ValueError):
                    report.error(f"{label}.{key}: fecha inválida")

    for event_id, route_id in sorted(unknown_references):
        report.warning(f"{event_id}: referencia histórica a ruta inexistente {route_id}")
    return unknown_references


def load_snapshot(root: Path, report: Report) -> FeedSnapshot | None:
    index, _ = read_json(root / "rutas" / "indice.json", report)
    entries = validate_index(index, "rutas/indice.json", report)
    routes: dict[str, dict[str, Any]] = {}
    route_raw: dict[str, str] = {}
    ids = {entry["id"] for entry in entries if isinstance(entry.get("id"), str)}
    for route_id in sorted(ids):
        route, raw = read_json(root / "rutas" / f"{route_id}.json", report)
        if isinstance(route, dict):
            routes[route_id] = route
            route_raw[route_id] = raw
            validate_route(route, route_id, f"rutas/{route_id}.json", report)
            entry = next(item for item in entries if item.get("id") == route_id)
            if route.get("nombre") != entry.get("nombre"):
                report.error(f"{route_id}: nombre no coincide entre índice y ruta")
            if route.get("color") != entry.get("color"):
                report.error(f"{route_id}: color no coincide entre índice y ruta")

    actual_files = {path.stem for path in (root / "rutas").glob("*.json") if path.name != "indice.json"}
    for route_id in sorted(ids - actual_files):
        report.error(f"rutas/{route_id}.json: falta archivo indexado")
    for route_id in sorted(actual_files - ids):
        report.error(f"rutas/{route_id}.json: archivo no incluido en el índice")

    events, events_raw = read_json(root / "eventos.json", report)
    if index is None or events is None:
        return None
    validate_events(events, root, ids, report)
    return FeedSnapshot(index=index, routes=routes, route_raw=route_raw, events=events, events_raw=events_raw)


def _git_show(base_ref: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "show", f"{base_ref}:{path}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    return result.stdout if result.returncode == 0 else None


def load_git_snapshot(base_ref: str, report: Report) -> FeedSnapshot | None:
    index_raw = _git_show(base_ref, "rutas/indice.json")
    events_raw = _git_show(base_ref, "eventos.json")
    if index_raw is None or events_raw is None:
        report.error(f"{base_ref}: no contiene el feed base esperado")
        return None
    index = parse_json(index_raw, f"{base_ref}:rutas/indice.json", report)
    events = parse_json(events_raw, f"{base_ref}:eventos.json", report)
    if not isinstance(index, dict) or not isinstance(events, dict):
        return None
    routes: dict[str, dict[str, Any]] = {}
    route_raw: dict[str, str] = {}
    for entry in index.get("rutas") or []:
        if not isinstance(entry, dict) or not isinstance(entry.get("id"), str):
            continue
        route_id = entry["id"]
        raw = _git_show(base_ref, f"rutas/{route_id}.json")
        if raw is None:
            continue
        route = parse_json(raw, f"{base_ref}:rutas/{route_id}.json", report)
        if isinstance(route, dict):
            routes[route_id] = route
            route_raw[route_id] = raw
    return FeedSnapshot(index=index, routes=routes, route_raw=route_raw, events=events, events_raw=events_raw)


def compare_with_base(current: FeedSnapshot, base: FeedSnapshot, root: Path, report: Report) -> None:
    current_entries = {entry["id"]: entry for entry in current.index.get("rutas") or [] if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
    base_entries = {entry["id"]: entry for entry in base.index.get("rutas") or [] if isinstance(entry, dict) and isinstance(entry.get("id"), str)}
    removed = set(base_entries) - set(current_entries)
    for route_id in sorted(removed):
        report.error(f"{route_id}: eliminar una ruta publicada no está permitido por esta puerta")

    added = set(current_entries) - set(base_entries)
    for route_id in sorted(added):
        route = current.routes.get(route_id)
        if route is not None:
            validate_new_route(route, current_entries[route_id], route_id, report)

    changed_routes = {
        route_id
        for route_id in set(current.route_raw) & set(base.route_raw)
        if current.route_raw[route_id] != base.route_raw[route_id]
    }
    for route_id in sorted(changed_routes):
        if current_entries[route_id].get("version") == base_entries[route_id].get("version"):
            report.error(f"{route_id}: cambió su JSON sin bumpear version en rutas/indice.json")
        if route_id != "RT01" and not current.routes[route_id].get("trayectosPorSegmento"):
            report.error(f"{route_id}: una ruta modificada debe conservar segmentos ida/regreso")
        current_jump = maximum_geometry_jump(current.routes[route_id])
        base_jump = maximum_geometry_jump(base.routes[route_id])
        if (
            current_jump > MAX_NEW_JUMP_METERS
            and current_jump > base_jump + 50.0
        ):
            report.error(
                f"{route_id}: la modificación introduce un salto geométrico "
                f"de {current_jump:.0f} m (antes {base_jump:.0f} m)"
            )

    changed_entries = {
        route_id
        for route_id in set(current_entries) & set(base_entries)
        if current_entries[route_id] != base_entries[route_id]
    }
    for route_id in sorted(changed_entries):
        entry = current_entries[route_id]
        if entry.get("temporal") is True and (
            entry.get("visibleDesde") is None or entry.get("visibleHasta") is None
        ):
            report.error(f"{route_id}: una ruta temporal modificada requiere ventana de visibilidad")

    index_changed = current.index != base.index
    if added or removed or changed_routes or index_changed:
        if current.index.get("version") == base.index.get("version"):
            report.error("rutas/indice.json: cambios de rutas requieren nueva version global")
        if current.index.get("generatedAt") == base.index.get("generatedAt"):
            report.error("rutas/indice.json: cambios de rutas requieren actualizar generatedAt")

    if current.events_raw != base.events_raw:
        if current.events.get("version") == base.events.get("version"):
            report.error("eventos.json: cambios requieren bumpear version")
        if current.events.get("updatedAt") == base.events.get("updatedAt"):
            report.error("eventos.json: cambios requieren actualizar updatedAt")

    known_routes = set(current_entries)
    current_unknown_report = Report()
    base_unknown_report = Report()
    current_unknown = validate_events(current.events, root, known_routes, current_unknown_report)
    base_known = set(base_entries)
    # The file check is irrelevant for a git snapshot; use the current root and
    # compare only route references here.
    base_unknown = validate_events(base.events, root, base_known, base_unknown_report)
    for event_id, route_id in sorted(current_unknown - base_unknown):
        report.error(f"{event_id}: nueva referencia a ruta inexistente {route_id}")


def print_report(report: Report) -> None:
    for warning in report.warnings:
        print(f"WARNING: {warning}")
    for error in report.errors:
        print(f"ERROR: {error}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="Valida el feed JSON de YuuBus sin modificarlo")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--base-ref", help="commit/rama base para validar versionado diferencial")
    args = parser.parse_args()

    root = args.root.resolve()
    report = Report()
    current = load_snapshot(root, report)
    if current is not None and args.base_ref:
        base = load_git_snapshot(args.base_ref, report)
        if base is not None:
            compare_with_base(current, base, root, report)

    print_report(report)
    if report.errors:
        print(f"Feed rechazado: {len(report.errors)} error(es), {len(report.warnings)} aviso(s).", file=sys.stderr)
        return 1
    route_count = len(current.routes) if current is not None else 0
    event_count = len(current.events.get("eventos") or []) if current is not None else 0
    print(f"Feed válido: {route_count} rutas, {event_count} eventos, {len(report.warnings)} aviso(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
