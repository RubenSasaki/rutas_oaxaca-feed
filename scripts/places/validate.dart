import 'dart:io';
import 'lib/utils/places_geojson.dart';

Future<void> main(List<String> args) async {
  if (args.length != 1) {
    stderr.writeln(
        'Uso: dart run tool/validar_places_geojson.dart archivo.geojson');
    exitCode = 64;
    return;
  }
  try {
    final file = File(args.single);
    if (await file.length() > PlacesGeoJson.maxBytes) {
      throw const FormatException('Places excede 2 MiB.');
    }
    final places = PlacesGeoJson.parse(await file.readAsString());
    stdout.writeln('VALIDO: ${places.length} registros; '
        '${places.where((p) => p.activo).length} activos. Sin publicar ni modificar archivos.');
  } catch (error) {
    stderr.writeln('INVALIDO: $error');
    exitCode = 1;
  }
}
