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

# 3. Setup inicial interactivo (migraciones, usuario admin, clave de cifrado, OAuth)
docker compose run --rm web flask onboard

# 4. Abrir el dashboard
open http://localhost:5000
```

El comando `flask onboard` guía paso a paso la configuración inicial:
- Aplica las migraciones de base de datos
- Crea el usuario administrador
- Genera la clave de cifrado para tokens OAuth (`TOKEN_ENCRYPTION_KEY`)
- Muestra las variables necesarias para configurar OpenAI Codex OAuth

## Configuración

Todas las variables se definen en `.env`. Ver `.env.example` para referencia.

| Variable | Descripción | Requerida |
|---|---|---|
| `SECRET_KEY` | Clave secreta de Flask para sesiones | Sí |
| `DATABASE_URL` | URI de conexión a PostgreSQL | Sí |
| `REDIS_URL` | URI de conexión a Redis | Sí |
| `TOKEN_ENCRYPTION_KEY` | Clave Fernet para cifrar tokens OAuth en reposo | Para OAuth |
| `OPENAI_CLIENT_ID` | Client ID de la app OAuth de OpenAI | Para chat |
| `OPENAI_CLIENT_SECRET` | Client Secret de la app OAuth de OpenAI | Para chat |
| `OPENAI_REDIRECT_URI` | URI de callback OAuth | Para chat |
| `OPENAI_MODEL` | Modelo por defecto (default: `o4-mini`) | No |
| `MAX_CONTEXT_TOKENS` | Límite de tokens de contexto (default: `128000`) | No |
| `MAX_HISTORY_MESSAGES` | Mensajes de historial a incluir (default: `50`) | No |
| `MATRIX_HOMESERVER` | URL del servidor Matrix (e.g. `https://matrix.org`) | Para Matrix |
| `MATRIX_USER_ID` | User ID del bot Matrix (e.g. `@bot:matrix.org`) | Para Matrix |
| `MATRIX_PASSWORD` | Contraseña del bot Matrix | Para Matrix |
| `MATRIX_ALLOWED_ROOMS` | IDs de salas permitidas, separados por coma (vacío = todas) | No |
| `MATRIX_ALLOWED_USERS` | User IDs permitidos, separados por coma (vacío = todos) | No |
| `MATRIX_ALLOWED_DM_USERS` | Allowlist específica para DMs (vacío = usa `MATRIX_ALLOWED_USERS`) | No |
| `MATRIX_GROUP_POLICY` | Política de respuesta en grupo: `always`, `mention`, `allowlist` | No |
| `SCHEDULER_ENABLED` | Activar scheduler (default: `true`) | No |
| `HEARTBEAT_INTERVAL_MINUTES` | Intervalo de heartbeat en minutos (default: `15`) | No |

### Configuración de OpenAI Codex OAuth

1. Crear una aplicación OAuth en [platform.openai.com](https://platform.openai.com)
2. Configurar la redirect URI: `http://localhost:5000/api/oauth/openai/callback`
3. Añadir `OPENAI_CLIENT_ID` y `OPENAI_CLIENT_SECRET` en `.env`
4. Generar clave de cifrado: `docker compose run --rm web python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
5. Añadir `TOKEN_ENCRYPTION_KEY` en `.env`
6. Reiniciar: `docker compose restart web`
7. Desde el dashboard o via API, iniciar el flujo OAuth: `GET /api/oauth/openai/start`
8. Asociar el perfil OAuth a un agente

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
docker compose run --rm web flask onboard                                        # Setup interactivo
docker compose run --rm web flask create-admin --email user@mail.com --password pass  # Crear admin

# Desarrollo
docker compose build web          # Rebuild tras cambios en dependencias
docker compose run --rm web pytest                    # Ejecutar tests
docker compose run --rm web pytest tests/test_auth.py # Ejecutar un test específico
```

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
│              │ run, oauth,    │                      │
│              │ scheduler,     │                      │
│              │ metrics, matrix│                      │
│              └───────┬────────┘                      │
│                      │                               │
│  ┌───────────────────▼────────────────────────────┐  │
│  │              Agent Runtime                     │  │
│  │  context_builder → model_client → tool_exec    │  │
│  │       ▲                              │         │  │
│  │       │         agent_runner          │         │  │
│  │       └──────── (loop) ◄─────────────┘         │  │
│  └───────────────────┬────────────────────────────┘  │
│                      │                               │
│         ┌────────────▼──────────────┐                │
│         │    Workspace Manager      │                │
│         │  SOUL.md  AGENTS.md       │                │
│         │  MEMORY.md  TOOLS.md      │                │
│         │  skills/ tools/ agents/   │                │
│         └───────────────────────────┘                │
└─────────────────────────────────────────────────────┘
          │                    │
    ┌─────▼─────┐      ┌──────▼──────┐
    │PostgreSQL │      │    Redis    │
    │  8 tablas │      │ cache/broker│
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
├── __init__.py          # App factory + CLI commands (create-admin, onboard)
├── config.py            # Configuración por entorno
├── extensions.py        # SQLAlchemy, Flask-Login, Bcrypt, CSRF
├── logging_config.py    # JSON logging estructurado a stdout
├── models/              # SQLAlchemy: User, Agent, Session, Message, Run, ToolExecution, OAuthProfile, ScheduledTask, Skill, Tool, PatchProposal
├── api/                 # Blueprints REST: auth, agents, chat (SSE), runs, oauth, scheduler, metrics, skills, tools, subagents, patches
│   ├── middleware.py    # Decoradores auth_required, admin_required
│   └── errors.py       # Manejadores de error JSON
├── dashboard/           # Vistas HTMX: overview, agents, chat, scheduler, metrics, skills, tools, topology, subagents, patches
├── services/            # Lógica de negocio: auth, agent, session, chat, run, oauth, scheduler, metrics, matrix, skill, tool, subagent, patch, security_policy
├── runtime/             # Motor del agente
│   ├── context_builder.py  # Ensambla system prompt desde workspace + historial
│   ├── model_client.py     # Wrapper OpenAI SDK con streaming
│   ├── tool_registry.py    # Registro de tools (built-in + dinámicas)
│   ├── tool_executor.py    # Ejecuta tools y registra en BD
│   └── agent_runner.py     # Loop de razonamiento (máx 10 rondas de tool calls)
├── workspace/           # Gestión de ficheros de workspace por agente
│   ├── manager.py       # CRUD de ficheros, scaffolding
│   ├── loader.py        # Carga SOUL/AGENTS/MEMORY/TOOLS.md
│   ├── discovery.py     # Descubrimiento de skills/tools, sync con BD, carga dinámica
│   └── manifest.py      # Validación de manifiestos JSON
├── templates/           # Jinja2 + HTMX
└── static/              # CSS + JS (chat.js para SSE)

worker.py                # Entry point del worker (scheduler + Matrix)
app/worker/
├── scheduler.py         # APScheduler con Redis job store
└── matrix_adapter.py    # matrix-nio async client en daemon thread
```

### Modelo de datos

| Tabla | Descripción |
|---|---|
| `users` | Administradores del dashboard |
| `agents` | Agentes con su workspace, modelo y perfil OAuth |
| `sessions` | Sesiones de chat por canal (web, matrix) |
| `messages` | Historial de mensajes por sesión |
| `runs` | Ejecuciones del agente con métricas (tokens, coste, duración) |
| `tool_executions` | Registro de cada invocación de tool |
| `oauth_profiles` | Perfiles OAuth con tokens cifrados (Fernet) |
| `scheduled_tasks` | Tareas programadas (cron, heartbeat, one-shot) |
| `skills` | Skills registradas por agente (manifest, estado, fuente) |
| `tools` | Tools del workspace por agente (manifest, handler, timeout) |
| `patch_proposals` | Propuestas de automejora con diff, snapshot, nivel de seguridad y estado |
| `objectives` | Objetivos de trabajo del agente (goal-oriented, multi-run) |
| `heartbeat_events` | Registro de cada tick del supervisor (decisión, razón, snapshot) |

### Runtime del agente

El flujo de una interacción por chat:

1. El usuario envía un mensaje via `POST /api/chat`
2. Se crea/recupera una sesión y se persiste el mensaje
3. Se crea un registro Run para métricas
4. El `agent_runner` construye el contexto:
   - System prompt: `SOUL.md` + `TOOLS.md` + `AGENTS.md` + `MEMORY.md`
   - Historial de mensajes recientes
   - Mensaje del usuario
5. Se invoca el modelo con streaming via OpenAI SDK
6. Si el modelo pide tool calls, se ejecutan y se vuelve al paso 5 (máx 10 rondas)
7. Los tokens se emiten como SSE (`text/event-stream`) al cliente
8. Se persiste la respuesta y se finaliza el Run con métricas

### Tools built-in

| Tool | Descripción |
|---|---|
| `read_workspace_file` | Lee un fichero del workspace del agente |
| `list_workspace_files` | Lista todos los ficheros del workspace |
| `get_current_time` | Devuelve fecha y hora UTC actual |
| `delegate_task` | Delega una tarea a un sub-agente y devuelve el resultado |
| `list_subagents` | Lista los sub-agentes disponibles para delegación |
| `propose_change` | Propone un cambio a un fichero del workspace (auto-aplica L1, espera aprobación L2, rechaza L3) |
| `list_patches` | Lista propuestas de cambio recientes del agente |

### Workspace de agente

Cada agente tiene un directorio en `/workspaces/<slug>/`:

| Fichero | Propósito | Frecuencia de cambio |
|---|---|---|
| `SOUL.md` | Identidad, estilo, principios, límites | Rara vez |
| `AGENTS.md` | Catálogo de agentes/subagentes | Al crear/modificar agentes |
| `MEMORY.md` | Memoria persistente resumida | Consolidación periódica |
| `TOOLS.md` | Inventario de tools disponibles | Al registrar tools |
| `HEARTBEAT.md` | Checklist declarativa que lee el supervisor cada tick | Editable por el usuario |

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

## API

### Auth
- `POST /api/auth/login` — Login con email/password
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
- `GET /api/sessions` — Listar sesiones
- `GET /api/sessions/:id/messages` — Mensajes de una sesión

### Runs
- `GET /api/runs` — Listar ejecuciones (paginado, filtrable por `agent_id`)
- `GET /api/runs/:id` — Detalle de ejecución

### OAuth
- `GET /api/oauth/openai/start` — Iniciar flujo OAuth
- `GET /api/oauth/openai/callback` — Callback OAuth
- `POST /api/oauth/openai/refresh` — Refrescar tokens
- `GET /api/oauth/profiles` — Listar perfiles OAuth

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
- `POST /api/agents/:id/subagents` — Crear sub-agente (`{"name": "...", "role": "..."}`)
- `POST /api/agents/:id/delegate` — Delegar tarea (`{"target_agent_id": 2, "message": "..."}` o `{"target_name": "slug", "message": "..."}`)
- `GET /api/agents/topology` — Árbol completo de topología de agentes
- `GET /api/agents/:id/topology` — Subárbol desde un agente

### Skills
- `GET /api/skills` — Listar skills (filtrable por `agent_id`)
- `POST /api/skills` — Crear skill (`{"agent_id": 1, "name": "..."}`)
- `PATCH /api/skills/:id` — Actualizar
- `POST /api/skills/:id/reload` — Recargar manifest desde filesystem
- `POST /api/skills/:id/toggle` — Activar/desactivar
- `POST /api/skills/sync` — Sincronizar skills del workspace con BD

### Tools (workspace)
- `GET /api/tools` — Listar tools (filtrable por `agent_id`)
- `POST /api/tools` — Crear tool (`{"agent_id": 1, "name": "..."}`)
- `PATCH /api/tools/:id` — Actualizar
- `POST /api/tools/:id/toggle` — Activar/desactivar
- `POST /api/tools/:id/test` — Ejecutar tool con input de prueba
- `POST /api/tools/sync` — Sincronizar tools del workspace con BD

### Metrics
- `GET /api/metrics/runs-per-day` — Ejecuciones por día
- `GET /api/metrics/response-times` — Tiempos de respuesta promedio
- `GET /api/metrics/errors` — Errores por día
- `GET /api/metrics/usage-by-agent` — Uso por agente
- `GET /api/metrics/usage-by-channel` — Uso por canal
- `GET /api/metrics/usage-by-tool` — Uso por tool

## Niveles de seguridad para automejora

| Nivel | Permitido | Ejemplo |
|---|---|---|
| 1 — Auto | Sin aprobación | Editar MEMORY.md, crear skills/tools |
| 2 — Revisión | Requiere aprobación | Modificar skills existentes, crear subagentes |
| 3 — Prohibido | Bloqueado en MVP | Modificar core Flask, OAuth, BD, políticas |

## Roadmap

- [x] **Fase 1 — Núcleo**: Flask, PostgreSQL, auth, chat SSE, runtime, workspace, OAuth
- [x] **Fase 2 — Canales y Scheduler**: Matrix, heartbeat, cron, métricas completas, worker service
- [x] **Fase 3 — Skills y Tools**: modelos Skill/Tool, registro dinámico, descubrimiento desde workspace, validación de manifiestos, carga dinámica de handlers, integración con runtime, panel dashboard
- [x] **Fase 4 — Multiagente**: creación de sub-agentes, herencia de OAuth, delegación de tareas (síncrona), tools delegate_task/list_subagents, topología en dashboard, trazabilidad parent_run_id
- [x] **Fase 5 — Automejora**: modelo PatchProposal, motor de política de seguridad (L1/L2/L3), servicio de patches con diff unificado, snapshots y rollback, tools `propose_change`/`list_patches`, API y dashboard de aprobación
- [ ] **Fase 6 — Hardening**: sandbox, observabilidad avanzada, límites finos
