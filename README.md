# telemetry

Estadísticas anónimas de uso para mis proyectos de código abierto, con un panel público en [stats.dgongut.com](https://stats.dgongut.com).

Un solo servicio sirve a todos los proyectos: cada uno declara en un fichero YAML lo que puede enviar, y con eso el servidor sabe qué validar y la web qué dibujar. **Añadir un proyecto es añadir un fichero, no escribir código.**

## Cómo funciona

```
proyecto ──POST /v1/ping (1 al día)──▶ telemetry.dgongut.com ─┐
                                                              ├─ FastAPI + SQLite (un contenedor)
navegador ──GET /─────────────────────▶ stats.dgongut.com ────┘
```

- **Envíos:** cada instalación manda una vez al día una foto de su estado (`metrics`) y los contadores de uso acumulados desde el último envío (`usage`). El formato está en [docs/protocol.md](docs/protocol.md).
- **Validación contra el manifiesto:** un dato que no esté declarado en `projects/<proyecto>.yaml` se descarta al llegar, así que ni un fallo en un cliente ni alguien que envíe a mano puede colar nada no previsto.
- **Solo agregados hacia fuera:** la web recibe cifras y porcentajes, nunca filas sueltas. Publicar el panel no expone ninguna instalación concreta.
- **Sin IPs:** la IP de cada envío solo vive en memoria durante una hora, para limitar abusos. En disco no se guarda.
- **Retención:** cada envío se borra a los 90 días. Antes, cada mes completo se resume en totales (instalaciones activas y nuevas, versiones, valores de cada métrica, uso de cada contador) que no llevan ningún identificador y se guardan para siempre. Así se conserva todo el histórico sin guardar los `install_id` más de lo necesario.

## Estructura

```
server/           API (FastAPI) y base de datos (SQLite)
web/              el panel: HTML, CSS y Chart.js servido desde aquí, sin CDNs
projects/         un manifiesto por proyecto
clients/python/   cliente de referencia: un módulo sin dependencias, instalable con pip
docs/             el protocolo
scripts/          datos de demostración para trabajar en la web
tests/
```

## Añadir un proyecto

1. Crea `projects/<id>.yaml`. El nombre del fichero es el id del proyecto: minúsculas, números y guiones. Toma como ejemplo [projects/docker-controller-bot.yaml](projects/docker-controller-bot.yaml).
2. Añade el cliente a su `requirements.txt` (ver [Cliente Python](#cliente-python)), o implementa [el protocolo](docs/protocol.md) si es de otro lenguaje.
3. Reinicia el contenedor. Si un manifiesto tiene un error, el servidor no arranca y dice cuál es. No se lo salta, porque saltárselo significaría rechazar en silencio todos los envíos de ese proyecto.

### Manifiesto

```yaml
name: Mi proyecto
description: { es: Qué es, en: What it is }
repo: https://github.com/dgongut/mi-proyecto
opt_out: { es: Cómo se desactiva, en: How to turn it off }
enabled: true          # a false: se responde "para" y no se guarda nada
interval_hours: 24

metrics:
  hosts:
    type: int          # int, number, bool o enum
    min: 0
    max: 1000
    kpi: sum           # opcional: sum o mean, sale como cifra arriba del panel
    kpi_label: { es: Hosts gestionados, en: Hosts managed }
    chart: histogram   # int: histogram/nonzero · number: histogram · bool: adoption · enum: donut/bar
    buckets: ["1", "2", "3-5", "6+"]
    label: { es: Hosts por instalación, en: Hosts per installation }
    description: { es: Para la página de privacidad, en: For the privacy page }

usage:
  description: { es: ..., en: ... }
  groups:
    - prefix: cmd_
      display: "/{name}"   # cmd_list se muestra como /list
      label: { es: Comandos, en: Commands }
  labels:
    auto_update_check: { es: Comprobación de actualizaciones, en: Update check }
```

Los textos pueden ser una cadena o un `{es:, en:}`. Todos los metrics llevan `description` obligatoria: es lo que aparece en `stats.dgongut.com/<id>/privacy`.

Las claves de `usage` no se declaran una a una, solo se validan por forma. Si hubiera que declararlas, cada botón nuevo de un proyecto se perdería en silencio hasta que alguien se acordara de añadirlo. Como son identificadores del código y nunca texto del usuario, basta con un patrón y un máximo de claves.

## Cliente Python

Los proyectos no copian el cliente: lo instalan desde un tag de este repo, sin PyPI. En su `requirements.txt`:

```
dgongut-telemetry @ https://github.com/dgongut/telemetry/archive/refs/tags/client-v1.0.0.zip#subdirectory=clients/python
```

Instala un único módulo, `telemetry`, así que en el código basta con `import telemetry`. Funciona igual en la imagen, en la CI y en local, porque todo pasa por `pip install -r requirements.txt`, y no necesita `git`: pip baja el `.zip` del tag.

La versión queda fijada en cada proyecto: un cambio en el cliente no le llega hasta que sube el número en esa línea.

### Publicar una versión

1. Cambia el cliente en `clients/python/telemetry.py`, con sus tests en `tests/test_client.py`.
2. Sube `version` en `clients/python/pyproject.toml` y el número de la línea de arriba (un test comprueba que coinciden).
3. Haz commit y crea el tag `client-v<versión>`:
   ```bash
   git tag client-v1.0.0 && git push origin main client-v1.0.0
   ```
4. En cada proyecto, cambia el número del tag en su `requirements.txt`.

Un tag publicado no se mueve: los proyectos que ya lo usan dependen de que siga siendo lo que era.

## Despliegue

Un solo contenedor sirve las dos cosas: los envíos de los proyectos y la web. Lo que cambia es la ruta: los bots envían a `/v1/ping` y las personas visitan `/`, `/<proyecto>` y `/<proyecto>/privacy`.

Aun así, conviene usar dos nombres de dominio que apunten al mismo contenedor:

- `telemetry.dgongut.com` → los envíos. Queda escrito en el código de cada versión publicada, así que no debe cambiar nunca.
- `stats.dgongut.com` → la web. Si algún día la mueves a otro sitio, los bots antiguos siguen enviando a `telemetry` sin enterarse.

### Puesta en marcha

1. Apunta los dos dominios al servidor con registros A.
2. Elige en qué IP escucha el contenedor con un `.env` junto al `docker-compose.yaml` (git lo ignora):
   - **Sin `.env`:** solo en `127.0.0.1`. Vale si el proxy está instalado en el propio servidor, fuera de Docker.
   - **`TELEMETRY_BIND=0.0.0.0`:** en todas las interfaces. Es lo necesario si el proxy corre en Docker y apunta a la IP pública. Cierra el puerto 8000 en el firewall de tu proveedor: Docker se salta las reglas de UFW.
3. Arranca el contenedor:
   ```bash
   docker compose up -d --build
   ```
4. En el proxy, envía los dos dominios al puerto `8000` por HTTP y **sin autenticación**: los bots no pueden iniciar sesión, y la web es pública.
5. Comprueba que `https://telemetry.dgongut.com/healthz` responde `{"ok":true,...}`.

### Copias de seguridad

Guarda `./data/telemetry.db`. Los envíos con identificador se borran solos a los 90 días, pero los totales mensuales se guardan para siempre y solo existen en ese fichero.

### Variables

| Variable | Por defecto | |
|---|---|---|
| `RATE_LIMIT` | `30` | Envíos por remitente y hora |
| `CACHE_SECONDS` | `600` | Cuánto se reutiliza un agregado antes de recalcularlo |

Los manifiestos se montan como volumen, así que añadir un proyecto no requiere reconstruir la imagen: basta con reiniciar el contenedor.

## Desarrollo

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r server/requirements.txt pytest httpx
pytest

DATA_DIR=./demo-data python3 scripts/seed_demo.py
DATA_DIR=./demo-data uvicorn --factory app:create_app --app-dir server --reload
```
