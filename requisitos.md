# Requisitos técnicos — Agente IA tipo Clawbot/Nanobot en Flask

## 1. Objetivo del producto

Construir un agente IA personal, orientado a arquitectura multiagente, ejecutado sobre **Python + Flask**, con dos superficies principales de interacción:

* **Gateway web** para chat, administración y observabilidad.
* **Matrix** como canal de mensajería externo.

El agente deberá estar diseñado para:

* operar de forma persistente y 24/7,
* automejorarse de manera controlada,
* crear o ampliar **skills** y **tools**,
* proponer y crear **subagentes** cuando detecte una necesidad,
* mantener memoria y configuración mediante ficheros del workspace,
* exponer un panel web de control y métricas,
* trabajar inicialmente **solo con OpenAI Codex vía OAuth**.

---

## 2. Alcance inicial (MVP)

### 2.1 Incluido en MVP

1. Backend en **Python 3.11+** con **Flask**.
2. **Gateway web** con:

   * chat básico,
   * panel de configuración,
   * panel de métricas,
   * gestión de agentes,
   * gestión de skills,
   * revisión de ejecuciones.
3. Canal **Matrix**:

   * recepción de mensajes,
   * envío de respuestas,
   * sesiones por usuario/sala,
   * soporte básico para texto.
4. Runtime de agente con:

   * loop de razonamiento,
   * ejecución de tools,
   * lectura de memoria por ficheros,
   * streaming de respuesta si el modelo lo permite.
5. Persistencia de contexto estructurada con:

   * `SOUL.md`
   * `AGENTS.md`
   * `MEMORY.md`
   * `TOOLS.md`
6. Sistema de **skills** instalables desde el workspace.
7. **Heartbeat** y **cron** para tareas programadas.
8. Soporte de **subagentes** y orquestación básica.
9. Integración con **OpenAI Codex por OAuth**.
10. Sistema de automejora **controlada**, con revisión, diff y rollback.

### 2.2 Fuera de alcance del MVP

1. Otros proveedores LLM distintos de OpenAI Codex.
2. Otros canales además de Gateway web y Matrix.
3. Voz, audio, visión o ejecución multimodal avanzada.
4. Marketplace público de skills.
5. Autoedición completamente autónoma sin políticas ni revisión.
6. Alta disponibilidad distribuida multi-nodo.

---

## 3. Principios de arquitectura

1. **Gateway como plano de control**, no como lógica de negocio acoplada.
2. **Workspace-first**: el comportamiento del agente debe depender de ficheros persistentes y versionables.
3. **Multiagente real**: agentes y skills como piezas separables.
4. **Self-improvement seguro**: toda modificación de código o prompts persistentes debe ser auditable.
5. **Extensible por plugins**: tools, skills y conectores deben poder añadirse sin reescribir el core.
6. **Aislamiento por agente/tarea** cuando haya ejecución de código.
7. **Observabilidad desde el día 1**: logs, eventos, consumos, métricas y trazas.

---

## 4. Arquitectura propuesta

## 4.1 Componentes principales

### A. Gateway Web (Flask)

Responsable de:

* API HTTP/REST.
* WebSocket/SSE para streaming de chat y eventos.
* Dashboard de administración.
* Autenticación de usuarios administradores.
* Exposición de métricas y estado.

### B. Agent Runtime

Responsable de:

* construir el contexto del agente,
* leer archivos del workspace,
* seleccionar tools/skills,
* invocar el modelo,
* ejecutar acciones,
* coordinar subagentes,
* registrar eventos y resultados.

### C. Channel Adapter: Matrix

Responsable de:

* consumir eventos de Matrix,
* normalizar mensajes entrantes,
* mapearlos a sesiones del runtime,
* devolver respuestas y estados.

### D. Workspace Manager

Responsable de:

* cargar y guardar `SOUL.md`, `AGENTS.md`, `MEMORY.md`, `TOOLS.md`,
* administrar skills,
* administrar tools generadas,
* versionar cambios.

### E. Scheduler

Responsable de:

* heartbeat periódico,
* cron jobs,
* tareas diferidas,
* reintentos,
* recuperación tras reinicios.

### F. Self-Improvement Engine

Responsable de:

* detectar carencias de tools/skills/agentes,
* proponer cambios,
* generar diff,
* ejecutar tests,
* solicitar aprobación si aplica,
* desplegar cambios en caliente o diferido,
* permitir rollback.

### G. Observability & Billing Layer

Responsable de:

* métricas por ejecución,
* consumo de tokens,
* coste estimado,
* tiempos de respuesta,
* uso por agente,
* uso por skill/tool,
* errores.

### H. Persistence Layer

Responsable de:

* base de datos transaccional,
* cola de tareas,
* almacenamiento de ficheros de workspace,
* snapshots/versiones.

---

## 5. Stack técnico recomendado

## 5.1 Backend

* **Python 3.11+**
* **Flask** para API y panel
* **Flask-SocketIO** o SSE para streaming
* **SQLAlchemy** como ORM
* **Alembic** para migraciones
* **Pydantic** para validación de esquemas

## 5.2 Tareas asíncronas

* **Celery** o **RQ** para jobs
* **Redis** como broker/cola/cache
* Alternativa más simple para MVP: **APScheduler** + Redis

## 5.3 Base de datos

* **PostgreSQL** como base principal

## 5.4 Frontend dashboard

* Puede ser:

  * plantillas Flask + HTMX para MVP, o
  * frontend separado en React/Vue si se prioriza UX.

Recomendación MVP: **Flask + Jinja + HTMX** para ir más rápido.

## 5.5 Matrix

* SDK Python de Matrix, preferiblemente **matrix-nio**.

## 5.6 Autenticación OpenAI Codex

* OAuth con almacenamiento seguro de sesión/tokens.
* Cifrado de credenciales sensibles en reposo.

## 5.7 Ejecución segura de código

* **Subprocess** controlado en MVP.
* Evolución recomendada: **Docker sandbox** por tarea/agente.

---

## 6. Estructura del workspace

Cada agente deberá tener un workspace con estructura similar a:

```text
/workspaces/<agent_id>/
  SOUL.md
  AGENTS.md
  MEMORY.md
  TOOLS.md
  skills/
    <skill_name>/
      SKILL.md
      skill.py
      manifest.json
  tools/
    <tool_name>/
      tool.py
      manifest.json
  agents/
    <subagent_name>/
      SOUL.md
      AGENTS.md
      MEMORY.md
      TOOLS.md
      config.json
  runs/
  patches/
  tests/
```

### Reglas funcionales

* `SOUL.md`: identidad, estilo, principios, límites.
* `AGENTS.md`: catálogo y roles de agentes/subagentes.
* `MEMORY.md`: memoria persistente resumida y decisiones estables.
* `TOOLS.md`: inventario de tools disponibles, contratos y restricciones.
* `skills/`: capacidades reutilizables.
* `tools/`: herramientas ejecutables registrables por el runtime.
* `patches/`: cambios propuestos/aplicados por automejora.

---

## 7. Modelo de datos mínimo

## 7.1 Entidades principales

### User

* id
* email/login
* role
* created_at
* last_login_at

### Agent

* id
* name
* slug
* status
* workspace_path
* model_name
* oauth_profile_id
* parent_agent_id nullable
* created_at
* updated_at

### Session

* id
* agent_id
* channel_type (`web`, `matrix`)
* external_chat_id
* external_user_id
* status
* created_at
* updated_at

### Message

* id
* session_id
* role (`user`, `assistant`, `system`, `tool`)
* content
* metadata_json
* token_count_nullable
* created_at

### Run

* id
* agent_id
* session_id nullable
* trigger_type (`message`, `cron`, `heartbeat`, `internal`)
* status
* started_at
* finished_at
* duration_ms
* input_tokens
* output_tokens
* estimated_cost
* error_summary nullable

### ToolExecution

* id
* run_id
* agent_id
* tool_name
* input_json
* output_json
* status
* started_at
* finished_at

### Skill

* id
* agent_id
* name
* version
* path
* enabled
* source (`builtin`, `generated`, `manual`)
* manifest_json

### PatchProposal

* id
* agent_id
* title
* reason
* diff_text
* target_type (`tool`, `skill`, `core`, `memory`, `config`)
* status (`draft`, `pending_review`, `approved`, `applied`, `rejected`, `rolled_back`)
* test_result_json
* created_at
* applied_at nullable

### ScheduledTask

* id
* agent_id
* type (`cron`, `heartbeat`, `one_shot`)
* schedule_expr
* timezone
* payload_json
* enabled
* last_run_at nullable
* next_run_at nullable

### OAuthProfile

* id
* provider (`openai_codex`)
* account_label
* encrypted_tokens
* expires_at
* refresh_status

---

## 8. Requisitos funcionales

## 8.1 Gateway web

### Chat web

* Permitir conversación con el agente.
* Mostrar respuesta en streaming.
* Mostrar estado de ejecución: pensando, usando tool, ejecutando skill, error, completado.
* Permitir reiniciar sesión.
* Permitir seleccionar agente.

### Dashboard de control

* Ver agentes activos.
* Ver sesiones activas.
* Ver jobs programados.
* Ver skills instaladas.
* Ver tools disponibles.
* Ver consumo de tokens y coste estimado.
* Ver historial de ejecuciones.
* Ver errores recientes.

### Configuración

* Editar parámetros del agente:

  * modelo,
  * temperatura si aplica,
  * límites de herramientas,
  * políticas de automejora,
  * permisos de creación de subagentes,
  * frecuencia de heartbeat.

### Observabilidad

* Dashboard con:

  * ejecuciones por día,
  * tiempo medio de respuesta,
  * errores por tipo,
  * uso por agente,
  * uso por canal,
  * uso por skill/tool,
  * consumo de suscripción.

---

## 8.2 Canal Matrix

* Conexión persistente al homeserver configurado.
* Soporte para mensajes directos y salas permitidas.
* Mapeo de room/user a sesión.
* Respuesta del agente en la misma conversación.
* Reintentos ante fallos transitorios.
* Gestión básica de medios opcional, al menos preparada para futuro.
* Lista blanca de usuarios/salas para seguridad.
* Posibilidad de política de grupos:

  * responder siempre,
  * responder solo con mención,
  * responder solo en allowlist.

---

## 8.3 Runtime del agente

* Construir contexto con:

  * system prompt base,
  * `SOUL.md`,
  * `AGENTS.md`,
  * `MEMORY.md`,
  * `TOOLS.md`,
  * skills activas,
  * historial reciente,
  * resumen de contexto largo.
* Soportar herramientas síncronas y asíncronas.
* Soportar tool-calling estructurado.
* Mantener sesiones por canal o unificadas según configuración.
* Permitir subagentes con rol especializado.
* Soportar trazabilidad completa por run.

---

## 8.4 Memoria

### Memoria persistente por ficheros

* El sistema debe leer y actualizar:

  * `SOUL.md`
  * `AGENTS.md`
  * `MEMORY.md`
  * `TOOLS.md`

### Reglas

* `SOUL.md` cambia muy raramente.
* `MEMORY.md` se actualiza mediante consolidación controlada.
* `AGENTS.md` se actualiza cuando se crean/modifican agentes.
* `TOOLS.md` se actualiza cuando se registran nuevas tools.

### Consolidación

* Debe existir un proceso periódico de consolidación de memoria.
* Debe resumir historial en memoria de largo plazo.
* Debe evitar duplicidades y contradicciones.
* Debe dejar snapshot o historial de cambios.

---

## 8.5 Skills y tools

### Skills

Una skill es una capacidad compuesta con:

* descripción en `SKILL.md`,
* manifiesto,
* opcionalmente código Python.

### Tools

Una tool es una unidad ejecutable formal con:

* nombre,
* descripción,
* esquema de entrada,
* esquema de salida,
* permisos,
* timeout,
* política de sandbox.

### Requisitos

* Descubrimiento automático de skills/tools del workspace.
* Registro dinámico sin reinicio completo si es posible.
* Activación/desactivación desde dashboard.
* Validación de manifiestos.
* Versionado básico.

---

## 8.6 Multiagente

### Requisitos

* El sistema debe soportar múltiples agentes con workspaces separados o heredados.
* Debe existir un **agente principal** y subagentes especializados.
* El agente principal podrá:

  * proponer un nuevo subagente,
  * crear su estructura base,
  * asignarle rol y herramientas,
  * delegarle una tarea,
  * recoger el resultado.

### Casos de uso

* Agente programador.
* Agente analista.
* Agente de memoria.
* Agente de monitorización.
* Agente de búsqueda/documentación.

### Restricciones

* La creación automática de agentes debe pasar por política configurable:

  * manual,
  * semiautomática,
  * automática con límites.

---

## 8.7 Heartbeat y cron

### Heartbeat

* Ejecutarse periódicamente, por ejemplo cada 5, 15 o 30 minutos.
* Revisar estado del sistema.
* Disparar tareas internas de mantenimiento:

  * consolidación de memoria,
  * limpieza de colas,
  * revisión de errores,
  * detección de carencias,
  * propuesta de nuevas skills o subagentes.

### Cron

* Permitir programar tareas recurrentes.
* Persistir tareas programadas en BD.
* Mostrar próximas ejecuciones en dashboard.
* Ejecutar aunque el usuario no interactúe.
* Reintentar con política configurable.

---

## 8.8 Automejora

Esta es la capacidad principal del sistema y debe implementarse con mucho control.

### Flujo mínimo

1. El agente detecta una carencia o mejora.
2. Genera una propuesta estructurada.
3. Produce diff y descripción.
4. Ejecuta validaciones automáticas.
5. Presenta la propuesta en dashboard.
6. Según política:

   * queda pendiente de aprobación, o
   * se aplica automáticamente si cumple límites.
7. Se registra snapshot/versión previa.
8. Puede hacerse rollback.

### Tipos de automejora permitidos en MVP

* Crear una nueva skill.
* Crear una nueva tool del workspace.
* Modificar `MEMORY.md`.
* Modificar `TOOLS.md`.
* Añadir un subagente.

### Tipos de automejora restringidos en MVP

* Modificar el core del runtime.
* Modificar autenticación.
* Modificar políticas de seguridad.
* Ejecutar migraciones no revisadas.

### Requisitos de seguridad

* Todo cambio debe quedar auditado.
* Todo cambio de código debe poder revertirse.
* Toda generación de código debe pasar tests básicos.
* Debe existir allowlist de rutas modificables.
* Debe existir límite de frecuencia de automejora.

---

## 9. Requisitos no funcionales

## 9.1 Seguridad

* Autenticación obligatoria en dashboard.
* Roles mínimos: `admin`, `viewer`.
* Cifrado de tokens OAuth en base de datos.
* Lista blanca para Matrix.
* Validación de entradas de tools.
* Aislamiento de ejecución de código.
* Restricción de rutas de lectura/escritura al workspace.
* Protección CSRF si hay formularios de panel.
* Rate limit en APIs sensibles.
* Auditoría completa de cambios y ejecuciones.

## 9.2 Rendimiento

* Respuesta de chat web inicial < 2 s ideal para streaming.
* Soporte de múltiples sesiones concurrentes.
* Jobs largos desacoplados del request HTTP.
* Cache de contexto resumido por sesión.

## 9.3 Fiabilidad

* Reconexión automática a Matrix.
* Recuperación de scheduler tras reinicio.
* Persistencia de jobs pendientes.
* Idempotencia en tareas críticas.

## 9.4 Mantenibilidad

* Arquitectura modular.
* Tipado y validación.
* Tests automáticos.
* Documentación técnica mínima.
* Logs estructurados JSON.

## 9.5 Observabilidad

* Logs por módulo.
* Métricas Prometheus o equivalentes.
* Trazabilidad por `run_id`.
* Salud del sistema con endpoint `/health`.

---

## 10. Requisitos de seguridad específicos para automejora

El sistema no debe tener permiso ilimitado para editarse a sí mismo.

### Política recomendada

#### Nivel 1 — permitido automáticamente

* editar `MEMORY.md`
* crear skill nueva en `skills/`
* crear tool nueva en `tools/`
* editar manifiestos del workspace

#### Nivel 2 — requiere aprobación

* modificar archivos Python de skills existentes
* crear subagentes
* modificar `AGENTS.md`
* modificar `TOOLS.md`

#### Nivel 3 — prohibido en MVP

* modificar core app Flask
* modificar capa OAuth
* modificar base de datos/migraciones
* modificar sandbox/políticas de seguridad

### Requisitos técnicos

* snapshots por cambio,
* diff visible,
* rollback de un clic,
* tests automáticos mínimos,
* sandbox para generación/ejecución,
* control de permisos por ruta.

---

## 11. API mínima requerida

## 11.1 Auth

* `POST /api/auth/login`
* `POST /api/auth/logout`
* `GET /api/auth/me`

## 11.2 Agents

* `GET /api/agents`
* `POST /api/agents`
* `GET /api/agents/{id}`
* `PATCH /api/agents/{id}`
* `POST /api/agents/{id}/start`
* `POST /api/agents/{id}/stop`

## 11.3 Chat / sessions

* `GET /api/sessions`
* `GET /api/sessions/{id}`
* `GET /api/sessions/{id}/messages`
* `POST /api/sessions/{id}/message`
* `POST /api/chat`

## 11.4 Skills

* `GET /api/skills`
* `POST /api/skills`
* `PATCH /api/skills/{id}`
* `POST /api/skills/{id}/reload`

## 11.5 Tools

* `GET /api/tools`
* `POST /api/tools`
* `PATCH /api/tools/{id}`
* `POST /api/tools/{id}/test`

## 11.6 Scheduler

* `GET /api/tasks`
* `POST /api/tasks`
* `PATCH /api/tasks/{id}`
* `DELETE /api/tasks/{id}`
* `POST /api/tasks/{id}/run`

## 11.7 Runs / observabilidad

* `GET /api/runs`
* `GET /api/runs/{id}`
* `GET /api/metrics/summary`
* `GET /api/metrics/tokens`
* `GET /api/errors/recent`

## 11.8 Self-improvement

* `GET /api/patches`
* `GET /api/patches/{id}`
* `POST /api/patches/{id}/approve`
* `POST /api/patches/{id}/reject`
* `POST /api/patches/{id}/apply`
* `POST /api/patches/{id}/rollback`

## 11.9 OAuth Codex

* `GET /api/oauth/openai/start`
* `GET /api/oauth/openai/callback`
* `POST /api/oauth/openai/refresh`
* `GET /api/oauth/profiles`

---

## 12. Dashboard mínimo

## 12.1 Pantallas

1. **Overview**

   * estado general,
   * agentes activos,
   * jobs en cola,
   * errores recientes,
   * consumo.

2. **Chat**

   * selector de agente,
   * historial,
   * streaming,
   * eventos de tool.

3. **Agents**

   * listado,
   * crear/editar,
   * activar/desactivar,
   * ver subagentes.

4. **Skills & Tools**

   * listado,
   * estado,
   * versión,
   * recarga,
   * logs de validación.

5. **Memory / Workspace**

   * visor/editor de `SOUL.md`, `AGENTS.md`, `MEMORY.md`, `TOOLS.md`,
   * historial de cambios.

6. **Scheduler**

   * tareas heartbeat,
   * cron jobs,
   * próximas ejecuciones,
   * logs.

7. **Runs / Metrics**

   * tabla de ejecuciones,
   * duración,
   * tokens,
   * coste,
   * errores.

8. **Self-Improvement**

   * propuestas,
   * diffs,
   * tests,
   * aprobar/aplicar/rollback.

---

## 13. Integración con OpenAI Codex OAuth

## Requisitos

* El sistema debe soportar login OAuth del propietario.
* Debe guardar sesión de forma segura.
* Debe renovar tokens si aplica.
* Debe permitir asociar un perfil OAuth a uno o varios agentes.
* Debe exponer en dashboard:

  * estado de autenticación,
  * cuenta conectada,
  * expiración,
  * reautenticación.

## Restricción inicial

* Solo se soporta **OpenAI Codex vía OAuth**.
* No se incluirán API keys manuales en el MVP salvo que se decida explícitamente.

---

## 14. Flujo operativo principal

1. Usuario escribe por web o Matrix.
2. Channel adapter/gateway normaliza el mensaje.
3. Se resuelve sesión y agente.
4. Runtime construye contexto.
5. Modelo responde con texto o tool calls.
6. Runtime ejecuta tools/skills/subagentes.
7. Se emite streaming al canal.
8. Se persisten mensajes, run, métricas y eventos.
9. Si detecta necesidad estructural, genera propuesta de mejora.

---

## 15. Flujo de automejora recomendado

1. El agente detecta: “necesito una tool para X”.
2. Crea una **PatchProposal**.
3. Genera:

   * `manifest.json`
   * `tool.py` o `skill.py`
   * actualización de `TOOLS.md` o `AGENTS.md`
   * actualiza github
4. Ejecuta tests locales:

   * import del módulo,
   * validación de manifiesto,
   * test básico de ejecución.
5. Muestra diff y resultado en dashboard.
6. Admin aprueba.
7. Sistema aplica y recarga registro.
8. Queda disponible para siguientes runs.

---

## 16. Testing mínimo exigido

## 16.1 Unit tests

* parser de workspace,
* registro de skills,
* registro de tools,
* scheduler,
* política de permisos,
* servicio OAuth,
* session manager,
* métricas.

## 16.2 Integration tests

* mensaje web → run → respuesta,
* mensaje Matrix → run → respuesta,
* creación de skill → recarga → uso,
* cron job → ejecución → persistencia,
* patch aprobado → aplicado → rollback.

## 16.3 Security tests

* acceso no autorizado a dashboard,
* acceso fuera de workspace,
* tool con input malicioso,
* intento de modificar core bloqueado.

---

## 17. DevOps y despliegue

## Requisitos

* Dockerfile para despliegue.
* docker-compose para entorno local.
* Variables sensibles por entorno.
* Migraciones automáticas de BD.
* Logs a stdout.
* Endpoint healthcheck.
* Reinicio seguro del worker y gateway.

## Entornos

* local
* staging
* producción

---

## 18. Roadmap de implementación recomendado

## Fase 1 — Núcleo funcional

* Flask app base
* PostgreSQL + SQLAlchemy
* Auth admin básica
* Chat web
* Agent runtime simple
* Lectura de `SOUL.md`, `AGENTS.md`, `MEMORY.md`, `TOOLS.md`
* OpenAI Codex OAuth

## Fase 2 — Canales y scheduler

* Integración Matrix
* Scheduler heartbeat + cron
* Persistencia completa de runs y métricas
* Dashboard de ejecuciones

## Fase 3 — Skills y tools

* Registro dinámico
* Carga desde workspace
* Panel de skills/tools
* Validación y recarga

## Fase 4 — Multiagente

* Subagentes
* Delegación de tareas
* Panel de agentes y topología

## Fase 5 — Automejora controlada

* Patch proposals
* Diff
* Tests automáticos
* Aprobación y rollback

## Fase 6 — Hardening

* sandbox por ejecución
* observabilidad avanzada
* límites de seguridad finos
* optimización de costes y contexto

---

## 19. Decisiones importantes que recomiendo fijar ya

1. **PostgreSQL + Redis** desde el inicio.
2. **Flask + HTMX** para ir rápido en dashboard.
3. **matrix-nio** para Matrix.
4. **Celery** si prevés jobs serios; APScheduler solo si quieres MVP muy rápido.
5. **Docker sandbox** como objetivo, aunque el primer prototipo use subprocess restringido.
6. **Automejora con aprobación humana por defecto**.
7. **Workspace versionado con Git local** o snapshots equivalentes.
8. **Una política estricta de rutas editables**.

---

## 20. Resumen ejecutivo para el desarrollador

Se debe desarrollar una plataforma de agente IA en Flask, con gateway web y canal Matrix, basada en workspaces persistentes por ficheros (`SOUL.md`, `AGENTS.md`, `MEMORY.md`, `TOOLS.md`), con soporte para skills, tools, cron, heartbeat, multiagente y un sistema de automejora controlado, auditable y reversible. El sistema debe operar inicialmente con OpenAI Codex vía OAuth, disponer de dashboard administrativo completo y registrar métricas, consumos y trazas por ejecución.

---

## 21. Referencias conceptuales tomadas como inspiración

* OpenClaw separa el Gateway como plano de control y usa workspace con `AGENTS.md`, `SOUL.md`, `TOOLS.md`, skills y coordinación entre agentes/sesiones. fileciteturn1file7
* OpenClaw trata el Gateway como superficie web/control y la seguridad de tools/sandboxes como parte central del diseño. fileciteturn1file8 fileciteturn1file9
* nanobot ya demuestra viabilidad de Matrix, cron/heartbeat, memoria persistente, skills y login con OpenAI Codex por OAuth. fileciteturn1file0 fileciteturn1file3 fileciteturn1file4
