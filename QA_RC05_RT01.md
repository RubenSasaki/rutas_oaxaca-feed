# ENSAYO QA — no integrar a main

Rama `codex/qa-rc05-rt01-20261123`, autorizada el 23/11/2026 para una APK QA
aislada. No cambia el endpoint main consumido por usuarios de producción.

- Base de datos: `2edd8d8f0355f5c713c96c2aa09347cded2e0090`.
- Catálogo completo: 38 rutas, 2.665 paradas.
- RC05: 39 IDA + 39 REGRESO, Base San Pablo Etla → Morelos.
- RT01: 35 IDA + 35 REGRESO, Yuroo Viguera ↔ Yuroo Xoxocotlán.
- Las otras 36 rutas conservan exactamente sus bytes.
- Capturas originales intactas; sin fusión de paradas o remapeo de horarios.
- Nombres de ruta rotulados `[QA]`, metadatos provisionales, traducciones null,
  horarios vacíos. El timestamp del índice es de generación del ensayo, no
  una fecha de publicación oficial.
- Versiones QA nuevas y SHA-256 de los bytes completos de ambos documentos.

No publicar estos JSON a clientes anteriores: requieren el nuevo contrato de
traducciones nulas. Las asociaciones de favoritos de los IDs antiguos no se
deben migrar automáticamente. La APK de este ensayo separa su persistencia
de rutas y favoritos, y conserva la identidad Android QA independiente de Play.

Esta rama puede leerse sin credenciales por HTTPS en raw.githubusercontent.com.
Su accesibilidad pública no constituye aprobación editorial. No fusionar esta
PR ni mover main como parte del ensayo.
