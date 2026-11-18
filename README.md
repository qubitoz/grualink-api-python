# GRUALINK API: ejemplo en Python

[English](README.en.md)

Ejemplo listo para clonar y correr que integra tu sistema (aseguradora, asistencia, integrador) con la
API de GRUALINK: **pedir una grúa**, **consultar el estatus** y **recibir avisos firmados**.

Guía pública de la API: <https://grualink.mx/api>

Qué trae:

- Un **cliente mínimo** (`grualink/client.py`): crea solicitudes con `Idempotency-Key`, consulta el
  estatus, errores tipados, reintentos con espera exponencial y tiempo máximo. Solo usa la biblioteca
  estándar: no hay dependencias de ejecución.
- Un **receptor de avisos** (`grualink/webhooks.py`, `grualink/webhook_server.py`): verifica la firma
  sobre el cuerpo crudo, valida la ventana de tiempo, soporta el cambio de secreto y descarta duplicados.
- Dos **ejemplos ejecutables** (`examples/`).
- El **kit de contrato** (`contract/`): el contrato de la API en español e inglés y los casos de prueba
  de firma que comparten los ejemplos de todos los lenguajes.

No es un SDK: es código pensado para leerse y copiarse a tu proyecto.

## Requisitos

- Python 3.10 o superior.
- Una **llave de prueba** (`glk_test_…`). Te la da la empresa de grúas con la que trabajas.
- Para los avisos: el **secreto de firma** (`whsec_…`) que te entrega la misma empresa al registrar la
  dirección de tu sistema.

## Instalación

```bash
git clone <la dirección de este repositorio>
cd grualink-api-python
```

No hay nada que instalar para correr los ejemplos ni las pruebas. Para el lint y la revisión de tipos
(opcional), crea un entorno virtual:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Variables de entorno

Solo los nombres; los valores van en tu equipo y nunca en el repositorio. Hay una plantilla en
`.env.example` (los ejemplos leen las variables del entorno; no cargan ese archivo por sí solos).

| Variable | Para qué |
|---|---|
| `GRUALINK_API_KEY` | Tu llave de **prueba** (`glk_test_…`) |
| `GRUALINK_WEBHOOK_SECRET` | Secreto de firma de los avisos (`whsec_…`), completo, con su prefijo |
| `GRUALINK_WEBHOOK_SECRET_PREVIOUS` | Opcional: el secreto anterior mientras dura un cambio de secreto |
| `GRUALINK_API_BASE_URL` | Opcional: otra dirección base (por omisión `https://grualink.mx/api/v1`) |
| `PORT` | Opcional: puerto del receptor de ejemplo (por omisión 8787) |

## Correr los ejemplos

Desde la raíz del repositorio.

### Pedir una grúa

```bash
export GRUALINK_API_KEY=...   # tu llave de prueba
python -m examples.request_tow
```

Con datos ficticios, el ejemplo envía una solicitud, imprime el precio estimado, la vuelve a enviar con
la misma `Idempotency-Key` para mostrar que no se duplica y consulta su estatus. Solo corre con llaves
de prueba.

Con una llave de prueba, el inicio de `external_ref` decide el estatus final: `TEST-CONFIRMAR`,
`TEST-EN-CURSO`, `TEST-TERMINAR`, `TEST-RECHAZAR`, `TEST-CANCELAR`.

### Recibir avisos

```bash
export GRUALINK_WEBHOOK_SECRET=...   # tu secreto de firma
python -m examples.webhook_server
```

Queda escuchando en `http://localhost:8787/webhooks/grualink`. GRUALINK solo entrega a direcciones
`https` públicas en el puerto 443: publica el receptor detrás de tu dominio (o de un túnel `https` mientras
pruebas), dale esa dirección a la empresa y pídele que te mande un aviso de prueba.

## Uso en tu código

```python
import os

from grualink import GrualinkClient, GrualinkValidationError

client = GrualinkClient(os.environ["GRUALINK_API_KEY"])

try:
    result = client.create_service_request(
        {
            "external_ref": "TEST-CONFIRMAR-0001",
            "contact": {"name": "Persona de Ejemplo", "phone": "+525500000000"},
            "pickup": {
                "address": "Av. Juárez 100, Centro, Guadalajara, Jal.",
                "lat": 20.6767,
                "lng": -103.3475,
            },
            "destination": {"address": "Av. Patria 500, Zapopan, Jal.", "lat": 20.7214, "lng": -103.3918},
        },
        idempotency_key=mi_registro.clave_de_idempotencia,  # guárdala antes de llamar
    )
    solicitud = result.service_request
    print(solicitud["id"], solicitud["status"], solicitud["quote"]["estimated_price"], result.replayed)
except GrualinkValidationError as error:
    print(error.details)
```

Las operaciones son `create_service_request`, `get_service_request`,
`find_service_requests_by_external_ref` y `health`. Las respuestas son los diccionarios del JSON, con
los nombres de campo del contrato; `grualink/types.py` los describe para tu editor y tu revisor de tipos.

El cliente es síncrono. Los tiempos se expresan en segundos (`timeout_seconds`, `retry_base_delay_seconds`,
`retry_max_delay_seconds`).

### Idempotencia y reintentos

- Cada solicitud lleva una `Idempotency-Key` tuya. Si repites la llamada con la misma clave y el mismo
  cuerpo en 24 horas, la API responde **`200`** con `Idempotent-Replayed: true` y no crea nada
  (`replayed` es `True`). La misma clave con otro cuerpo responde `409`.
- El cliente reintenta solo lo que tiene sentido: errores de red, tiempo agotado, `5xx` y
  `429 rate_limited` (esperando lo que diga `Retry-After`). Usa espera exponencial con jitter y reenvía
  siempre la misma clave y los mismos bytes.
- Cada intento tiene un tiempo máximo (15 segundos por omisión) y nunca se sigue una redirección.
- La llave nunca aparece en errores ni registros.

### Verificar un aviso

```python
import os

from grualink import verify_webhook_signature

result = verify_webhook_signature(
    raw_body=raw_body,  # los bytes tal como llegaron
    signature_header=headers.get("x-grualink-signature"),
    secret=os.environ["GRUALINK_WEBHOOK_SECRET"],  # completo, con whsec_
)
if not result.valid:
    ...  # responde 401 sin detalle; result.reason es para tus registros
```

Lo importante:

1. Verifica el **cuerpo crudo**, antes de convertirlo a JSON. Si lo conviertes y lo vuelves a
   serializar, la firma ya no coincide.
2. Usa el secreto **completo**, con su prefijo `whsec_`.
3. Rechaza marcas de tiempo con más de 5 minutos de diferencia.
4. Tras un cambio de secreto llegan dos firmas `v1` durante 24 horas: acepta si cualquiera coincide.
5. Los avisos pueden repetirse: guarda el `id` de los ya procesados y responde `2xx` sin reprocesar.
6. Responde `2xx` en menos de 10 segundos y haz el trabajo pesado después.

### Montarlo en Flask, FastAPI o Django

El punto delicado es conseguir el **cuerpo crudo**. `create_webhook_handler` no depende de ningún
framework: recibe los bytes y las cabeceras y devuelve el estatus y el cuerpo de la respuesta.

```python
from grualink import create_webhook_handler

handle = create_webhook_handler(secret=os.environ["GRUALINK_WEBHOOK_SECRET"], on_event=on_event)
```

Flask: usa `request.get_data()` (no `request.json` ni `request.get_json()`, que entregan el cuerpo ya
convertido).

```python
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024


@app.post("/webhooks/grualink")
def grualink_webhook():
    result = handle(request.get_data(), dict(request.headers))
    return result.body, result.status
```

FastAPI: pide el `Request` y lee `await request.body()` (no declares un modelo como parámetro: FastAPI
convertiría el cuerpo antes).

```python
@app.post("/webhooks/grualink")
async def grualink_webhook(request: Request):
    result = handle(await request.body(), dict(request.headers))
    return JSONResponse(result.body, status_code=result.status)
```

Django: usa `request.body` (bytes) y exime la vista de CSRF, porque la autenticación es la firma.

```python
@csrf_exempt
@require_POST
def grualink_webhook(request):
    result = handle(request.body, dict(request.headers))
    return JsonResponse(result.body, status=result.status)
```

En los tres casos limita el tamaño del cuerpo a 64 KB en tu servidor o proxy, y haz que `on_event` solo
guarde o encole el aviso.

`InMemoryDeliveryDeduplicator` sirve para un solo proceso. Con varios procesos o instancias (lo normal
detrás de gunicorn o uvicorn), sustitúyelo por uno que use tu base de datos: basta una clave única sobre
el `id` del aviso, con los métodos `claim(delivery_id)` y `release(delivery_id)`.

## Errores

Todos los errores de la API tienen la forma `{ "error": { "code", "message", "details"? } }`. El `code`
es estable; decide con él, no con el `message`.

| HTTP | `code` | Qué pasó | Clase | ¿Se reintenta? |
|---|---|---|---|---|
| 400 | `invalid_request` | La petición no es válida | `GrualinkInvalidRequestError` | no |
| 400 | `invalid_json` | El cuerpo no es JSON válido | `GrualinkInvalidRequestError` | no |
| 400 | `key_in_query` | La llave se envió en la URL; pide que la revoquen | `GrualinkInvalidRequestError` | no |
| 400 | `idempotency_key_required` | Falta `Idempotency-Key` | `GrualinkInvalidRequestError` | no |
| 400 | `invalid_idempotency_key` | `Idempotency-Key` inválida (1 a 255 caracteres ASCII visibles) | `GrualinkInvalidRequestError` | no |
| 401 | `unauthorized` | Llave ausente, inválida, vencida o revocada | `GrualinkAuthenticationError` | no |
| 403 | `insufficient_scope` | La llave no tiene permiso para esto | `GrualinkPermissionError` | no |
| 403 | `customer_unavailable` | Tu cuenta de cliente con la empresa no está disponible | `GrualinkPermissionError` | no |
| 403 | `live_mode_unavailable` | Las solicitudes reales aún no están habilitadas | `GrualinkPermissionError` | no |
| 403 | `browser_not_allowed` | Llave real usada desde un navegador | `GrualinkPermissionError` | no |
| 404 | `not_found` | No existe o no es tuya | `GrualinkNotFoundError` | no |
| 405 | `method_not_allowed` | Método no permitido para esa ruta | `GrualinkInvalidRequestError` | no |
| 409 | `idempotency_conflict` | Esa `Idempotency-Key` ya se usó con otro cuerpo | `GrualinkIdempotencyConflictError` | no |
| 413 | `payload_too_large` | El cuerpo excede 16 KB | `GrualinkInvalidRequestError` | no |
| 415 | `unsupported_media_type` | El cuerpo debe ir como `application/json` | `GrualinkInvalidRequestError` | no |
| 422 | `validation_error` | Campos inválidos; `details` dice cuáles | `GrualinkValidationError` | no |
| 429 | `rate_limited` | Demasiadas peticiones; espera `Retry-After` | `GrualinkRateLimitError` | sí |
| 429 | `too_many_open_requests` | Demasiadas solicitudes sin resolver con la empresa | `GrualinkRateLimitError` | no |
| 500 | `internal_error` | Error interno | `GrualinkServerError` | sí |

Los errores de la API (`GrualinkApiError`) llevan `status`, `code`, `message`, `details`,
`retry_after_seconds` y `attempts`.

Además: `GrualinkConnectionError` (sin respuesta, se reintenta), `GrualinkTimeoutError` (tiempo agotado,
se reintenta) y `GrualinkConfigError` (configuración o argumento inválido; no se envió nada). Todas
heredan de `GrualinkError`.

El contrato completo, con campos, límites y el orden de las comprobaciones, está en
[`contract/API.es.md`](contract/API.es.md).

## Pruebas

```bash
python -m unittest                 # las pruebas; solo biblioteca estándar
python scripts/check_contract.py   # contract/ es idéntico al kit compartido
```

Con el entorno virtual de desarrollo:

```bash
ruff check . && ruff format --check .
mypy
```

Las pruebas no llaman a la API real: usan un servidor falso local. Cubren todos los casos de
`contract/signature-vectors.json`, cada código de error, los reintentos, la idempotencia y que la llave
no aparezca en errores ni registros.

## Estructura

```
contract/     contrato de la API (es/en), casos de prueba de firma y lista de verificación
grualink/     cliente, errores, tipos, verificación de avisos y receptor
examples/     request_tow.py y webhook_server.py
tests/        pruebas (unittest) y servidor falso
scripts/      comprobación de que contract/ no cambió
```

## Licencia

[MIT](LICENSE)
