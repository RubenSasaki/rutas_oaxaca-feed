import 'dart:convert';
import '../models/lugar.dart';

/// The editorial GeoJSON contract. This parser is also used by the local CLI.
/// It validates the entire snapshot, including inactive records, before returning.
class PlacesGeoJson {
  static const maxBytes = 2 * 1024 * 1024;
  static const fields = {
    'id',
    'nombre',
    'nombreEN',
    'categoria',
    'aliases',
    'keywords',
    'municipio',
    'localidad',
    'fuente',
    'activo',
    'notas',
  };
  static const categories = {
    'zonaUrbana': TipoLugar.zonaUrbana,
    'pueblo': TipoLugar.pueblo,
    'mercado': TipoLugar.mercado,
    'institucion': TipoLugar.institucion,
    'cultura': TipoLugar.cultura,
    'eventoFijo': TipoLugar.eventoFijo,
  };

  static List<Lugar> parse(String raw) {
    if (utf8.encode(raw).length > maxBytes) {
      throw const FormatException('Places excede 2 MiB.');
    }
    final data = jsonDecode(raw);
    if (data is! Map ||
        data['type'] != 'FeatureCollection' ||
        data['features'] is! List ||
        (data['features'] as List).length > 5000) {
      throw const FormatException(
          'Se requiere FeatureCollection (máximo 5000).');
    }
    if (data.containsKey('crs')) {
      final crs = data['crs'];
      final name = crs is Map && crs['properties'] is Map
          ? crs['properties']['name']
          : null;
      if (name != 'urn:ogc:def:crs:OGC:1.3:CRS84' &&
          name != 'urn:ogc:def:crs:EPSG::4326') {
        throw const FormatException('Exportar Places en WGS84, EPSG:4326.');
      }
    }
    final ids = <String>{};
    final places = <Lugar>[];
    for (final feature in data['features']) {
      if (feature is! Map ||
          feature['type'] != 'Feature' ||
          feature['properties'] is! Map ||
          feature['geometry'] is! Map) {
        throw const FormatException('Feature inválido.');
      }
      final p = feature['properties'] as Map;
      if (p.length != fields.length || !fields.every(p.containsKey)) {
        throw const FormatException(
            'Properties debe contener exactamente el contrato Places V1.');
      }
      String requiredText(String key) {
        final value = p[key];
        if (value is! String || value.trim().isEmpty || value.length > 2000) {
          throw FormatException('$key debe ser texto no vacío (máximo 2000).');
        }
        return value.trim();
      }

      String? optionalText(String key) {
        final value = p[key];
        if (value == null) return null;
        if (value is! String || value.length > 2000) {
          throw FormatException('$key debe ser texto o null (máximo 2000).');
        }
        return value.trim().isEmpty ? null : value.trim();
      }

      List<String> separated(String key) {
        final value = p[key];
        if (value is! String || value.length > 8000) {
          throw FormatException('$key debe ser texto separado por |.');
        }
        if (value.trim().isEmpty) return const [];
        final items = value.split('|').map((v) => v.trim()).toList();
        if (items.length > 64 ||
            items.any((v) => v.isEmpty || v.length > 200) ||
            items.toSet().length != items.length) {
          throw FormatException(
              '$key contiene elementos vacíos, repetidos o demasiado largos.');
        }
        return List.unmodifiable(items);
      }

      final id = requiredText('id');
      if (!RegExp(r'^PL_[A-Z0-9]+(?:_[A-Z0-9]+)*$').hasMatch(id) ||
          id.length > 128 ||
          !ids.add(id)) {
        throw FormatException('ID inválido o duplicado: $id');
      }
      final category = requiredText('categoria');
      final type = categories[category];
      if (type == null) {
        throw FormatException('Categoría no soportada: $category');
      }
      if (p['activo'] is! bool) {
        throw FormatException('$id: activo debe ser booleano.');
      }
      final g = feature['geometry'] as Map;
      final coords = g['coordinates'];
      if (g['type'] != 'Point' ||
          coords is! List ||
          coords.length != 2 ||
          coords.any((v) => v is! num || !v.isFinite) ||
          (coords[0] as num).abs() > 180 ||
          (coords[1] as num).abs() > 90) {
        throw FormatException('$id: Point requiere [longitud, latitud] WGS84.');
      }
      places.add(Lugar(
          id: id,
          nombre: requiredText('nombre'),
          nombreEN: optionalText('nombreEN'),
          alias: separated('aliases'),
          keywords: separated('keywords'),
          tipo: type,
          municipio: optionalText('municipio'),
          localidad: optionalText('localidad'),
          fuente: requiredText('fuente'),
          activo: p['activo'] as bool,
          notas: optionalText('notas'),
          longitud: (coords[0] as num).toDouble(),
          latitud: (coords[1] as num).toDouble(),
          // Presentation/spatial defaults stay internal, outside the feed schema.
          radioMetros: 300,
          paradasCercanas: const [],
          icono: '📍'));
    }
    return List.unmodifiable(places);
  }
}
