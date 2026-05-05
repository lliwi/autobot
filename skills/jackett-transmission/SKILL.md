---
name: jackett-transmission
description: Busca torrents con Jackett y gestiona descargas en Transmission de forma controlada.
version: 0.1.1
---

# Jackett + Transmission Download Skill

Busca torrents con Jackett y gestiona descargas en Transmission. Úsala cuando el usuario quiera buscar contenido, revisar resultados y encolar una descarga en su cliente Transmission.

## Changelog

### 0.1.1

#### Fixed
- Documentado el flujo Autobot seguro con credencial `jackett` y variante `jackett-search-token`.
- Añadida nota sobre falsos negativos en wrappers `agentcred` cuando no resuelven credenciales desde el contexto de la tool.
- Reforzada la recomendación de configurar endpoints mediante variables de entorno o configuración del workspace, sin hard-codear URLs privadas en templates reutilizables.

#### Notes
- Release de compatibilidad Autobot; no cambia el protocolo Jackett/Torznab ni el comportamiento de Transmission.
- La API key debe mantenerse siempre en memoria y no debe escribirse en shell, logs ni ficheros.

## Configuración

### Credenciales

- `jackett`: credencial tipo token con la API key de Jackett.

### Endpoints

Configurar mediante variables de entorno, secretos del runtime o configuración del workspace:

- `JACKETT_URL`: URL base de Jackett.
- `TRANSMISSION_URL`: URL base del RPC de Transmission o base del servicio.

No incluir URLs privadas de despliegues concretos en el template de la skill.

## Implementación

La lógica operativa histórica vive en `skill.sh` cuando se ejecuta por shell. En Autobot, para búsquedas de Jackett, el flujo preferente es usar herramientas que no expongan la API key.

## Flujo Autobot recomendado

1. Obtener la credencial con `get_credential("jackett")`.
2. Ejecutar `jackett-search-token` pasando el token en memoria.
3. Usar el `base_url` configurado por el usuario o workspace.
4. Presentar resultados al usuario antes de encolar cualquier descarga.
5. Para añadir a Transmission, pedir confirmación explícita del resultado elegido.

Ejemplo conceptual:

```text
get_credential("jackett")
jackett-search-token(token=<in-memory>, query="...", category="tv", base_url=<configured-jackett-url>)
```

No imprimir, persistir ni pasar el token por shell.

## Uso manual

```bash
bash skills/jackett-transmission/skill.sh ping
bash skills/jackett-transmission/skill.sh search "Example query" tv
bash skills/jackett-transmission/skill.sh add "magnet:?xt=urn:btih:..."
bash skills/jackett-transmission/skill.sh list
```

## Acciones soportadas

- `ping`: verifica conectividad básica con Jackett y Transmission.
- `search`: busca torrents en Jackett y devuelve resultados ordenados por seeders.
- `add`: añade a Transmission un magnet link o una URL descargable.
- `add-first`: busca en Jackett y añade automáticamente a Transmission el primer resultado con más seeders.
- `list`: lista los torrents activos o conocidos por Transmission.

## Categorías

Mapeo Torznab usado:
- `movie` → `2000`
- `tv` → `5000`
- `book` → `7000`
- `music` → `3000`
- `software` → `4000`
- `other` → sin filtro específico

## Seguridad

- No imprimir ni persistir la API key de Jackett.
- No pasar la API key por comandos shell cuando exista una tool `*-token`.
- Validar `target` para aceptar solo `magnet:`, `http://` o `https://`.
- `add-first` prioriza seeders, pero no sustituye validación humana del release.

## Resolución de problemas

- Si falta la credencial `jackett`, la skill devolverá `missing_credential_jackett`.
- Si un wrapper `agentcred` devuelve `missing_credential_jackett` pero la credencial existe, usar `get_credential("jackett")` + `jackett-search-token`.
- Si la conexión a Jackett o Transmission falla, revisar la URL configurada y la conectividad desde el runtime.
