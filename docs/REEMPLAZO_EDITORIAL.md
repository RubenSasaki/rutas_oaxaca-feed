# Paquete local de reemplazo editorial

No publica ni modifica el feed de entrada. Se usa después del conversor
`tool/preparar_reemplazo_ruta.dart` del repositorio integral. Las propuestas
requieren captura validada, metadatos explícitos y las huellas originales.

Base de herramientas: `35bc4329da1c5aba7f7644fca1fed7bfeb2489d1` (línea SHA
separada; este trabajo no implica su integración a main).
Datos usados en el ensayo: `2edd8d8f0355f5c713c96c2aa09347cded2e0090`.

## Plan y ejecución

Crear un JSON con estas claves exactas, usando valores editoriales aprobados:

```json
{
  "schemaVersion": 1,
  "operation": "replace-published-routes",
  "baseCommit": "COMMIT_COMPLETO_DE_40_CARACTERES",
  "reviewedBy": "REVISOR",
  "reference": "REFERENCIA_DE_REVISION",
  "version": "VERSION_GLOBAL_NUEVA",
  "generatedAt": "INSTANTE_EDITORIAL_ISO8601",
  "routes": [
    {
      "id": "RC05",
      "version": "PREFIJO_EDITORIAL_APROBADO-78",
      "expectedBaseSha256": "SHA256_DE_LOS_BYTES_ANTERIORES",
      "proposalSha256": "SHA256_DE_PROVENANCE_JSON"
    }
  ]
}
```

Los marcadores del ejemplo no son datos válidos. RT01 usa sufijo `-70`.
No se generan fechas ni se obtiene autorización a partir de estas cadenas.

```bash
python3 -B scripts/prepare_route_replacement.py --repo /ruta/al/feed --proposals /salida/propuestas --plan /ruta/plan-editorial.json --output /salida/nueva/revision
python3 -B -m unittest discover -s tests -v
python3 -B scripts/validate_feed.py --root /salida/nueva/revision/candidate --base-ref COMMIT_COMPLETO
```

La última orden debe ejecutarse desde el checkout que contiene el commit.
La salida ha de ser nueva y estar fuera de repositorios productivos/feed.
Una propuesta por ID contiene exactamente `route.json`, `capture.json`,
`capture-plan.json`, `metadata.json` y `provenance.json`.

## Garantías comprobables

- Sólo sustituye IDs existentes de una allowlist; comprueba base y propuesta.
- SHA se calcula sobre bytes finales, incluido newline, sin volver a serializar
  las rutas. Un hash nuevo no exime de aumentar la versión editorial.
- Exige idiomas ingleses null o texto válido; español obligatorio. Reemplazos
  estrictos con IDs/sentidos/orden/conteos, geometría canónica y `horarios: []`.
- Conserva orden, `activa`, temporalidad y ventanas del índice. El resto del
  catálogo, Eventos y lugares conservan sus bytes. No elimina archivos.
- `before/` conserva historial y horarios anteriores. El informe detecta IDs
  que ahora representan atributos físicos distintos y referencias antiguas.
- No fusiona paradas ni reasigna horarios o relaciones entre rutas. Los
  favoritos del dispositivo requieren decisión separada antes de publicar.
- Valida el catálogo completo y su diff antes de terminar `READY_FOR_REVIEW`.
  Sin marcador la salida es incompleta. El marcador no autoriza publicación.

`review_capture(base, capture)` permite producir un informe sin metadatos;
marca expresamente que no es candidato de feed. La evidencia de entrada
queda ligada por SHA, no constituye una firma de aprobación editorial.

## Puertas antes de publicar

Revisar diff, referencias reutilizadas, metadatos oficiales, nuevas versiones
y fecha editorial. Distribuir antes un cliente compatible con traducciones
nulas: el contrato integral base `3648208` las rechaza. Revisar el respaldo
offline histórico y la transición de favoritos. Obtener autorización explícita
del propietario. Este script no incluye ninguna operación de publicación.
