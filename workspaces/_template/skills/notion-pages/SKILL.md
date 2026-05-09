---
name: notion-pages
version: 0.1.1
description: Interactúa con la API de Notion usando la credencial `notion` para listar páginas y crear páginas o subpáginas.
metadata:
  autobot:
    credential: "notion"
    preferred_tools:
      - "notion-blocks-lister-token"
      - "notion-page-search-token"
      - "notion-subpage-publisher-token"
    compatibility: "Autobot credential-resolution compatibility release"
---

# Notion Pages Reader/Publisher

Interactúa con la API de Notion usando la credencial `notion` para listar páginas y crear páginas o subpáginas.

## Changelog

### 0.1.1

- Validada disponibilidad real de Notion mediante credencial `notion` y lectura tokenizada de una página accesible.
- Documentado flujo Autobot recomendado: `get_credential("notion")` + herramientas `*-token` con token en memoria.
- Documentado fallo conocido de wrappers `agentcred` que pueden devolver falsos negativos como `missing notion credential`.
- Reforzada la regla de no incluir IDs o URLs privadas de páginas concretas en templates reutilizables.
- Añadida acción `verify_api` para comprobar conectividad y validez de la credencial.

### 0.1.0

- Versión inicial de lectura/publicación de páginas Notion.

## Autobot runtime notes

- Credencial requerida: `notion`.
- Página destino: debe proporcionarla el usuario o la configuración del workspace.
- Flujo preferente para operaciones reales:
  1. Obtener la credencial con `get_credential("notion")`.
  2. Usar herramientas tokenizadas como `notion-blocks-lister-token`, `notion-page-search-token` o `notion-subpage-publisher-token`.
  3. Mantener el token solo en memoria; no escribirlo en comandos shell, logs ni ficheros.
- Nota de troubleshooting: si `notion-page-search-agentcred` devuelve `missing notion credential`, usar el flujo tokenizado anterior.

## Capacidades

- Verificar conectividad y validez de la credencial (`verify_api`).
- Listar páginas a las que el token tiene acceso vía `/v1/search`.
- Crear una página nueva en una base de datos (`/v1/pages`).
- Crear una subpágina bajo una página padre (`/v1/pages` con `parent.page_id`).
- Generar y actualizar documentos con estructura clara, escaneable y visualmente agradable usando bloques de Notion.

## Entradas

- `action` (requerida): `verify_api`, `list_pages`, `create_page` o `create_subpage`.
- `query` (opcional, para `list_pages`): texto para filtrar resultados.
- `page_size` (opcional, para `list_pages`): máximo de resultados a pedir; por defecto `50`.
- `database_id` (requerida para `create_page`): id de la base de datos destino.
- `parent_page_id` (requerida para `create_subpage`): id de la página padre.
- `title` (requerida para `create_page` y `create_subpage`): título de la página.
- `content` (opcional): contenido en párrafos simples.

## Salida

- Para `verify_api`: `ok`, `message`, `result_count`.
- Para `list_pages`: lista con `id`, `title`, `url`, `last_edited_time`.
- Para `create_page`: objeto con `id`, `url`, `created_time`.
- Para `create_subpage`: objeto con `id`, `url`, `created_time`, `title`.

## Ejemplos

- `notion-pages(action="verify_api")`
- `notion-pages(action="list_pages")`
- `notion-pages(action="list_pages", query="Roadmap")`
- `notion-pages(action="create_page", database_id="...", title="Nota rápida", content="Hola")`
- `notion-pages(action="create_subpage", parent_page_id="...", title="skills", content="Documentación...")`

## Seguridad

- Requiere credencial `notion` de tipo token.
- Usa `Notion-Version: 2022-06-28`.
- No imprimir, persistir ni pasar el token por shell.
- No publicar IDs privados de páginas, bases de datos o workspaces en templates compartidos.

## Document creation guidance

Prefer hierarchy over long text, use headings every 3–5 blocks, avoid long paragraphs, and use bullets for scanability. Include callouts, toggles, tables or code blocks when they improve readability.
