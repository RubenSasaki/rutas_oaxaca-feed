enum TipoLugar {
  zonaUrbana,
  pueblo,
  mercado,
  institucion,
  cultura,
  eventoFijo,
  colaborador,
}

class Lugar {
  final String id;
  final String nombre;
  final String? nombreEN;
  final List<String> alias;
  final List<String> keywords;
  final String? municipio;
  final String? localidad;
  final String? fuente;
  final bool activo;
  final String? notas;
  final TipoLugar tipo;
  final double latitud;
  final double longitud;
  final double radioMetros;
  final List<String> paradasCercanas;
  final String icono;
  final bool esDestacado;
  final String? descripcionCorta;
  final String? descripcionCortaEN;

  const Lugar({
    required this.id,
    required this.nombre,
    this.nombreEN,
    required this.alias,
    this.keywords = const [],
    this.municipio,
    this.localidad,
    this.fuente,
    this.activo = true,
    this.notas,
    required this.tipo,
    required this.latitud,
    required this.longitud,
    required this.radioMetros,
    required this.paradasCercanas,
    required this.icono,
    this.esDestacado = false,
    this.descripcionCorta,
    this.descripcionCortaEN,
  });

  String nombreLocalizado(String idioma) =>
      idioma == 'en' && nombreEN != null ? nombreEN! : nombre;

  String? descripcionLocalizada(String idioma) =>
      idioma == 'en' ? descripcionCortaEN : descripcionCorta;
}

class Colaborador extends Lugar {
  final String nombreComercial;
  final String paradaId;
  final int metrosALaPuerta;
  final String? instrucciones;
  final String? instruccionesEN;
  final String? logoUrl;
  final String? urlWeb;
  final bool esPremium;
  final TipoColaborador tipoNegocio;

  const Colaborador({
    required super.id,
    required super.nombre,
    super.nombreEN,
    required super.alias,
    required super.latitud,
    required super.longitud,
    required super.radioMetros,
    required super.paradasCercanas,
    required super.icono,
    super.esDestacado,
    super.descripcionCorta,
    super.descripcionCortaEN,
    required this.nombreComercial,
    required this.paradaId,
    required this.metrosALaPuerta,
    this.instrucciones,
    this.instruccionesEN,
    this.logoUrl,
    this.urlWeb,
    this.esPremium = false,
    required this.tipoNegocio,
  }) : super(tipo: TipoLugar.colaborador);

  String? instruccionesLocalizadas(String idioma) =>
      idioma == 'en' ? instruccionesEN : instrucciones;
}

enum TipoColaborador {
  hotel,
  restaurante,
  tourOperador,
  tienda,
  museo,
  otro,
}
