# API de GRUALINK: contrato público (v1)

Este documento es el contrato que implementa cada repositorio de ejemplo, en cualquier lenguaje. Describe
solo lo que un integrador puede observar desde fuera. Versión en inglés: [API.md](API.md). Guía pública:
<https://grualink.mx/api>.

- Dirección base: `https://grualink.mx/api/v1`
- Formato: JSON, UTF-8. Las fechas son ISO 8601 en UTC.
- Los nombres de campos, los códigos y los valores enumerados son los mismos en todos los lenguajes. No
  se traducen.

## 1. Autenticación

Toda petición, salvo `GET /health`, lleva la llave que te entrega la empresa de grúas con la que
trabajas:

```
Authorization: Bearer glk_test_...
```

| Llave | Uso |
|---|---|
| `glk_test_...` | Integración y pruebas. Sus solicitudes nunca se convierten en servicios reales. |
| `glk_live_...` | Producción. Es secreta: solo desde tu servidor. |

Reglas:

- La llave va **solo** en la cabecera `Authorization`. Una petición con la llave en la URL (o con un
  parámetro llamado `key`, `api_key`, `token`, `access_token`, `auth` o `authorization`) se rechaza con
  `400 key_in_query`, también en `/health`.
- Una llave ausente, inválida, vencida o revocada recibe siempre el mismo `401 unauthorized`, con
  `WWW-Authenticate: Bearer`.
- Una llave real enviada desde un navegador (la petición trae la cabecera `Origin`) recibe
  `403 browser_not_allowed`.
- Nunca registres la llave, nunca la pongas en un mensaje de error, nunca la subas al repositorio. Léela
  de la variable de entorno `GRUALINK_API_KEY`.

## 2. Rutas

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/health` | Comprueba que el servicio responde. Sin llave. `200 {"status":"ok","version":"v1"}` |
| `POST` | `/service-requests` | Envía una solicitud de servicio. `201` (creada) o `200` (repetida) |
| `GET` | `/service-requests/{id}` | Devuelve una solicitud. `200` |
| `GET` | `/service-requests?external_ref=...` | Busca tus solicitudes por tu referencia (hasta 20, la más reciente primero). `200` |

Una ruta que existe llamada con otro método responde `405 method_not_allowed`; cualquier otra ruta
responde `404 not_found`.

## 3. Crear una solicitud de servicio

`POST /service-requests`

### Cabeceras de la petición

| Cabecera | Obligatoria | Valor |
|---|---|---|
| `Authorization` | sí | `Bearer <llave>` |
| `Content-Type` | sí | `application/json` (se aceptan parámetros como `; charset=utf-8`) |
| `Idempotency-Key` | sí | De 1 a 255 caracteres ASCII visibles (`0x21` a `0x7E`, sin espacios). Ver sección 4 |

### Cuerpo

Un objeto JSON de **16 KB** como máximo (16,384 bytes). Solo se aceptan los campos de la tabla: uno
desconocido, en cualquier nivel, se rechaza con `validation_error` (`unknown_field`). `null` equivale a
omitir el campo.

| Campo | Tipo | Obligatorio | Límites |
|---|---|---|---|
| `external_ref` | texto | no (1) | hasta 64 caracteres. Tu referencia (siniestro, expediente). |
| `contact` | objeto | **sí** | |
| `contact.name` | texto | **sí** | hasta 120 caracteres |
| `contact.phone` | texto | **sí** | hasta 40 caracteres tal como se envía. Formato internacional (`+52...`); ver reglas de teléfono |
| `pickup` | objeto | **sí** | |
| `pickup.address` | texto | **sí** | hasta 500 caracteres |
| `pickup.lat` | número | no (2) | de -90 a 90 |
| `pickup.lng` | número | no (2) | de -180 a 180 |
| `destination` | objeto | no | |
| `destination.address` | texto | **sí** si se envía `destination` | hasta 500 caracteres |
| `destination.lat` / `destination.lng` | número | no (2) | mismos rangos |
| `vehicle` | objeto | no | |
| `vehicle.make` | texto | no | hasta 40 caracteres |
| `vehicle.model` | texto | no | hasta 60 caracteres |
| `vehicle.year` | entero | no | de 1900 al año actual + 2. También se acepta un texto de 4 dígitos |
| `vehicle.color` | texto | no | hasta 30 caracteres |
| `vehicle.plate` | texto | no | se guarda en mayúsculas, sin espacios ni puntos; después, de 1 a 15 de `A-Z 0-9 -` |
| `vehicle.type` | texto | no | `auto`, `camioneta`, `motocicleta`, `camion`, `otro` |
| `service` | objeto | no | |
| `service.reason` | texto | no | hasta 200 caracteres |
| `service.category` | texto | no | `vial`, `colision` |
| `service.required_class` | texto | no | `A`, `B`, `C`, `D` |
| `service.required_accessories` | lista de textos | no | hasta 12, sin repetidos. Valores: `dolly`, `winch`, `cadenas`, `eslingas`, `patines`, `rampas`, `barra_tiro`, `extension_pluma`, `gato_hidraulico`, `pasacorriente`, `compresor`, `luces_remolque` |
| `notes` | texto | no | hasta 1,000 caracteres y 20 líneas |

(1) Obligatoria cuando tu cuenta con la empresa exige número de expediente. Si falta en ese caso la
respuesta es `422 validation_error` con `{"field":"external_ref","issue":"invalid_value"}`.

(2) `lat` y `lng` van juntas: enviar solo una es un error (`required` en la que falta). El par `0, 0` se
rechaza (`invalid_value` en `pickup` o `destination`). Sin coordenadas de origen y de destino la
solicitud se acepta, pero no se cotiza.

Reglas de texto (todos los campos de texto):

- El texto se recorta y se normaliza (Unicode NFC). La longitud se cuenta en caracteres (puntos de
  código), después de recortar.
- Un texto opcional en blanco cuenta como ausente; uno obligatorio en blanco es `required`.
- Los caracteres de control y las marcas de dirección bidireccional se rechazan (`invalid_format`).
  `notes` admite saltos de línea (`\n`; `\r\n` y `\r` se convierten).

Reglas de teléfono (`contact.phone`): los separadores se ignoran y se quita un `tel:`, `sms:` o
`whatsapp:` inicial. 10 dígitos se toman como número de México (`+52`); `00` al inicio se lee como `+`.
El resultado debe ser `+` seguido de 10 a 15 dígitos, sin empezar con 0; si no, `invalid_format`.

### Respuesta

`201` con la solicitud (sección 5). `200` con la cabecera `Idempotent-Replayed: true` cuando la petición
es una repetición (sección 4).

## 4. Idempotencia

- La `Idempotency-Key` es tuya: un valor nuevo por cada solicitud que quieras crear (un UUID sirve).
  Guárdala junto a tu registro **antes** de llamar.
- Repetir la llamada con la **misma clave y el mismo cuerpo** dentro de 24 horas no crea nada y devuelve
  la solicitud original con **`200`** y la cabecera **`Idempotent-Replayed: true`** (la primera llamada
  respondió `201`).
- «El mismo cuerpo» significa **los mismos bytes**. Serializa el cuerpo una sola vez y reenvía esos
  bytes; no reconstruyas el JSON en un reintento (otro orden de claves u otros espacios es otro cuerpo).
- La misma clave con otro cuerpo responde `409 idempotency_conflict`.
- Las claves son por llave de API y se recuerdan 24 horas.
- Solo se recuerda una creación exitosa. Una llamada que terminó en error se puede volver a enviar con
  la misma clave.

## 5. El objeto solicitud

```json
{
  "id": "11111111-2222-4333-8444-555555555555",
  "object": "service_request",
  "status": "quoted",
  "mode": "test",
  "external_ref": "TEST-CONFIRMAR-0001",
  "created_at": "2026-11-18T10:00:00.000Z",
  "resolved_at": null,
  "rejection_reason": null,
  "service": null,
  "quote": {
    "estimated_price": {
      "currency": "MXN",
      "subtotal": 864.33,
      "taxes": [{ "name": "IVA", "type": "tax", "rate": 16, "amount": 138.29 }],
      "tax_total": 138.29,
      "retention_total": 0,
      "total": 1002.62
    },
    "includes_extras": false,
    "approximate_distance": true,
    "unpriced_reason": null,
    "note": "Precio estimado. No incluye extras como casetas o maniobras; la empresa confirma el precio final."
  },
  "assignment": null,
  "timeline": null
}
```

| Campo | Tipo | Notas |
|---|---|---|
| `id` | texto (UUID) | |
| `object` | texto | siempre `service_request` |
| `status` | texto | ver estatus |
| `mode` | texto | `test` o `live`, según la llave |
| `external_ref` | texto o null | |
| `created_at` | texto | |
| `resolved_at` | texto o null | |
| `rejection_reason` | texto o null | viene cuando `status` es `rejected` |
| `service` | objeto o null | `{ "folio": texto o null, "status": texto }` cuando la empresa ya registró el servicio |
| `quote.estimated_price` | objeto o null | `null` si no se pudo cotizar; ver `unpriced_reason` |
| `quote.estimated_price.taxes[].type` | texto | `tax` (se suma) o `retention` (se resta) |
| `quote.includes_extras` | booleano | siempre `false`: la estimación nunca incluye extras como casetas |
| `quote.approximate_distance` | booleano | |
| `quote.unpriced_reason` | texto o null | `missing_coordinates`, `missing_destination`, `no_zones_configured`, `missing_vehicle_class` |
| `assignment` | objeto o null | `{ "operator_first_name": null, "truck": { "economic_number", "plate" } }`. Solo llaves reales |
| `timeline` | objeto o null | alguno de `on_the_way_at`, `on_site_at`, `transport_started_at`, `completed_at`. Solo llaves reales |

Trata los campos desconocidos como compatibilidad hacia adelante: ignora lo que no conozcas.

### Estatus

`quoted` → `confirmed` → `in_progress` → `completed`, o termina en `rejected`, `cancelled` o `expired`.

| Estatus | Significado |
|---|---|
| `received` | Recibida; no se pudo cotizar en automático y la empresa la cotiza a mano |
| `quoted` | La empresa la tiene como cotización |
| `confirmed` | La empresa la confirmó |
| `in_progress` | La grúa va en camino o el servicio está en curso |
| `completed` | Servicio terminado |
| `rejected` | La empresa la rechazó; ver `rejection_reason` |
| `cancelled` | Cancelada |
| `expired` | Solo en pruebas: nadie la resolvió en 24 horas |

### Modo de prueba

Con una llave `glk_test_` la solicitud se valida y se cotiza igual que una real, pero el inicio de
`external_ref` decide su estatus final:

| `external_ref` empieza con | Estatus final |
|---|---|
| `TEST-CONFIRMAR` | `confirmed` |
| `TEST-EN-CURSO` | `in_progress` |
| `TEST-TERMINAR` | `completed` |
| `TEST-RECHAZAR` | `rejected` |
| `TEST-CANCELAR` | `cancelled` |
| cualquier otra o ninguna | `quoted` |

## 6. Consultar

`GET /service-requests/{id}` devuelve el objeto de la sección 5. Un id que no es tuyo, que no existe o
que no es un UUID responde `404 not_found`.

`GET /service-requests?external_ref=<referencia>` devuelve `{ "object": "list", "data": [ ... ] }` con
hasta 20 de tus solicitudes, la más reciente primero (lista vacía si no hay ninguna). `external_ref` es
el único parámetro aceptado: otro responde `400 invalid_request` (`unknown_field`); ausente o en blanco
responde `400 invalid_request` (`required`); con más de 64 caracteres responde `422 validation_error`
(`too_long`).

## 7. Errores

Todos los errores tienen esta forma. El `code` es estable; el `message` está escrito para personas, en
español, y puede cambiar. Decide con el `code` (y el estatus HTTP), nunca con el `message`.

```json
{ "error": { "code": "validation_error", "message": "Hay campos inválidos.",
             "details": [{ "field": "contact.phone", "issue": "invalid_format" }] } }
```

`details` (hasta 20 entradas) aparece en `validation_error` y en algunos `invalid_request`. `issue` es
uno de `required`, `invalid_type`, `too_long`, `invalid_format`, `out_of_range`, `unknown_field`,
`invalid_value`, `duplicate`. Cuando el cuerpo no es un objeto JSON, `field` es `body`.

| HTTP | `code` | Qué pasó | ¿Reintentar? |
|---|---|---|---|
| 400 | `invalid_request` | La petición no es válida | no |
| 400 | `invalid_json` | El cuerpo no es JSON válido (o no es UTF-8 válido) | no |
| 400 | `key_in_query` | La llave se envió en la URL. Pide que la revoquen | no |
| 400 | `idempotency_key_required` | Falta la cabecera `Idempotency-Key` | no |
| 400 | `invalid_idempotency_key` | `Idempotency-Key` no tiene de 1 a 255 caracteres ASCII visibles | no |
| 401 | `unauthorized` | Llave ausente, inválida, vencida o revocada | no |
| 403 | `insufficient_scope` | La llave no tiene permiso para esto | no |
| 403 | `customer_unavailable` | Tu cuenta de cliente con la empresa no está disponible | no |
| 403 | `live_mode_unavailable` | Las solicitudes reales aún no están habilitadas; usa una llave de prueba | no |
| 403 | `browser_not_allowed` | Se usó una llave real desde un navegador | no |
| 404 | `not_found` | No existe o no es tuya | no |
| 405 | `method_not_allowed` | Método no permitido para esta ruta | no |
| 409 | `idempotency_conflict` | Esa `Idempotency-Key` ya se usó con otro cuerpo | no |
| 413 | `payload_too_large` | El cuerpo excede 16 KB | no |
| 415 | `unsupported_media_type` | El cuerpo debe enviarse como `application/json` | no |
| 422 | `validation_error` | Hay campos inválidos; ver `details` | no |
| 429 | `rate_limited` | Demasiadas peticiones. Espera los segundos de `Retry-After` | **sí** |
| 429 | `too_many_open_requests` | Demasiadas solicitudes sin resolver con esta empresa | no (3) |
| 500 | `internal_error` | Error interno | **sí**, con la misma `Idempotency-Key` |

(3) Se libera cuando la empresa resuelve alguna de tus solicitudes abiertas, no al cabo de unos
segundos. Repórtalo; no lo reintentes en bucle.

### Orden de las comprobaciones

Cuando una petición tiene varios problemas, este es el que recibes. Las pruebas de todos los lenguajes
cuentan con él.

Para cualquier petición:

1. `404 not_found` / `405 method_not_allowed` (se decide antes de mirar la llave)
2. `400 key_in_query`
3. `403 browser_not_allowed`
4. `401 unauthorized`, o `429 rate_limited`

Después, para `POST /service-requests`:

5. `415 unsupported_media_type`
6. `400 idempotency_key_required` y luego `400 invalid_idempotency_key`
7. `413 payload_too_large`
8. `400 invalid_json`
9. `422 validation_error` (reglas de campos de la sección 3)
10. `403 insufficient_scope` y luego `403 live_mode_unavailable`
11. idempotencia: repetición `200`, o `409 idempotency_conflict`
12. `403 customer_unavailable`, o `422 validation_error` por reglas de la cuenta (`external_ref`
    obligatoria)
13. `429 too_many_open_requests`
14. `201`

Por eso un `409` solo aparece con un cuerpo que ya pasó la validación, y una repetición se responde
aunque se haya alcanzado el tope de solicitudes abiertas.

## 8. Límites

- 60 peticiones por minuto por llave (más un tope conjunto por empresa). Al pasarlo: `429 rate_limited`
  con `Retry-After` en segundos (de 1 a 60).
- Las peticiones sin llave válida se limitan a 20 por minuto por dirección IP: los `401` repetidos se
  vuelven `429`.
- Hay un tope de solicitudes abiertas (sin resolver), contado por separado para pruebas y reales.
  Pruebas: 20 por llave y 50 por empresa. Reales: 50 por llave y 200 por empresa de forma predeterminada
  (la empresa puede subirlo).
- Cuerpo: 16 KB. Memoria de la `Idempotency-Key`: 24 horas. Una solicitud de prueba sin resolver caduca
  a las 24 horas.

## 9. Comportamiento recomendado del cliente

- **Tiempo máximo**: pon uno en cada petición (los ejemplos usan 15 segundos por intento).
- **Reintenta** solo: errores de red, tiempo agotado, `5xx` y `429 rate_limited`. Nunca otros `4xx`.
- **Espera**: exponencial con jitter completo (`aleatorio(0, min(tope, base * 2^n))`; los ejemplos usan
  base 0.5 s, tope 8 s, 3 reintentos). Si la respuesta trae `Retry-After`, espera al menos eso.
- **Idempotencia**: cada reintento de un `POST` reenvía la misma `Idempotency-Key` y los mismos bytes.
- **Redirecciones**: no las sigas; la llave no debe viajar a otra dirección.
- **Secretos**: la llave nunca aparece en registros, mensajes de error ni excepciones, tampoco cuando el
  servidor o un intermediario la devuelven en su respuesta.

## 10. Avisos (webhooks)

En lugar de consultar, puedes recibir un aviso cuando una solicitud cambia. Dale a la empresa la
dirección de tu sistema y elige los eventos; la empresa te entrega un **secreto de firma** que empieza
con `whsec_`.

Requisitos de la dirección: `https`, puerto predeterminado (443), un nombre de dominio público (sin
direcciones IP ni nombres internos), sin credenciales, 500 caracteres ASCII como máximo.

### Entrega

Un `POST` a tu dirección con estas cabeceras (los nombres de cabecera no distinguen mayúsculas):

| Cabecera | Valor |
|---|---|
| `Content-Type` | `application/json; charset=utf-8` |
| `User-Agent` | `GRUALINK-Avisos/1.0` |
| `X-GruaLink-Event` | El evento, igual que `type` en el cuerpo |
| `X-GruaLink-Delivery` | Id del evento, igual que `id` en el cuerpo e idéntico en todos los reintentos |
| `X-GruaLink-Timestamp` | Segundos desde 1970 (UTC) en que se firmó este intento |
| `X-GruaLink-Mode` | `test` o `live` |
| `X-GruaLink-Signature` | `t=<segundos>,v1=<firma>[,v1=<firma>]` |

Cuerpo (64 KB como máximo):

```json
{
  "id": "aaaaaaaa-0000-4000-8000-000000000001",
  "object": "event",
  "type": "service_request.confirmed",
  "mode": "test",
  "created_at": "2026-11-18T10:05:01.000Z",
  "data": {
    "previous_status": "quoted",
    "service_request": { "...": "el objeto de la sección 5, tal como lo devuelve GET /service-requests/{id}" }
  }
}
```

El ejemplo está formateado para leerse. El cuerpo real tiene sus propios espacios y su propio orden de
claves: nunca supongas que coincide con lo que escribiría tu biblioteca de JSON.

| `type` | Cuándo |
|---|---|
| `service_request.quoted` | La empresa la tiene como cotización |
| `service_request.confirmed` | La empresa la confirmó |
| `service_request.in_progress` | En camino o en curso |
| `service_request.completed` | Terminada |
| `service_request.rejected` | Rechazada (con el motivo) |
| `service_request.cancelled` | Cancelada |
| `service_request.expired` | Solo en pruebas |
| `service_request.assigned` | Se asignó o cambió operador y grúa. `previous_status` es `null` |
| `service_request.on_the_way` | El operador salió hacia el origen. `previous_status` es `null` |
| `service_request.on_site` | El operador llegó. `previous_status` es `null` |
| `webhook.test` | Aviso de prueba enviado a petición. `data` es `{ "message": "..." }` |

Ignora los tipos de evento que no conozcas.

### Verificar la firma

La firma es `HMAC-SHA256`, en hexadecimal en minúsculas, de los bytes `<t>.<cuerpo crudo>`, con tu
secreto como llave **tal cual te lo entregaron, incluido su prefijo `whsec_`**.

Impleméntala como una función pura `(cuerpo crudo, cabecera de firma, secreto, ahora, tolerancia) →
(válida, motivo)` que nunca lanza, con estos pasos **en este orden**:

1. Si no hay secreto (vacío o sin configurar): rechaza, `missing_secret`.
2. Si la cabecera `X-GruaLink-Signature` falta o está en blanco: rechaza, `missing_header`.
3. Separa la cabecera por `,`; recorta cada parte; separa cada parte en su primer `=` en nombre y valor.
   - `t`: la primera parte llamada `t`. Debe tener de 1 a 12 dígitos decimales.
   - candidatas: cada parte llamada `v1` cuyo valor tenga exactamente 64 caracteres hexadecimales en
     minúsculas. Los demás valores `v1` y las partes con otros nombres se ignoran.
   - Si `t` falta o está mal formada, o no hay ninguna candidata: rechaza, `malformed_header`.
4. Si `|ahora - t| > tolerancia` (**300 segundos** de forma predeterminada, límite incluido): rechaza,
   `timestamp_out_of_tolerance`. `ahora` va en segundos y debe poder inyectarse en las pruebas.
5. Calcula `esperada = hex(HMAC-SHA256(llave = bytes del secreto, mensaje = t + "." + bytes del cuerpo
   crudo))`, con `t` como los dígitos recibidos y el cuerpo como los **bytes crudos recibidos**, antes de
   convertirlo a nada.
6. Acepta (`ok`) si **alguna** candidata es igual a `esperada`, comparando en **tiempo constante**. Si
   no, rechaza, `signature_mismatch`.

Los motivos son exactamente: `ok`, `missing_secret`, `missing_header`, `malformed_header`,
`timestamp_out_of_tolerance`, `signature_mismatch`.

Errores frecuentes, todos cubiertos por `signature-vectors.json`:

- Verificar el cuerpo después de convertirlo a objeto y volver a serializarlo. Los bytes cambian y la
  firma falla.
- Quitar el prefijo `whsec_` o decodificar el secreto. Úsalo como texto, completo.
- Decodificar el cuerpo con otra codificación. Se firman bytes, no texto.
- Comparar con `==`. Usa la comparación en tiempo constante de tu plataforma.
- Aceptar hexadecimal en mayúsculas, o pasar la cabecera a minúsculas antes de comparar.
- Saltarse la comprobación de la marca de tiempo, que es lo que impide repetir un aviso viejo.

### Rotación del secreto

Después de que la empresa cambia el secreto, los avisos traen **dos** valores `v1` durante 24 horas: el
primero hecho con el secreto nuevo y el segundo con el anterior. Acepta el aviso si algún `v1` coincide
con el secreto que tienes. Así puedes cambiar de secreto en cualquier momento dentro de esas 24 horas.

### Responder, reintentos y duplicados

- Responde cualquier `2xx` en menos de **10 segundos**. Lo demás (otro código, una redirección, no
  responder a tiempo) cuenta como fallo. Haz el trabajo lento después de responder.
- Una entrega fallida se reintenta después de 1 min, 5 min, 30 min, 2 h, 6 h y 12 h: hasta 7 intentos
  en 24 horas. Cada intento se firma de nuevo (`t` nueva), con el mismo `id`.
- Tras 20 entregas fallidas seguidas los avisos se desactivan; pide a la empresa que los reactive.
- Los avisos pueden llegar **más de una vez** y, raramente, **fuera de orden**. Deduplica por el id del
  evento: guarda los ids que ya procesaste (al menos 24 horas) y responde `2xx` a una repetición sin
  volver a procesarla. `X-GruaLink-Delivery` no está cubierta por la firma y el `id` del cuerpo sí:
  después de verificar, comprueba que sean iguales y usa ese valor.
- A lo que venga sin firma o mal firmado respóndele `401`, sin detallar por qué.
- Para ordenar eventos usa `created_at` y el `status` dentro de `data.service_request`, o consulta la
  solicitud.

## 11. Nombres compartidos por todos los lenguajes

Usa la convención de mayúsculas de cada lenguaje (`createServiceRequest`, `create_service_request`,
`CreateServiceRequest`) con estas mismas palabras, en inglés.

| Concepto | Nombre |
|---|---|
| Cliente | `GrualinkClient` |
| Crear | `createServiceRequest(input, { idempotencyKey })` → `{ serviceRequest, replayed, idempotencyKey }` |
| Consultar una | `getServiceRequest(id)` |
| Buscar | `findServiceRequestsByExternalRef(externalRef)` |
| Conexión | `health()` |
| Verificar | `verifyWebhookSignature({ rawBody, signatureHeader, secret, nowSeconds, toleranceSeconds })` → `{ valid, reason }` |
| Firmar (para pruebas locales) | `computeWebhookSignature(secret, timestamp, rawBody)`, `buildSignatureHeader(secrets, timestamp, rawBody)` |
| Deduplicación | `DeliveryDeduplicator` con `claim(deliveryId)` y `release(deliveryId)`; `InMemoryDeliveryDeduplicator` |
| Receptor | `createWebhookHandler({ secret, onEvent, deduplicator })` |

| Error | Cuándo |
|---|---|
| `GrualinkError` | Base de todos |
| `GrualinkConfigError` | Configuración o argumento inválido; no se envió nada |
| `GrualinkConnectionError` | Sin respuesta HTTP (red) |
| `GrualinkTimeoutError` | Sin respuesta dentro del tiempo máximo |
| `GrualinkApiError` | La API respondió un error: `status`, `code`, `message`, `details`, `retryAfterSeconds`, `attempts` |
| `GrualinkInvalidRequestError` | 400, 405, 413, 415 |
| `GrualinkAuthenticationError` | 401 |
| `GrualinkPermissionError` | 403 |
| `GrualinkNotFoundError` | 404 |
| `GrualinkIdempotencyConflictError` | 409 |
| `GrualinkValidationError` | 422 |
| `GrualinkRateLimitError` | 429 |
| `GrualinkServerError` | 5xx |

Una respuesta de error cuyo cuerpo no tiene la forma documentada usa el código `unexpected_response`.

Variables de entorno: `GRUALINK_API_KEY`, `GRUALINK_WEBHOOK_SECRET`, `GRUALINK_WEBHOOK_SECRET_PREVIOUS`
(opcional), `GRUALINK_API_BASE_URL` (opcional).
