# Notion Pages Reader/Publisher

Interactúa con la API de Notion usando la credencial `notion` para listar páginas y crear páginas o subpáginas.

# Notion Pages Reader/Publisher

Skill para interactuar con la API de Notion usando una API key almacenada como credencial `notion`.

## Capacidades
- Listar páginas a las que el token tiene acceso (vía `/v1/search`).
- Crear una página nueva en una base de datos (`/v1/pages`).
- Crear una subpágina bajo una página padre (`/v1/pages` con `parent.page_id`).
- Generar y actualizar documentos con estructura clara, escaneable y visualmente agradable usando bloques de Notion.

## Entradas
- `action` (requerida): `list_pages`, `create_page` o `create_subpage`.
- `query` (opcional, para `list_pages`): texto para filtrar resultados.
- `page_size` (opcional, para `list_pages`): máximo de resultados a pedir; por defecto `50`.
- `database_id` (requerida para `create_page`): id de la base de datos destino.
- `parent_page_id` (requerida para `create_subpage`): id de la página padre.
- `title` (requerida para `create_page` y `create_subpage`): título de la página.
- `content` (opcional): contenido en párrafos simples.

## Salida
- Para `list_pages`: lista con `id`, `title`, `url`, `last_edited_time`.
- Para `create_page`: objeto con `id`, `url`, `created_time`.
- Para `create_subpage`: objeto con `id`, `url`, `created_time`, `title`.

## Ejemplos
- `notion-pages(action="list_pages")`
- `notion-pages(action="list_pages", query="Roadmap")`
- `notion-pages(action="create_page", database_id="...", title="Nota rápida", content="Hola")`
- `notion-pages(action="create_subpage", parent_page_id="...", title="skills", content="Documentación...")`

## Notas
- Requiere credencial `notion` de tipo token.
- Usa `Notion-Version: 2022-06-28`.
- La creación en bases de datos asume una propiedad de título llamada `title`; algunos databases pueden requerir adaptación adicional.

---

# 🧩 Notion Skill — High Quality Document Creator

## 🎯 Purpose

This skill enables the agent to create, update, and format Notion documents in a **clear, structured, and visually pleasant way**, optimized for human readability and fast scanning.

---

## 🧠 Mental Model (VERY IMPORTANT)

Notion is NOT a text editor. It is a **block-based structured system**.

Always:

- Think in blocks, not paragraphs
- Prefer hierarchy over long text
- Optimize for scanability (humans skim, not read)

Golden rules:

- One idea = one block
- Use headings every 3–5 blocks
- Avoid long paragraphs (>4 lines)
- Prefer bullets over prose

---

## 🏗️ Default Document Structure (MANDATORY)

Every document MUST follow this structure:

1. **Title (H1)**
2. **Short summary (2–3 lines)**
3. **Key points (bullet list)**
4. **Main content (sections with H2/H3)**
5. **Examples or use cases**
6. **Action items / next steps**

---

## 🎨 Visual Formatting Rules

Always include:

- At least **1 callout block** for important info
- At least **1 toggle block** for optional/advanced detail
- Bullet lists instead of long paragraphs
- **Bold for key terms** (not entire sentences)

Use when appropriate:

- Tables → for comparisons
- Code blocks → for commands/config
- Numbered lists → for step-by-step processes

---

## 🧱 Block Mapping (Intent → Notion Block)

- Explanation → paragraphs + bullet list
- Steps → numbered list
- Warnings → callout (⚠️)
- Tips → callout (💡)
- Advanced detail → toggle
- Comparison → table
- Config/code → code block

---

## 📐 Style Conventions

- Emojis ONLY inside callouts
- H2 for sections, H3 for subsections
- Max 6 bullets per list
- Keep sections short and focused

---

## 🧪 Example Output (Reference)

# Deploying Flask API

**Summary**  
Quick guide to deploy a Flask API using Docker in a production-ready way.

**Key Points**

- Uses Docker
- Production-ready
- Easy to scale

---

## Setup

1. Install Docker
2. Create Dockerfile
3. Build image
4. Run container

💡 **Tip**  
Use slim images to reduce size and attack surface.

<toggle title="Advanced optimization">
Use multi-stage builds to reduce final image size and improve caching.
</toggle>

---

## Example Configuration

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install -r requirements.txt
CMD ["python", "app.py"]
```

---

## Next Steps

- Add CI/CD pipeline
- Configure monitoring
- Deploy to cloud

---

## ❌ Anti-Patterns (NEVER DO THIS)

- Do NOT write long walls of text
- Do NOT skip headings
- Do NOT put everything in paragraphs
- Do NOT repeat information
- Do NOT create empty sections

---

## 🧠 Pre-Generation Checklist

Before creating a document, ask:

- Is this scannable in 10 seconds?
- Are sections clearly separated?
- Are important ideas highlighted?
- Would a human enjoy reading this?

---

## 🔄 Refactor Mode (When Updating Documents)

If editing an existing document:

- Improve structure BEFORE adding content
- Break long paragraphs into bullet lists
- Add headings if missing
- Add callouts where useful
- Remove redundancy

---

## 📊 Quality Score (Self-Evaluation)

After generating a document, internally evaluate:

- Structure clarity (0–10)
- Readability (0–10)
- Visual organization (0–10)
- Use of components (0–10)

If score < 32 → Refactor before delivering.

---

## ⚡ Output Guidelines

- Always prioritize clarity over verbosity
- Prefer structured content over narrative text
- Keep formatting consistent
- Optimize for real human usage (not just correctness)

---

## 🚀 Goal

Transform any content into:

✅ Clear  
✅ Structured  
✅ Visually pleasant  
✅ Easy to scan and understand

---

End of skill.
