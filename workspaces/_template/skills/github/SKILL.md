# GitHub

Interactúa con GitHub para crear issues y pull requests sobre errores o mejoras del código del agente. Usa preferentemente `gh` CLI cuando esté disponible y fallback por REST API con credencial `github_token` o `github`.

# GitHub Skill

Usa esta skill cuando el usuario quiera registrar errores, mejoras técnicas, deuda técnica o cambios propuestos en GitHub como **issues** o **pull requests** relacionados con el código del agente/workspace.

## Repositorio por defecto

El repositorio principal de este agente/workspace es:

```text
https://github.com/lliwi/autobot
```

En comandos y llamadas usa por defecto:

```text
lliwi/autobot
```

Si el usuario no indica otro repositorio, crea issues y PRs en `lliwi/autobot`. Solo pide repositorio si la petición se refiere claramente a otro proyecto o si hay ambigüedad real.

## Objetivo principal

- Crear issues claros y accionables sobre bugs/mejoras detectadas en skills, tools o configuración del workspace.
- Crear pull requests cuando exista una rama con cambios ya preparados en un repositorio Git.
- Consultar issues, PRs, checks y workflow runs para verificar estado.

## Requisitos

### Preferido: GitHub CLI

Si `gh` está instalado y autenticado:

```bash
gh auth status
```

Usa siempre `--repo owner/repo` cuando no estés dentro de un repositorio Git. Para este workspace, el valor por defecto es:

```bash
--repo lliwi/autobot
```

### Fallback: API REST

Si `gh` no está instalado, usa la GitHub REST API con una credencial almacenada:

1. primero intenta `github_token`;
2. si no existe, intenta `github`.

La credencial debe ser un token con permisos adecuados:

- Issues: `issues:write` o equivalente.
- Pull requests: permisos de contenido/PR según el tipo de repo.

Nunca imprimas ni guardes el token.

## Entradas recomendadas

Para crear issue:

- `repo`: opcional; por defecto `lliwi/autobot`.
- `title`: título breve y específico.
- `body`: descripción completa.
- `labels`: opcional, por ejemplo `bug`, `enhancement`, `technical-debt`, `agent`, `skill`, `tool`.

Para crear PR:

- `repo`: opcional; por defecto `lliwi/autobot`.
- `head`: rama origen.
- `base`: rama destino, normalmente `main` o `master`.
- `title`: título del PR.
- `body`: resumen, cambios, pruebas y riesgos.

## Flujo para crear issues sobre errores/mejoras del agente

1. Identifica el componente afectado:
   - skill: `skills/<slug>/...`
   - tool: `tools/<slug>/...`
   - documentación: `AGENTS.md`, `TOOLS.md`, `MEMORY.md`, etc.
2. Resume evidencia:
   - error exacto;
   - comandos o tools llamadas;
   - resultado esperado;
   - resultado real;
   - archivos afectados;
   - propuesta de solución.
3. Crea el issue en `lliwi/autobot`, salvo que el usuario indique otro repositorio.
4. Devuelve al usuario el número y URL del issue.

## Plantilla de issue recomendada

```markdown
## Contexto

Qué se intentaba hacer y por qué.

## Resultado actual

Qué falla o qué comportamiento es mejorable.

## Resultado esperado

Cómo debería comportarse.

## Evidencia

- Componente: `skills/...` o `tools/...`
- Error/log relevante: `...`
- Pasos de reproducción:
  1. ...
  2. ...

## Propuesta

Cambio sugerido.

## Riesgos / notas

Compatibilidad, credenciales, seguridad o límites conocidos.
```

## Crear issue con gh

```bash
gh issue create \
  --repo lliwi/autobot \
  --title "Fix credential access in youtube-summary-publisher" \
  --body-file /tmp/issue.md \
  --label bug \
  --label agent
```

Listar issues:

```bash
gh issue list --repo lliwi/autobot --json number,title,state,url --jq '.[] | "#\(.number) [\(.state)] \(.title) - \(.url)"'
```

Ver issue:

```bash
gh issue view 123 --repo lliwi/autobot --json number,title,body,state,labels,url
```

## Crear issue con REST API fallback

Endpoint:

```text
POST https://api.github.com/repos/lliwi/autobot/issues
```

Payload:

```json
{
  "title": "Título",
  "body": "Markdown del issue",
  "labels": ["bug", "agent"]
}
```

Cabeceras:

```text
Authorization: Bearer <token>
Accept: application/vnd.github+json
X-GitHub-Api-Version: 2022-11-28
```

## Pull Requests

### Crear PR con gh

```bash
gh pr create \
  --repo lliwi/autobot \
  --base main \
  --head feature/fix-youtube-summary-publisher \
  --title "Fix YouTube summary publisher credential resolution" \
  --body-file /tmp/pr.md
```

### Ver PR

```bash
gh pr view 55 --repo lliwi/autobot --json number,title,state,url,headRefName,baseRefName,mergeable
```

### Checks de CI

```bash
gh pr checks 55 --repo lliwi/autobot
```

### Workflow runs

```bash
gh run list --repo lliwi/autobot --limit 10
```

```bash
gh run view <run-id> --repo lliwi/autobot --log-failed
```

## Crear PR con REST API fallback

Endpoint:

```text
POST https://api.github.com/repos/lliwi/autobot/pulls
```

Payload:

```json
{
  "title": "Título del PR",
  "head": "feature/my-branch",
  "base": "main",
  "body": "Markdown del PR"
}
```

Importante: el fallback REST solo crea el PR si la rama ya existe en GitHub. No crea commits ni sube ramas.

## Buenas prácticas

- Usar `lliwi/autobot` como repo por defecto.
- No crear issues duplicados: buscar antes por palabras clave si el usuario no pide creación directa.
- No incluir secretos, tokens, URLs privadas sensibles ni outputs completos con credenciales.
- Para errores de credenciales, describir el síntoma sin exponer valores.
- Usar títulos concretos:
  - Bueno: `Fix Notion credential injection in youtube-summary-publisher`
  - Malo: `Bug`
- Añadir etiquetas cuando existan; si GitHub rechaza labels inexistentes, reintentar sin labels.
- Si el usuario indica explícitamente otro repositorio, respetarlo.

## Estado de este workspace

En la comprobación inicial, `gh` no está instalado en PATH. Para uso inmediato, preferir fallback REST API si existe credencial `github_token` o `github`; alternativamente solicitar instalación/configuración de `gh` y autenticación.
