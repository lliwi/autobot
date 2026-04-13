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
│              │ run, oauth     │                      │
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
    │  7 tablas │      │ cache/broker│
    └───────────┘      └─────────────┘
```

### Estructura del código

```
app/
├── __init__.py          # App factory + CLI commands (create-admin, onboard)
├── config.py            # Configuración por entorno
├── extensions.py        # SQLAlchemy, Flask-Login, Bcrypt, CSRF
├── logging_config.py    # JSON logging estructurado a stdout
├── models/              # SQLAlchemy: User, Agent, Session, Message, Run, ToolExecution, OAuthProfile
├── api/                 # Blueprints REST: auth, agents, chat (SSE), runs, oauth
│   ├── middleware.py    # Decoradores auth_required, admin_required
│   └── errors.py       # Manejadores de error JSON
├── dashboard/           # Vistas HTMX: overview, agents, chat
├── services/            # Lógica de negocio: auth, agent, session, chat, run, oauth
├── runtime/             # Motor del agente
│   ├── context_builder.py  # Ensambla system prompt desde workspace + historial
│   ├── model_client.py     # Wrapper OpenAI SDK con streaming
│   ├── tool_registry.py    # Registro de tools (built-in + dinámicas)
│   ├── tool_executor.py    # Ejecuta tools y registra en BD
│   └── agent_runner.py     # Loop de razonamiento (máx 10 rondas de tool calls)
├── workspace/           # Gestión de ficheros de workspace por agente
│   ├── manager.py       # CRUD de ficheros, scaffolding
│   └── loader.py        # Carga SOUL/AGENTS/MEMORY/TOOLS.md
├── templates/           # Jinja2 + HTMX
└── static/              # CSS + JS (chat.js para SSE)
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

### Workspace de agente

Cada agente tiene un directorio en `/workspaces/<slug>/`:

| Fichero | Propósito | Frecuencia de cambio |
|---|---|---|
| `SOUL.md` | Identidad, estilo, principios, límites | Rara vez |
| `AGENTS.md` | Catálogo de agentes/subagentes | Al crear/modificar agentes |
| `MEMORY.md` | Memoria persistente resumida | Consolidación periódica |
| `TOOLS.md` | Inventario de tools disponibles | Al registrar tools |

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

## Niveles de seguridad para automejora

| Nivel | Permitido | Ejemplo |
|---|---|---|
| 1 — Auto | Sin aprobación | Editar MEMORY.md, crear skills/tools |
| 2 — Revisión | Requiere aprobación | Modificar skills existentes, crear subagentes |
| 3 — Prohibido | Bloqueado en MVP | Modificar core Flask, OAuth, BD, políticas |

## Roadmap

- [x] **Fase 1 — Núcleo**: Flask, PostgreSQL, auth, chat SSE, runtime, workspace, OAuth
- [ ] **Fase 2 — Canales y Scheduler**: Matrix, heartbeat, cron, métricas completas
- [ ] **Fase 3 — Skills y Tools**: registro dinámico, carga desde workspace, panel
- [ ] **Fase 4 — Multiagente**: subagentes, delegación, topología
- [ ] **Fase 5 — Automejora**: patch proposals, diffs, tests, aprobación/rollback
- [ ] **Fase 6 — Hardening**: sandbox, observabilidad avanzada, límites finos
