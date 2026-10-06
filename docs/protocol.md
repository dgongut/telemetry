# Protocolo

Un cliente hace como mucho un envío cada `interval_hours` horas (24 por defecto).

## Envío

```
POST https://telemetry.dgongut.com/v1/ping
Content-Type: application/json
```

```json
{
  "schema": 1,
  "project": "docker-controller-bot",
  "install_id": "3f0c8f7e-5b1a-4b8e-9d57-2a9e0c1d4b6f",
  "version": "5.0.0",
  "arch": "arm64",
  "metrics": { "hosts": 3, "language": "es", "check_updates": true },
  "usage": { "cmd_list": 41, "btn_confirmUpdate": 2 }
}
```

| Campo | Obligatorio | Regla |
|---|---|---|
| `schema` | sí | `1` |
| `project` | sí | El nombre de un fichero de `projects/`, sin extensión |
| `install_id` | sí | Un UUID aleatorio que genera el cliente en su primer envío y guarda. Si se desactiva la telemetría, se borra |
| `version` | sí | `^[0-9A-Za-z][0-9A-Za-z._+-]{0,31}$` |
| `arch` | no | `^[a-z0-9_]{1,16}$`: `amd64`, `arm64`, `armv7`… |
| `metrics` | no | Solo las claves declaradas en el manifiesto, con su tipo y rango. Una foto del estado actual |
| `usage` | no | Contadores desde el último envío correcto. Claves `^[a-z][a-zA-Z0-9_]{0,63}$`, valores enteros entre 1 y 100.000, como mucho 300 claves |

Cada campo se valida por separado. Un metric no declarado o con un valor fuera de rango se descarta, pero el resto del envío se guarda. El envío completo solo se rechaza (con un 400) si falla algo del sobre: `schema`, `project`, `install_id` o `version`.

Si hay dos envíos el mismo día (UTC), sus contadores se suman y los metrics se quedan con los del último.

## Respuesta

```json
{ "enabled": true, "next_ping_h": 24 }
```

- `next_ping_h`: cuántas horas esperar hasta el siguiente envío. El cliente lo limita a un valor entre 1 y 168.
- `enabled: false`: el proyecto está apagado en el servidor. El cliente no vuelve a enviar hasta que pasen `next_ping_h` horas, y entonces pregunta de nuevo.

No hay ningún otro campo. El servidor no puede pedir al cliente nada más.

| Código | Qué significa | Qué hace el cliente |
|---|---|---|
| 200 | Guardado (o proyecto apagado) | Resta de sus contadores lo que envió |
| 400 | Sobre inválido | Conserva los contadores y lo reintenta en una hora |
| 413 | Más de 32 KB | Igual que el 400 |
| 429 | Más de 30 envíos por hora desde la misma IP | Igual que el 400 |

## Lo que debe cumplir un cliente

1. No enviar nada, y no crear `install_id`, mientras la telemetría esté desactivada o la variable de entorno `TELEMETRY` tenga cualquier valor distinto de `true`. Se documenta como `TELEMETRY=false`, pero cualquier otro valor también la apaga: quien escribe la variable quiere apagarla. Es la misma en todos los proyectos y siempre gana a sus ajustes.
2. No enviar antes de llevar 10 minutos en marcha, para no contar arranques de prueba ni bucles de reinicio.
3. No enviar nunca texto que haya escrito el usuario: nombres, rutas, IDs, direcciones. Si algo tiene que ir, que vaya como número, sí/no o un valor de una lista cerrada que se declara en el manifiesto.
4. No bloquear ni hacer fallar el programa que lo usa.

El cliente de referencia en Python, [`clients/python/telemetry.py`](../clients/python/telemetry.py), cumple todo esto. Los proyectos lo instalan con pip desde un tag de este repo: ver [Cliente Python](../README.md#cliente-python) en el README.
