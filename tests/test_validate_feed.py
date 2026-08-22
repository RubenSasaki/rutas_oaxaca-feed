import json
import tempfile
import unittest
from pathlib import Path

from scripts.validate_feed import (
    FeedSnapshot,
    Report,
    compare_with_base,
    load_snapshot,
    parse_json,
    validate_new_route,
)


def _route(route_id="RA99"):
    ida = [[17.06, -96.72], [17.061, -96.719]]
    regreso = [[17.061, -96.719], [17.06, -96.72]]
    stops = []
    for stop_id, name, coordinate in (
        (f"{route_id}_IDA_01", "ORIGEN", ida[0]),
        (f"{route_id}_REG_01", "REGRESO", regreso[0]),
    ):
        stops.append(
            {
                "id": stop_id,
                "nombre": name,
                "nombreEN": name,
                "lat": coordinate[0],
                "lng": coordinate[1],
                "rutasQueParanAqui": [route_id],
                "esTianguis": False,
                "esEventoCultural": False,
            }
        )
    return {
        "id": route_id,
        "nombre": "RUTA DE PRUEBA",
        "nombreEN": "TEST ROUTE",
        "origen": "ORIGEN",
        "origenEN": "ORIGIN",
        "destino": "DESTINO",
        "destinoEN": "DESTINATION",
        "color": "#123456",
        "tipo": "camion",
        "paradas": stops,
        "trayecto": ida + regreso,
        "trayectosPorSegmento": {"ida": ida, "regreso": regreso},
        "horarios": [],
        "esGratuita": False,
        "notaEspecial": None,
    }


def _entry(route_id="RA99"):
    return {
        "id": route_id,
        "version": "2026-08-21-2",
        "nombre": "RUTA DE PRUEBA",
        "color": "#123456",
        "activa": True,
        "temporal": False,
    }


class FeedValidatorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "rutas").mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def write_feed(self, route=None, entry=None, events=None):
        route = route or _route()
        entry = entry or _entry()
        index = {
            "version": "2026-08-21",
            "generatedAt": "2026-08-21T12:00:00-06:00",
            "rutas": [entry],
        }
        (self.root / "rutas" / "indice.json").write_text(json.dumps(index))
        (self.root / "rutas" / f"{route['id']}.json").write_text(json.dumps(route))
        (self.root / "eventos.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "updatedAt": "2026-08-21T12:00:00-06:00",
                    "eventos": events or [],
                }
            )
        )

    def test_valid_feed_passes_without_warnings(self):
        self.write_feed()
        report = Report()
        snapshot = load_snapshot(self.root, report)
        self.assertIsNotNone(snapshot)
        self.assertEqual([], report.errors)
        self.assertEqual([], report.warnings)

    def test_duplicate_json_keys_are_rejected(self):
        report = Report()
        parsed = parse_json('{"id":"RA01","id":"RA02"}', "ruta", report)
        self.assertIsNone(parsed)
        self.assertIn("clave JSON duplicada", report.errors[0])

    def test_non_finite_json_numbers_are_rejected(self):
        report = Report()
        parsed = parse_json('{"lat":NaN}', "ruta", report)
        self.assertIsNone(parsed)
        self.assertIn("no finito", report.errors[0])

    def test_segment_order_must_match_path_exactly(self):
        route = _route()
        route["trayecto"][0] = [17.5, -96.5]
        self.write_feed(route=route)
        report = Report()
        load_snapshot(self.root, report)
        self.assertTrue(any("concatenación exacta" in error for error in report.errors))

    def test_stop_ids_only_need_to_be_unique_inside_their_route(self):
        route = _route()
        route["paradas"][1]["id"] = route["paradas"][0]["id"]
        self.write_feed(route=route)
        report = Report()
        load_snapshot(self.root, report)
        self.assertTrue(any("duplicado dentro" in error for error in report.errors))

    def test_historical_unknown_event_route_is_visible_as_warning(self):
        self.write_feed(events=[{"id": "evento", "rutas": ["RA01"]}])
        report = Report()
        load_snapshot(self.root, report)
        self.assertEqual([], report.errors)
        self.assertTrue(any("ruta inexistente RA01" in warning for warning in report.warnings))

    def test_new_route_requires_canonical_stop_sequence(self):
        route = _route()
        route["paradas"][0]["id"] = "RA99_IDA_02"
        report = Report()
        validate_new_route(route, _entry(), "RA99", report)
        self.assertTrue(any("numeración IDA" in error for error in report.errors))

    def test_new_route_keeps_outbound_before_inbound(self):
        route = _route()
        route["paradas"] = list(reversed(route["paradas"]))
        report = Report()
        validate_new_route(route, _entry(), "RA99", report)
        self.assertTrue(any("IDA no pueden aparecer" in error for error in report.errors))

    def test_new_route_rejects_large_geometry_jump_without_reordering_it(self):
        route = _route()
        route["trayectosPorSegmento"]["ida"][1] = [18.061, -97.719]
        route["trayecto"] = (
            route["trayectosPorSegmento"]["ida"]
            + route["trayectosPorSegmento"]["regreso"]
        )
        report = Report()
        validate_new_route(route, _entry(), "RA99", report)
        self.assertTrue(any("salto geométrico" in error for error in report.errors))

    def test_changed_route_requires_index_version_bump(self):
        self.write_feed()
        current_report = Report()
        current = load_snapshot(self.root, current_report)
        self.assertIsNotNone(current)
        base = FeedSnapshot(
            index=json.loads(json.dumps(current.index)),
            routes=json.loads(json.dumps(current.routes)),
            route_raw={"RA99": "contenido anterior"},
            events=json.loads(json.dumps(current.events)),
            events_raw=current.events_raw,
        )
        report = Report()
        compare_with_base(current, base, self.root, report)
        self.assertTrue(any("sin bumpear version" in error for error in report.errors))

    def test_changed_route_cannot_introduce_a_new_large_geometry_jump(self):
        self.write_feed()
        base_report = Report()
        base = load_snapshot(self.root, base_report)
        self.assertIsNotNone(base)

        route = _route()
        route["trayectosPorSegmento"]["ida"][1] = [18.061, -97.719]
        route["trayecto"] = (
            route["trayectosPorSegmento"]["ida"]
            + route["trayectosPorSegmento"]["regreso"]
        )
        entry = _entry()
        entry["version"] = "2026-08-22-2"
        self.write_feed(route=route, entry=entry)
        index = json.loads((self.root / "rutas" / "indice.json").read_text())
        index["version"] = "2026-08-22"
        index["generatedAt"] = "2026-08-22T12:00:00-06:00"
        (self.root / "rutas" / "indice.json").write_text(json.dumps(index))

        current_report = Report()
        current = load_snapshot(self.root, current_report)
        self.assertIsNotNone(current)
        report = Report()
        compare_with_base(current, base, self.root, report)
        self.assertTrue(any("introduce un salto geométrico" in error for error in report.errors))


if __name__ == "__main__":
    unittest.main()
