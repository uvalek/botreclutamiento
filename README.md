# Bot de reclutamiento (demo Adlek)

Chatbot guiado para reclutar personal operativo. Es una demo para el **Foro Automotriz Tlaxcala 2026** con una empresa ficticia, *Componentes del Centro*.

El candidato escribe al WhatsApp demo (+52 241 279 0966). Chatwoot le pasa cada mensaje a este bot como **Agent Bot**, y el bot responde por la API de Chatwoot. Nunca usa la Graph API de Meta. Recursos Humanos ve el resultado en Chatwoot (etiquetas, atributos y una nota privada).

```
Candidato (WhatsApp) → Meta → Chatwoot (bandeja WhatsApp Demos)
   → POST /webhooks/chatwoot (este bot) → responde con la API de Chatwoot
   → Chatwoot → Meta → Candidato
```

**v2 (rama `v2`):** arquitectura de la plantilla sobre el guion.

```
Buffer de entrada → Policía (seguridad) → Router/Intérprete (IA) → Guion (máquina de estados)
   → M1 Preguntas (RAG en Supabase) · M3 Agendamiento (Cal.com) · M4 Seguimiento (temporizadores)
   → M2 Redactor (IA): 1–3 burbujas naturales, con botones en la última
```

- El **guion** sigue siendo la autoridad: qué dato falta, validación, clasificación, registro en Chatwoot. La IA entiende texto libre (varios datos en un mensaje, notas de voz) y hace que suene humano.
- **Red de seguridad:** si la IA falla, tarda o cambia un dato, se usan los textos fijos. Sin `OPENAI_API_KEY` el bot funciona igual que la v1.
- **Supabase (proyecto demos):** memoria en `n8n_chat_histories`, RAG en `documents` (metadata `demo=reclutamiento`), candidatos en `contactos` (un renglón por prospecto).
- **Cal.com:** solo se ofrecen horarios libres del tipo de evento; al confirmar se reserva. Si Cal.com no responde, se usan los horarios fijos de `INTERVIEW_SLOTS`.

**v1 (sin IA):** un flujo guiado con un intérprete de respuestas (números, palabras, sinónimos y errores de dedo). Las respuestas salen de textos fijos en `app/vacante.py`, así que **el bot no puede inventar datos**.

## Flujo

Bienvenida (con aviso de demo) → nombre → edad → municipio → turno → documentos → experiencia → clasificación → horario → confirmación → recordatorio simulado (2 min) → respuesta 1 o 2.

**Clasificación:**
- **Revisar:** si le faltan documentos o su municipio no tiene ruta de transporte. Aun así puede agendar.
- **Califica:** en cualquier otro caso.

**Comandos y casos especiales:**

| Qué pasa | Qué hace el bot |
|---|---|
| Escribe **asesor** | Pasa la conversación a un humano (estado *abierta*) y se calla |
| Escribe **reiniciar** | Deja una nota con el resumen anterior, limpia etiquetas y atributos y empieza de nuevo. Funciona siempre |
| Manda audio, foto o sticker | "Por ahora solo puedo leer texto" y repite la pregunta |
| Pregunta sueldo, transporte, ubicación, turnos o qué llevar | Responde con los datos de la vacante y regresa al paso |
| No se le entiende 2 veces | Le ofrece hablar con un asesor |
| Deja de responder 3 min a medio flujo | Le manda un seguimiento y pone la etiqueta `abandonó` |

## Estructura

| Archivo | Qué hace |
|---|---|
| `app/vacante.py` | **Todos los textos** y los datos de la vacante (única fuente de verdad) |
| `app/flow/engine.py` | Máquina de estados pura: (sesión, mensaje) → acciones |
| `app/flow/matcher.py` | Interpreta "1", "matutino", "en la mañana", "matuino"… |
| `app/flow/faq.py` | Detecta preguntas frecuentes y devuelve la respuesta fija |
| `app/flow/rules.py` | Clasificación y nota de resumen para RH |
| `app/processor.py` | Filtra los eventos de Chatwoot, descarta repetidos y tiene un candado por conversación y el reloj de temporizadores |
| `app/executor.py` | Convierte las acciones en llamadas a Chatwoot |
| `app/chatwoot.py` | Cliente de la API de Chatwoot (botones y listas vía `input_select`) |
| `app/store.py` | SQLite con sesiones, mensajes procesados y temporizadores |
| `scripts/simular.py` | Para platicar con el bot en la terminal |
| `scripts/chatwoot_setup.rb` | Configura etiquetas, atributos y el Agent Bot en Chatwoot |

## Probar en tu computadora

```bash
uv venv --python 3.11 .venv
```

```bash
uv pip install --python .venv/bin/python -e ".[dev]"
```

```bash
.venv/bin/python -m pytest -q
```

```bash
.venv/bin/python scripts/simular.py
```

En el simulador:
- `/recordatorio` dispara el recordatorio.
- `/seguimiento` dispara el mensaje por abandono.
- `/foto` simula una foto.
- `/estado` muestra los datos guardados.

## Despliegue en EasyPanel (app nueva, separada de la plantilla)

1. **Crear la app:** *Create project* `reclutamiento` → *+ Service → App* `bot`.
2. **Fuente:** GitHub `uvalek/botreclutamiento`, rama `main`, *Build: Dockerfile*.
3. **Volumen:** *Mounts* → Volume `data` → ruta `/data`, para que las sesiones y temporizadores sobrevivan a los redeploys.
4. **Réplicas:** **1**, porque el reloj de temporizadores vive dentro del proceso.
5. **Dominio:** el automático de EasyPanel, puerto **8000**, con HTTPS.
6. **Variables:** en *Environment*, las de `.env.example`.
7. **Deploy:** revisa `https://<dominio>/health` y `https://<dominio>/version`.

> Chatwoot bloquea URLs de red interna. El bot **debe** usar su dominio público HTTPS.

## Conectar con Chatwoot

Los comandos se corren desde tu Mac y usan el alias `vps-adlek`:

```bash
ssh vps-adlek 'C=$(docker ps -q -f name=chatwoot_chatwoot\.1 | head -1); docker exec -i -e STEP=revisar $C bundle exec rails runner -' < scripts/chatwoot_setup.rb
```

1. **`STEP=preparar`** (con `-e BOT_URL=https://<dominio>/webhooks/chatwoot`): crea las 7 etiquetas, los 11 atributos de conversación y el Agent Bot. **Todavía no lo conecta** a la bandeja.
2. **Copiar token y secreto:** en Chatwoot, *Ajustes → Bots → Reclutamiento Demo*, copia el **Token de acceso** y el **Webhook Secret**. Pégalos en EasyPanel como `CHATWOOT_BOT_TOKEN` y `CHATWOOT_WEBHOOK_SECRET` y haz *Deploy*.
3. **`STEP=conectar`:** resuelve las conversaciones abiertas de WhatsApp Demos y le asigna el bot. Resolverlas es necesario porque en una conversación abierta el bot no contesta: la considera en manos de un humano.
4. **Carpeta "Candidatos"** en Chatwoot: filtro etiqueta = `reclutamiento`, todos los estados. Las conversaciones que atiende el bot quedan en **Pendientes**, no en Abiertas.

**Botón de pánico:** `STEP=desconectar`, o *Ajustes → Entradas → WhatsApp Demos → Bot → ninguno*. La bandeja vuelve a ser solo humana.

## Checklist antes de la demo

**Recorrido completo:**
- Botones, lista y negritas se ven bien en WhatsApp.
- En Chatwoot aparecen las etiquetas, los atributos y la nota.
- El recordatorio llega a los 2 minutos; probar la respuesta 1 y la 2.

**Casos especiales:**
- El seguimiento llega a los 3 minutos sin responder.
- Con "asesor" la conversación queda abierta y el bot se calla.
- Con "reiniciar" empieza de cero.
- Una foto y un audio.
- Una pregunta frecuente a mitad del flujo.

**Antes de ir al foro:** correr el checklist 2 veces seguidas, con "reiniciar" entre una y otra, y revisar los logs de EasyPanel.

## Variables

Todas se explican en `.env.example`. Las que se pueden ajustar para la demo sin tocar código:
- `INTERVIEW_SLOTS`: los horarios de entrevista.
- `REMINDER_DELAY_SECONDS`: cuándo llega el recordatorio.
- `FOLLOWUP_DELAY_SECONDS`: cuándo llega el seguimiento por abandono.
- `DEMO_MODE`: muestra u oculta los textos "(en esta demo…)".
