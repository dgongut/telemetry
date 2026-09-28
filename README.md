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
clients/python/   cliente de referencia, un fichero para copiar en cada proyecto
docs/             el protocolo
scripts/          datos de demostración para trabajar en la web
tests/
```

## Añadir un proyecto

1. Crea `projects/<id>.yaml`. El nombre del fichero es el id del proyecto: minúsculas, números y guiones. Toma como ejemplo [projects/docker-controller-bot.yaml](projects/docker-controller-bot.yaml).
2. Copia `clients/python/telemetry.py` en el proyecto, o implementa [el protocolo](docs/protocol.md) si es de otro lenguaje.
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

## Despliegue

Un solo contenedor sirve las dos cosas: los envíos de los proyectos y la web. Lo que cambia es la ruta: los bots envían a `/v1/ping` y las personas visitan `/`, `/<proyecto>` y `/<proyecto>/privacy`.

Aun así, conviene usar dos nombres de dominio que apunten al mismo contenedor:

- `telemetry.dgongut.com` → los envíos. Queda escrito en el código de cada versión publicada, así que no debe cambiar nunca.
- `stats.dgongut.com` → la web. Si algún día la mueves a otro sitio, los bots antiguos siguen enviando a `telemetry` sin enterarse.

### Con Pangolin en el mismo VPS

1. Crea los dos registros A en el DNS apuntando al VPS.
2. Junto al `docker-compose.yaml`, crea un `docker-compose.override.yaml` con esto. Compose lo lee solo, y como git lo ignora, un `git pull` futuro no choca con él:
   ```yaml
   services:
       telemetry:
           ports: !reset []   # sin puertos: solo se llega a través de Pangolin
           networks:
               - pangolin

   networks:
       pangolin:
           external: true
   ```
   Si la red de Pangolin no se llama `pangolin`, cámbiale el nombre; `docker network ls` te dice cuál es.
3. Arráncalo:
   ```bash
   docker compose up -d --build
   ```
4. En Pangolin, crea dos recursos HTTP en el site local (el del propio servidor), uno para `telemetry.dgongut.com` y otro para `stats.dgongut.com`. Los dos con destino `http://telemetry:8000`.
5. **Quita la autenticación de los dos recursos.** Los bots no pueden iniciar sesión, y la web es pública. Si se queda protegida, los envíos reciben la página de login y no se guarda nada.
6. Comprueba que `https://telemetry.dgongut.com/healthz` responde `{"ok":true,...}`.

### Sin Pangolin

Deja el `docker-compose.yaml` tal cual: el contenedor escucha en `127.0.0.1:8000` y tu proxy inverso apunta los dos dominios ahí.

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
