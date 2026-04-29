# Autobot

Plataforma de agente IA personal con arquitectura multiagente, automejora controlada, gateway web y canal Matrix.

## Requisitos

- Docker y Docker Compose

## Quick Start

```bash
# 1. Clonar y configurar
git clone <repo-url> && cd autobot
cp .env.example .env

# 2. Arrancar servicios
docker compose up -d

# 3. Setup inicial interactivo (migraciones, admin, clave de cifrado, OAuth, agentes, Matrix)
docker compose run --rm web flask onboard

# 4. Abrir el dashboard
open http://localhost:5000
```

El comando `flask onboard` guía paso a paso la configuración inicial:
- Aplica migraciones
- Crea el usuario administrador
- Genera la `TOKEN_ENCRYPTION_KEY` (Fernet) para cifrar credenciales y tokens OAuth
- Lanza el flujo OAuth de OpenAI Codex (PKCE, callback local en `localhost:1455`)
- Aprovisiona los dos agentes por defecto: **optimus** (orquestador) y **reviewer**
- Configura (opcional) el canal Matrix

## Configuración

Todas las variables se definen en `.env`. Ver `.env.example` para referencia.

| Variable | Descripción | Requerida |
|---|---|---|
| `SECRET_KEY` | Clave secreta de Flask para sesiones | Sí |
| `DATABASE_URL` | URI de conexión a PostgreSQL | Sí |
| `REDIS_URL` | URI de conexión a Redis | Sí |
| `TOKEN_ENCRYPTION_KEY` | Clave Fernet para cifrar credenciales y tokens OAuth en reposo | Sí |
| `OPENAI_MODEL` | Modelo Codex por defecto (e.g. `gpt-5.2`) | No |
| `MAX_CONTEXT_TOKENS` | Límite del contexto del modelo (default: `128000`) | No |
| `CONTEXT_RESPONSE_RESERVE_TOKENS` | Tokens reservados para la respuesta del modelo (default: `8000`) | No |
| `MAX_HISTORY_MESSAGES` | Cap legacy; ya no es el límite real, lo es el budget de tokens | No |
| `MAX_TOOL_ROUNDS` | Cap de rondas de tool-calls por run (default: `20`). Override por agente en `agents.max_tool_rounds` | No |
| `PATCHES_PER_HOUR_PER_AGENT` | Rate-limit de automejora: patches `applied + pending_review + approved` por hora y agente (default: `30`, `0` = desactivado) | No |
| `WORKSPACES_BASE_PATH` | Raíz de los workspaces (default: `./workspaces`) | No |
| `PACKAGE_ALLOWLIST` | PyPI auto-instalables en venvs de workspace (CSV) | No |
| `VENV_BASE_PACKAGES` | Packages preinstalados en cada venv nuevo (CSV) | No |
| `PIP_INSTALL_TIMEOUT_SECONDS` | Timeout de `pip install` (default: `180`) | No |
| `WORKSPACE_TOOL_TIMEOUT_SECONDS` | Timeout por ejecución de tool de workspace (default: `30`) | No |
| `AVATAR_UPLOAD_DIR` | Directorio de avatares subidos (default: `./instance/avatars`) | No |
| `AVATAR_MAX_BYTES` | Tamaño máximo por avatar (default: `2 MB`) | No |
| `MFA_ISSUER` | Issuer mostrado en apps TOTP (default: `Autobot`) | No |
| `AUTOBOT_CRED_<NAME>` | Credencial preseeded desde entorno — visible a los agentes por `get_credential` con `source=env` | No |
| `AUTOBOT_GITHUB_REPO` | Repo de GitHub destino de los PRs de promoción (default: `https://github.com/lliwi/autobot`). Útil si trabajas en un fork | No |
| `MATRIX_HOMESERVER` | URL del servidor Matrix (e.g. `https://matrix.org`) | Para Matrix |
| `MATRIX_USER_ID` | User ID del bot Matrix (e.g. `@bot:matrix.org`) | Para Matrix |
| `MATRIX_PASSWORD` | Contraseña del bot Matrix | Para Matrix |
| `MATRIX_ALLOWED_ROOMS` | IDs de salas permitidas (CSV, vacío = todas) | No |
| `MATRIX_ALLOWED_USERS` | User IDs permitidos (CSV, vacío = todos) | No |
| `MATRIX_ALLOWED_DM_USERS` | Allowlist DM (vacío = usa `MATRIX_ALLOWED_USERS`) | No |
| `MATRIX_GROUP_POLICY` | Política de respuesta en grupo: `always`, `mention`, `allowlist` | No |
| `SCHEDULER_ENABLED` | Activar scheduler (default: `true`) | No |
| `HEARTBEAT_INTERVAL_MINUTES` | Intervalo por defecto del heartbeat (default: `15`) | No |

### Codex OAuth

El login con Codex ya no usa un flujo web-OAuth redirigido al navegador del usuario. Se hace por
PKCE desde la línea de comandos:

```bash
docker compose run --rm web flask codex-login     # levanta callback en :1455 e imprime la URL
docker compose run --rm web flask codex-status    # muestra estado/cuenta
docker compose run --rm web flask codex-logout    # borra el token
```

`flask onboard` ejecuta `codex-login` automáticamente.

## Comandos

Todos los comandos se ejecutan dentro de Docker:

```bash
# Servicios
docker compose up -d              # Arrancar todo
docker compose down               # Parar todo
docker compose logs -f web        # Ver logs en tiempo real
docker compose restart web        # Reiniciar la app

# Base de datos
docker compose run --rm web flask db migrate -m "descripción"   # Crear migración
docker compose run --rm web flask db upgrade                     # Aplicar migraciones
docker compose run --rm web flask db downgrade                   # Revertir última migración

# Administración
docker compose run --rm web flask onboard                              # Setup interactivo completo
docker compose run --rm web flask setup-default-agents                 # (Re)configurar optimus + reviewer
docker compose run --rm web flask setup-matrix                         # Configurar sólo el canal Matrix
docker compose run --rm web flask create-admin --email u@m.com --password pw

# Codex
docker compose run --rm web flask codex-login
docker compose run --rm web flask codex-logout
docker compose run --rm web flask codex-status

# Backup / portabilidad
docker compose exec web flask export-bundle -o /tmp/autobot.tar.gz [--include-env] [--include-secrets]
docker compose exec web flask import-bundle -i /tmp/autobot.tar.gz [--overwrite]

# Desarrollo
docker compose build web                              # Rebuild tras cambios en dependencias
docker compose run --rm web pytest                    # Ejecutar tests
docker compose run --rm web pytest tests/test_auth.py # Ejecutar un test específico
```

### Exportar e importar una instalación

`export-bundle` snapshotea toda la instalación (DB + filesystem) en un `tar.gz` portable:

- `manifest.json` — versión de esquema, timestamp y contadores
- `agents.json`, `tools.json`, `skills.json`, `packages.json`, `credentials.json` — filas DB serializadas por `slug`
- `workspaces/<slug>/` — contenido completo del workspace (excluye `.venv`, `runs/`, `__pycache__/`)
- `.env` — opcional (`--include-env`)

Las credenciales se exportan **descifradas** sólo con `--include-secrets` (el tarball no está
cifrado, protégelo fuera). En el `import-bundle` se re-cifran con la `TOKEN_ENCRYPTION_KEY` del
destino, así que las dos instalaciones no necesitan compartir clave.

Conflictos en import:
- Sin `--overwrite`: filas y ficheros existentes se respetan (se cuentan como `skipped`).
- Con `--overwrite`: las filas se actualizan in-place (mismo id, FKs intactas) y los ficheros del workspace se reemplazan.

Los packages se importan siempre en `pending_review` (salvo los que ya estaban `rejected`) para
que el instalador del destino los regenere en su propio venv.

## Arquitectura

```
┌─────────────────────────────────────────────────────┐
│                   Gateway Web (Flask)                │
│  ┌──────────┐  ┌──────────┐  ┌───────────────────┐  │
│  │ API REST │  │Dashboard │  │   SSE Streaming   │  │
│  │ /api/*   │  │ HTMX     │  │   /api/chat       │  │
│  └────┬─────┘  └────┬─────┘  └────────┬──────────┘  │
│       └──────────────┼─────────────────┘             │
│                      │                               │
│              ┌───────▼────────┐                      │
│              │   Services     │                      │
│              │ auth, agent,   │                      │
│              │ chat, session, │                      │
│              │ run, codex,    │                      │
│              │ scheduler,     │                      │
│              │ metrics, matrix│                      │
│              │ review, creds, │                      │
│              │ packages,      │                      │
│              │ bundle, patches│                      │
│              └───────┬────────┘                      │
│                      │                               │
│  ┌───────────────────▼────────────────────────────┐  │
│  │              Agent Runtime                     │  │
│  │  context_builder → model_client → tool_exec    │  │
│  │  context_budget  ─── lazy manifest ──┐         │  │
│  │       ▲                              │         │  │
│  │       │         agent_runner          │         │  │
│  │       └──────── (loop) ◄─────────────┘         │  │
│  └───────────────────┬────────────────────────────┘  │
│                      │                               │
│         ┌────────────▼──────────────┐                │
│         │    Workspace Manager      │                │
│         │  SOUL.md  AGENTS.md       │                │
│         │  MEMORY.md  TOOLS.md      │                │
│         │  HEARTBEAT.md PACKAGES.md │                │
│         │  skills/ tools/ agents/   │                │
│         │  .venv/ (per-workspace)   │                │
│         └───────────────────────────┘                │
└─────────────────────────────────────────────────────┘
          │                    │
    ┌─────▼─────┐      ┌──────▼──────┐
    │PostgreSQL │      │    Redis    │
    │           │      │ cache/broker│
    └───────────┘      └─────────────┘
┌─────────────────────────────────────────────────────┐
│                 Worker Service                       │
│  ┌──────────────────┐  ┌─────────────────────────┐  │
│  │   APScheduler    │  │   Matrix Adapter        │  │
│  │  heartbeat/cron  │  │   matrix-nio async      │  │
│  └──────────────────┘  └─────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

### Estructura del código

```
app/
├── __init__.py          # App factory + CLI commands (onboard, codex-*, setup-*, export/import-bundle…)
├── config.py            # Configuración por entorno
├── extensions.py        # SQLAlchemy, Flask-Login, Bcrypt, CSRF
├── logging_config.py    # JSON logging + Redis ring buffer (compartido web+worker para la vista Logs)
├── models/              # SQLAlchemy — ver sección "Modelo de datos"
├── api/                 # Blueprints REST: auth, agents, chat (SSE), runs, scheduler, metrics, skills, tools, subagents, patches, credentials, packages
│   ├── middleware.py    # Decoradores auth_required, admin_required
│   └── errors.py        # Manejadores de error JSON
├── dashboard/           # Vistas HTMX: overview, agents, chat, scheduler, metrics, logs, skills, tools, topology, subagents, patches, credentials, packages, heartbeat
├── services/            # Lógica de negocio: auth, agent, session, chat, run, codex_auth, scheduler, metrics, matrix, skill, tool, subagent, patch, patch_validator, security_policy, credential, package, venv_manager, review, bundle
├── runtime/             # Motor del agente
│   ├── context_builder.py  # Ensambla system prompt + historial con budget de tokens
│   ├── context_budget.py   # Token counting (tiktoken cl100k_base) + drop-oldest trimming
│   ├── action_heuristics.py# Detecta promesas sin acción ("voy a…") para re-prompting
│   ├── model_client.py     # Wrapper Codex con streaming
│   ├── tool_registry.py    # Registro de tools built-in + cache per-run de lecturas
│   ├── tool_executor.py    # Ejecuta tools y persiste en tool_executions
│   └── agent_runner.py     # Loop de razonamiento (max_tool_rounds configurable, cap de 20K chars por tool result)
├── workspace/           # Gestión de ficheros de workspace por agente
│   ├── manager.py       # CRUD de ficheros, scaffolding, refresh TOOLS.md
│   ├── loader.py        # Carga SOUL/MEMORY/PACKAGES (AGENTS y TOOLS van al manifest lazy)
│   ├── discovery.py     # Descubrimiento de skills/tools, sync con BD, carga dinámica
│   └── manifest.py      # Validación de manifiestos JSON
├── templates/           # Jinja2 + HTMX
└── static/              # CSS + JS (chat.js con SSE + markdown + context meter)
    └── vendor/          # marked.min.js, purify.min.js, htmx.min.js (servidos en local)

worker.py                # Entry point del worker (scheduler + Matrix)
app/worker/
├── scheduler.py         # APScheduler con Redis job store
└── matrix_adapter.py    # matrix-nio async client en daemon thread
```

### Modelo de datos

| Tabla | Descripción |
|---|---|
| `users` | Administradores del dashboard (con MFA TOTP opcional) |
| `agents` | Agentes con slug, workspace, modelo, parent_agent_id, review_effort y review_token_budget_daily |
| `sessions` | Sesiones de chat por canal (web, matrix) |
| `messages` | Historial de mensajes por sesión |
| `runs` | Ejecuciones del agente con métricas (tokens, coste, duración, trigger_type) |
| `tool_executions` | Registro de cada invocación de tool (incluido output completo) |
| `scheduled_tasks` | Tareas programadas (cron, heartbeat, one-shot) |
| `skills` | Skills registradas por agente (manifest, estado, fuente) |
| `tools` | Tools de workspace por agente (manifest, path, timeout) |
| `patch_proposals` | Propuestas de automejora con diff, snapshot, nivel de seguridad y estado |
| `objectives` | Objetivos de trabajo del agente (goal-oriented, multi-run) |
| `heartbeat_events` | Registro de cada tick del supervisor (decisión, razón, snapshot) |
| `credentials` | Secretos cifrados con Fernet (API keys, user/password) — globales o por agente |
| `package_installations` | Packages Python por workspace con estado (pending_review / installing / installed / failed / rejected) |
| `approval_rules` | Reglas de aprobación automática para patches/packages (por agente, tipo y patrón) |
| `review_events` | Eventos auditados por el reviewer: qué revisó, qué dijo, tokens consumidos |
| `codex_quota_snapshots` | Snapshot histórico de cuotas/rate-limits devueltos por la API de Codex |

### Runtime del agente

El flujo de una interacción por chat:

1. El usuario envía un mensaje vía `POST /api/chat`.
2. Se crea/recupera la sesión y se persiste el mensaje.
3. Se crea un registro `Run` para métricas.
4. `context_builder` ensambla el contexto:
   - **Baseline de seguridad** (política plataforma) + `TOOL_PROTOCOL` (acción-first).
   - `SOUL.md`, `MEMORY.md`, `PACKAGES.md`, live roster de sub-agentes y pending review items.
   - **Workspace index (lazy)**: `TOOLS.md`, `AGENTS.md` y cada `SKILL.md` **NO** se inlinean — se listan con su path y descripción. El agente los lee bajo demanda con `read_workspace_file`.
   - Historial de mensajes pakado newest-first hasta llenar el token budget (`MAX_CONTEXT_TOKENS` − `CONTEXT_RESPONSE_RESERVE_TOKENS`). El mensaje del usuario y el system prompt siempre se preservan.
5. `agent_runner` invoca al modelo con streaming; tokens emitidos como SSE.
6. Si hay tool calls se ejecutan y se vuelve al paso 5 (máx 10 rondas). Resultados > 20 KB se truncan en el contexto pero se guardan enteros en `tool_executions`. Mismo fichero leído dos veces en un run devuelve un stub cacheado.
7. Detección anti-"voy a hacerlo pero no lo hago": si el agente promete sin llamar tool y la petición era accionable, se re-inyecta una nudge-system y se reintenta una vez.
8. Se persiste la respuesta final y el `Run` se cierra con métricas.

### Gestión de contexto

El budget de tokens (`MAX_CONTEXT_TOKENS` − `CONTEXT_RESPONSE_RESERVE_TOKENS`) se calcula con
`tiktoken` (cl100k_base) y se aplica en cada turno:

- El system prompt y el mensaje del usuario actual son intocables.
- El historial se packea newest-first; los mensajes más antiguos que no caben se descartan.
- Los ficheros grandes del workspace (`TOOLS.md`, `AGENTS.md`, cada `SKILL.md`) se sirven por manifest, no inline.
- El chat muestra un **indicador de uso de contexto** (barra verde/ámbar/rojo) debajo del input con `input_tokens / budget` del último turno.
- `GET /api/chat/context?agent_id=…[&session_id=…]` devuelve `{total_tokens, budget, pct, message_count}` para refrescar el indicador desde UI.

### Chat con Markdown

Las respuestas del asistente se renderizan con `marked` + `DOMPurify` (vendored bajo
`app/static/vendor/`). El toggle "Hide tool" persiste en `localStorage`. Los tokens se acumulan en
un buffer y se re-renderizan en cada chunk SSE sin flicker.

### Tools built-in

Todos disponibles vía tool calls del modelo. Las descripciones concretas y parámetros están en
`app/runtime/tool_registry.py`.

| Tool | Descripción |
|---|---|
| `read_workspace_file` | Lee un fichero del workspace. Lecturas repetidas en el mismo run devuelven stub |
| `list_workspace_files` | Lista todos los ficheros del workspace |
| `get_current_time` | Devuelve fecha y hora UTC actual |
| `fetch_url` | GET/POST HTTP con headers, body y timeout; fallback cuando no hay tool específico |
| `delegate_task` | Delega a un sub-agente por slug o nombre; devuelve la respuesta |
| `list_subagents` | Lista los sub-agentes activos del agente actual |
| `create_skill` | Crea un skill completo (SKILL.md + handler) en una sola llamada |
| `create_tool` | Crea un tool de workspace (manifest + tool.py) |
| `propose_change` | Propone un cambio de fichero; L1 auto-aplica, L2 espera aprobación, L3 rechaza |
| `list_patches` | Lista propuestas recientes del agente |
| `schedule_task` | Crea una tarea cron para este agente (`schedule_expr`, `message`) |
| `list_scheduled_tasks` | Lista tareas programadas del agente |
| `cancel_scheduled_task` | Cancela una tarea programada por id |
| `get_credential` | Resuelve una credencial por nombre: DB agent-scoped → DB global → `AUTOBOT_CRED_<NAME>` |
| `list_credentials` | Lista nombres y metadata (sin valores) |
| `set_credential` | Crea o reemplaza una credencial agent-scoped |
| `delete_credential` | Borra una credencial agent-scoped |
| `install_package` | Solicita un package PyPI para el venv del workspace (auto-install si está allowlisted) |
| `list_packages` | Lista el historial de instalaciones del agente con estado |

### Credenciales

Almacén cifrado con Fernet en la tabla `credentials`, gestionado desde el dashboard y accesible
por los agentes vía `get_credential`.

- Scope: `agent_id IS NULL` = global; `agent_id` = sólo ese agente (shadowa a la global con el mismo nombre).
- Tipos: `token` (valor único) y `user_password` (username + password).
- Fallback `AUTOBOT_CRED_<UPPER(NAME)>`: permite preseeded de credenciales por `.env`; devuelve `source="env"` al consultarse.
- Los valores sólo aparecen en claro cuando el admin pulsa "Reveal" en la UI o cuando el agente llama a `get_credential`. Nunca se serializan en las respuestas de listado.

### Packages por workspace

Cada workspace tiene un venv aislado en `<workspace>/.venv`. El agente solicita instalaciones con
`install_package`:

- Si el spec normalizado está en `PACKAGE_ALLOWLIST` → auto-install.
- Si no → fila en `pending_review`, el admin aprueba/rechaza desde el dashboard.
- Estados: `pending_review` → `approved` → `installing` → `installed` / `failed` / `rejected`.
- `VENV_BASE_PACKAGES` se instala en cada venv nuevo para tener un kit base.

### Workspace de agente

Cada agente tiene un directorio en `/workspaces/<slug>/`:

| Fichero / dir | Propósito | Frecuencia de cambio |
|---|---|---|
| `SOUL.md` | Identidad, estilo, principios, límites | Rara vez |
| `AGENTS.md` | Catálogo de agentes/subagentes (lazy-loaded) | Al crear/modificar agentes |
| `MEMORY.md` | Memoria persistente resumida | Consolidación periódica |
| `TOOLS.md` | Inventario de tools disponibles (lazy-loaded) | Regenerado al registrar tools |
| `HEARTBEAT.md` | Checklist declarativa que lee el supervisor cada tick | Editable por el usuario |
| `PACKAGES.md` | Packages instalados en el venv del workspace | Automático |
| `skills/` | SKILL.md y handler por skill | Creados con `create_skill` |
| `tools/` | Manifest y tool.py por tool | Creados con `create_tool` |
| `patches/` | Snapshots pre/post de patches aplicados | Automático |
| `runs/` | Logs de ejecuciones (excluido de exports) | Automático |
| `.venv/` | Entorno Python aislado (excluido de exports) | Regenerado al importar |

### Heartbeat supervisor

El heartbeat **no es un temporizador** — es el bucle de supervisión que mantiene
al agente "vivo" entre interacciones. Cada tick hace cinco cosas:

1. **Snapshot del mundo**: lee `HEARTBEAT.md`, consulta objetivos activos,
   detecta `Run` atascadas (`status=running` durante más de 15 min → marca
   `stuck`) e identifica el último canal activo (web / matrix).
2. **Decisión por reglas** (sin LLM): si no hay nada accionable → `skip`; si
   hay un `Run` vivo o acaba de actuar (< 60 s) → `defer`; si hay tasks vivas
   u objetivos vencidos → `act`.
3. **Ejecución contextualizada**: cuando decide actuar, construye un prompt
   con el snapshot (no un prompt estático) y lanza un `Run` con
   `trigger_type=heartbeat`, enrutado al último canal activo.
4. **Telemetría**: cada tick (`act` / `skip` / `defer`) queda registrado en
   `heartbeat_events` con el snapshot y el `run_id` si hubo ejecución.
5. **Cadencia**: el intervalo (minutos) se configura por agente en
   `agents.heartbeat_interval`; APScheduler dispara `_execute_heartbeat` que
   delega en `app.services.heartbeat_supervisor.tick(agent_id)`.

**Sintaxis de `HEARTBEAT.md`**: cada entrada es un `- item` con directivas
opcionales inline:

```markdown
- Revisar errores recientes. every: 2h priority: high
- [done] Tarea antigua (ignorada por el supervisor)
- Consolidar MEMORY.md si está ruidosa. every: 1d priority: low
```

**Objectives** (tabla `objectives`): unidad de trabajo multi-run que sobrevive
entre interacciones. Estados: `active`, `blocked`, `waiting`, `done`,
`cancelled`. El supervisor sólo considera los `active` cuyo `next_check_at`
haya vencido (o sea `NULL`). Se gestionan desde `/agents/<id>/heartbeat`.

### Reviewer y review gating

Cada agente puede tener un sub-agente de tipo `reviewer` (creado por `setup-default-agents`). Los
puntos de auditoría se activan por `review_effort` (0 = off, 10 = auditoría total). Cuando `create_skill`,
`create_tool` o `schedule_task` ejecutan, la respuesta puede llevar un campo `review` con el
feedback del reviewer. `review_token_budget_daily` limita el gasto diario en auditorías por agente
(cuando se supera, el review_gate se cierra hasta el siguiente día UTC). Todo queda auditado en
`review_events`.

## Promover tools/skills a la instalación base

Una vez que una tool o skill lleva tiempo funcionando en producción —con sus patches aplicados y validados— el admin puede **promoverla a la plantilla por defecto** (`workspaces/_template/`). Los agentes creados a partir de ese momento heredarán la tool/skill automáticamente al ejecutar `scaffold_workspace`.

El mecanismo tiene tres niveles:

### Nivel 1 — Bundle descargable (siempre disponible)

Genera un `tar.gz` listo para revisar y abrir un PR manualmente:

- `workspaces/_template/<tools|skills>/<slug>/` — ficheros de la tool/skill con su estado actual post-patches
- `PROMOTION.md` — metadatos: agente origen, versión, número de patches aplicados, resultado de validación, instrucciones de test
- `promote.patch` — diff unificado contra lo que ya hay en `_template/` (vacío si es nueva)

Desde el dashboard (admin): **Agents → Tools/Skills del agente → `📦 Bundle`**

Desde la API:
```bash
curl -X POST /api/admin/promote/bundle \
  -H "Content-Type: application/json" \
  -d '{"type": "tool", "agent_id": 1, "slug": "mi-tool"}'
# Retorna {"ok": true, "bundle_name": "mi-tool-20260427.tar.gz", "diff": "...", "pr_title": "...", "pr_body": "..."}

# Descargar el bundle
curl /api/admin/promote/bundle/mi-tool-20260427.tar.gz -o promotion.tar.gz
```

Con el bundle descargado, aplicar al repo y crear el PR:
```bash
tar -xzf promotion.tar.gz          # extrae en workspaces/_template/tools/mi-tool/
git checkout -b promote/tool/mi-tool
git add workspaces/_template/
git commit -m "promote(tool): mi-tool v1.2"
gh pr create --title "promote(tool): mi-tool" --body "$(cat PROMOTION.md)"
```

### Nivel 2 — PR automático en GitHub (requiere `gh_token`)

Si hay un token de GitHub configurado, la app crea la rama, hace el commit y abre el PR directamente.

**Configurar el token** (una de las dos vías):

| Vía | Cómo |
|---|---|
| Credencial cifrada (recomendado) | Dashboard → **Credentials → New** → nombre `gh_token`, tipo `token`, scope global |
| Variable de entorno | `GH_TOKEN=ghp_xxx` en `.env` (o `AUTOBOT_CRED_GH_TOKEN=ghp_xxx`) |

El sistema busca en este orden: credencial `gh_token` en BD → `AUTOBOT_CRED_GH_TOKEN` en env → `GH_TOKEN` en env.

El repo destino del PR se toma de `AUTOBOT_GITHUB_REPO` en `.env` (default: `https://github.com/lliwi/autobot`). Si trabajas en un fork, añade:

```env
AUTOBOT_GITHUB_REPO=https://github.com/tu-usuario/autobot
```

Desde el dashboard (admin): **Agents → Tools/Skills del agente → `🔀 PR`**

Desde la API:
```bash
curl -X POST /api/admin/promote/pr \
  -H "Content-Type: application/json" \
  -d '{"type": "skill", "agent_id": 2, "slug": "mi-skill"}'
# Retorna {"ok": true, "pr_url": "https://github.com/lliwi/autobot/pull/42", "branch": "promote/skill/mi-skill"}
```

La rama creada sigue el patrón `promote/<type>/<slug>`. Si ya existe, se añade un sufijo de timestamp.

### Nivel 3 — Broadcast a todos los agentes existentes

Además de actualizar `_template/`, copia la tool/skill a todos los agentes que no la tengan:

```bash
curl -X POST /api/admin/promote/broadcast \
  -H "Content-Type: application/json" \
  -d '{"type": "tool", "agent_id": 1, "slug": "mi-tool"}'
# Retorna {"ok": true, "broadcast_copied": 3, "broadcast_errors": ["otro-agente: already has tool"]}
```

Los errores de slug duplicado se acumulan en `broadcast_errors` sin interrumpir el proceso.

### Estado en el dashboard

La columna **Template** en la lista de tools/skills muestra `★ En template` cuando el slug ya existe en `_template/`, o `☆ No promovida` si aún no se ha promovido. Los botones de promoción sólo son visibles para admins.

### Validación previa

Antes de generar el bundle o el PR, la tool/skill pasa por los mismos checks que los patches:
- JSON parseable y con forma válida de manifiesto
- Sintaxis Python correcta (AST)
- Presencia del `def handler(...)` (tools)
- Smoke import en subprocess (usando el venv del workspace)

Si alguno falla, la operación se cancela con el error detallado.

## API

### Auth
- `POST /api/auth/login` — Login con email/password (+ TOTP si está activado)
- `POST /api/auth/logout` — Cerrar sesión
- `GET /api/auth/me` — Usuario actual

### Agents
- `GET /api/agents` — Listar agentes
- `POST /api/agents` — Crear agente (`{"name": "..."}`)
- `GET /api/agents/:id` — Detalle
- `PATCH /api/agents/:id` — Actualizar
- `POST /api/agents/:id/start` — Activar
- `POST /api/agents/:id/stop` — Desactivar

### Chat
- `POST /api/chat` — Enviar mensaje, respuesta SSE (`{"agent_id": 1, "message": "hola"}`)
- `GET /api/chat/context?agent_id=…[&session_id=…]` — Tamaño del contexto del próximo turno
- `GET /api/chat/history?agent_id=…` — Historial de la sesión activa
- `GET /api/sessions` — Listar sesiones
- `GET /api/sessions/:id/messages` — Mensajes de una sesión

### Runs
- `GET /api/runs` — Listar ejecuciones (paginado, filtrable por `agent_id`)
- `GET /api/runs/:id` — Detalle de ejecución

### Scheduled Tasks
- `GET /api/scheduled-tasks` — Listar tareas (filtrable por `agent_id`)
- `POST /api/scheduled-tasks` — Crear tarea
- `GET /api/scheduled-tasks/:id` — Detalle
- `PUT /api/scheduled-tasks/:id` — Actualizar
- `DELETE /api/scheduled-tasks/:id` — Eliminar
- `POST /api/scheduled-tasks/:id/toggle` — Activar/desactivar

### Heartbeat & Objectives (dashboard)
- `GET  /agents/:id/heartbeat` — Panel con objetivos, últimos ticks y `HEARTBEAT.md`
- `POST /agents/:id/heartbeat/tick` — Disparar un tick manual del supervisor
- `POST /agents/:id/objectives/create` — Crear un objetivo (`title`, `description`)
- `POST /objectives/:id/update` — Actualizar estado (`active`/`blocked`/`waiting`/`done`/`cancelled`)
- `POST /objectives/:id/delete` — Eliminar un objetivo

### Self-improvement (patches)
- `GET /api/patches` — Listar patches (filtrable por `agent_id`, `status`)
- `GET /api/patches/:id` — Detalle con diff completo
- `POST /api/patches/:id/approve` — Aprobar patch L2 pendiente
- `POST /api/patches/:id/reject` — Rechazar patch
- `POST /api/patches/:id/apply` — Aplicar patch aprobado al workspace
- `POST /api/patches/:id/rollback` — Revertir patch aplicado usando snapshot

### Sub-agents
- `GET /api/agents/:id/subagents` — Listar sub-agentes de un agente
- `POST /api/agents/:id/subagents` — Crear sub-agente
- `POST /api/agents/:id/delegate` — Delegar tarea
- `GET /api/agents/topology` — Árbol completo de topología de agentes
- `GET /api/agents/:id/topology` — Subárbol desde un agente

### Skills
- `GET /api/skills` — Listar skills (filtrable por `agent_id`)
- `POST /api/skills` — Crear skill
- `PATCH /api/skills/:id` — Actualizar
- `POST /api/skills/:id/reload` — Recargar manifest desde filesystem
- `POST /api/skills/:id/toggle` — Activar/desactivar
- `POST /api/skills/sync` — Sincronizar skills del workspace con BD

### Tools (workspace)
- `GET /api/tools` — Listar tools (filtrable por `agent_id`)
- `POST /api/tools` — Crear tool
- `PATCH /api/tools/:id` — Actualizar
- `POST /api/tools/:id/toggle` — Activar/desactivar
- `POST /api/tools/:id/test` — Ejecutar tool con input de prueba
- `POST /api/tools/sync` — Sincronizar tools del workspace con BD

### Promoción a plantilla (admin)
- `GET /api/admin/promote/status?type=tool|skill&slug=...` — Estado de promoción (en template, rama activa)
- `POST /api/admin/promote/bundle` — Generar bundle descargable (`{"type", "agent_id", "slug"}`)
- `GET /api/admin/promote/bundle/<filename>` — Descargar bundle generado
- `POST /api/admin/promote/pr` — Crear PR en GitHub automáticamente (`{"type", "agent_id", "slug"}`)
- `POST /api/admin/promote/broadcast` — Copiar a todos los agentes existentes (`{"type", "agent_id", "slug"}`)

### Credenciales
- `GET /api/credentials` — Listar (metadata + preview redactado)
- `POST /api/credentials` — Crear credencial
- `PATCH /api/credentials/:id` — Actualizar
- `DELETE /api/credentials/:id` — Borrar
- `GET /api/credentials/:id/reveal` — Ver valor en claro (requiere admin)

### Packages
- `GET /api/packages` — Listar instalaciones (filtrable por `agent_id`, `status`)
- `POST /api/packages/:id/approve` — Aprobar y lanzar instalación
- `POST /api/packages/:id/reject` — Rechazar
- `POST /api/packages/:id/retry` — Re-intentar una instalación fallida

### Metrics
- `GET /api/metrics/runs-per-day` — Ejecuciones por día
- `GET /api/metrics/response-times` — Tiempos de respuesta promedio
- `GET /api/metrics/errors` — Errores por día
- `GET /api/metrics/usage-by-agent` — Uso por agente
- `GET /api/metrics/usage-by-channel` — Uso por canal
- `GET /api/metrics/usage-by-tool` — Uso por tool

## Automejora (self-improvement)

### Niveles de seguridad

| Nivel | Permitido | Ejemplo |
|---|---|---|
| 1 — Auto | Sin aprobación | Editar MEMORY.md, crear skills/tools |
| 2 — Revisión | Requiere aprobación | Modificar skills existentes, crear subagentes |
| 3 — Prohibido | Bloqueado en MVP | Modificar core Flask, OAuth, BD, políticas |

### Pipeline de validación

Todo `propose_change` pasa por un validador estático antes de tocar disco, y
vuelve a pasar en el momento del `apply` como red de seguridad. Checks actuales
(ver [app/services/patch_validator.py](app/services/patch_validator.py)):

| Check | Se ejecuta en | Bloquea si |
|---|---|---|
| `json_parse` | ficheros `.json` | no es JSON válido |
| `manifest_shape` | `manifest.json` | falta `name` / `description`; `parameters` no es objeto |
| `python_syntax` | ficheros `.py` | `ast.parse` falla |
| `tool_handler_present` | `tools/*/tool.py` | no define `def handler(...)` top-level |
| `smoke_import` | ficheros `.py` | importar el módulo falla (timeout 10 s, corre en el venv del workspace vía subprocess) |

Un fallo de validación → el patch se queda en `rejected` con el detalle en
`test_result_json.validation.error`. En el detalle del patch se pueden re-lanzar
las validaciones manualmente con el botón **Re-run validations**.

### Rate limit

`PATCHES_PER_HOUR_PER_AGENT` (default `30`) limita cuántos patches puede generar
un agente por hora deslizante. Sólo cuentan los estados "con coste"
(`applied + pending_review + approved`); los rechazados por el validador no
consumen cupo, para no penalizar un primer intento sintácticamente roto.

### Flujo completo

1. El agente invoca `propose_change` / `create_skill` / `create_tool`.
2. Clasificación L1/L2/L3 + chequeo de rate-limit + validación estática.
3. Si pasa validación: snapshot → L1 auto-apply; L2 busca rule → reviewer
   sub-agent → queda pendiente si ninguno aprueba; L3 rechazado.
4. En `apply` (manual o automático) re-validación, escritura al workspace y
   marcado de `applied_at`.
5. `rollback` restaura desde el snapshot guardado en `workspaces/<slug>/patches/`.

## Observabilidad

### Logs (ring buffer)

Todos los registros de web y worker se envían a una lista Redis
(`autobot:logs`, cap 5000) además de a stdout. El dashboard incluye una vista
**Observability → Logs** con filtros por nivel, proceso (`web`/`worker`),
logger, mensaje, y auto-refresh cada 5 s. Útil para ver en un solo timeline
qué hizo el scheduler, el Matrix adapter y el runtime del agente.

## Roadmap

- [x] **Fase 1 — Núcleo**: Flask, PostgreSQL, auth, chat SSE, runtime, workspace, Codex OAuth (PKCE CLI)
- [x] **Fase 2 — Canales y Scheduler**: Matrix, heartbeat, cron con timezones, métricas, worker service
- [x] **Fase 3 — Skills y Tools**: registro dinámico, descubrimiento desde workspace, validación, carga dinámica, panel dashboard
- [x] **Fase 4 — Multiagente**: sub-agentes, herencia de OAuth, delegación síncrona, tools `delegate_task` / `list_subagents`, topología, trazabilidad `parent_run_id`
- [x] **Fase 5 — Automejora**: `PatchProposal`, política L1/L2/L3, diff unificado, snapshots + rollback, review-gating con sub-agente reviewer, **validador estático** (JSON/AST/handler/smoke-import), **rate-limit por agente**, UI de patches con checks detallados y botón "Re-run validations"
- [x] **Credenciales**: store cifrado Fernet, scopes global/agente, fallback env, tools `get/set/list/delete_credential`
- [x] **Packages por workspace**: venv aislado, allowlist, aprobación en dashboard, tools `install_package` / `list_packages`
- [x] **Gestión de contexto**: token budget con `tiktoken`, drop-oldest, workspace index lazy-loaded, indicador de uso en chat
- [x] **Chat markdown**: render seguro con marked + DOMPurify
- [x] **Portabilidad**: `flask export-bundle` / `flask import-bundle` para clonar una instalación entera
- [x] **Promoción a plantilla**: tools y skills probadas en producción se promueven a `workspaces/_template/` vía bundle descargable o PR automático en GitHub (`gh_token` desde credenciales cifradas o env); broadcast opcional a todos los agentes existentes
- [x] **Logs centralizados**: ring buffer Redis compartido web+worker, vista `Observability → Logs` con filtros + auto-refresh
- [x] **Tests del core (bootstrap)**: suite `tests/` con 67 casos en pytest cubriendo `patch_validator` (JSON/AST/handler/smoke-import), `security_policy` (clasificación L1/L2/L3), `approval_rule_service` (patrones + CRUD) y `patch_service` (propose/approve/apply/reject/rollback, no-op, dedup, rate-limit). Se ejecutan con `docker compose run --rm web pytest`.
- [ ] **Fase 6 — Hardening** (pendiente):
    - **Ampliar cobertura de tests**: la suite base ya corre en verde. Queda extenderla a `scheduler_service` (cron + heartbeat), `agent_runner` (tool rounds, context budget), `review_service` (effort dial, sampling) y a un flow de integración end-to-end del chat SSE con runtime mockeado.
    - **Sandbox real de ejecución**: workspace tools corren hoy en un venv por agente pero comparten filesystem/red/CPU del contenedor. Aislar con un contenedor efímero (Docker-in-Docker o `firecracker`/`bwrap`), network egress controlado, cuotas de CPU/memoria/filesystem.
    - **Límites finos por agente**: cap diario de tokens / coste € por agente (`agents.daily_token_budget`, `daily_cost_budget_eur`) con freno automático cuando se supera. Hoy sólo existe `review_token_budget_daily` para el reviewer.
    - **Métricas de coste**: columna `cost_eur` ya se persiste en `runs`; falta panel en Metrics con € por día / por agente / por tool, y alertas cuando sobrepasa umbral.
    - **Rotación de credenciales**: marca `rotate_after` en `credentials`, aviso en dashboard cuando caduca. Hoy el admin las edita a mano sin recordatorio.
    - **Rate-limit en APIs sensibles**: `RATELIMIT_STORAGE_URI` está configurado pero ningún endpoint lo usa todavía (login brute-force, chat flood).
    - **Endpoint `/health`**: requisito de §9.5 — expone estado de DB, Redis, Matrix, scheduler, Codex OAuth.
    - **Métricas Prometheus**: `/metrics` en formato Prometheus para observabilidad externa.
    - **Auditoría firmada**: hoy los patches quedan en `patch_proposals` pero no hay hash encadenado que permita detectar manipulación retrospectiva del historial.
