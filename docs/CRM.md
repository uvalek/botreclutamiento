# Conexión con el CRM de Adlek (rama `crm`)

> Desde el 15 nov 2026 el bot ya no vive dentro de Chatwoot: recibe y contesta a través del **CRM de Adlek** (`https://app.adlek.com.mx`), que es quien tiene conectado el WhatsApp **+52 241 279 0966**. La lógica del bot (guion, IA, RAG, Cal.com, Supabase, temporizadores) **no cambió**: solo cambió el "cable".

```
Paciente/candidato (WhatsApp) → Meta → CRM (app.adlek.com.mx)
   → POST /webhooks/crm (este bot, aviso firmado)
   → el bot contesta con la API del CRM (POST /api/v1/messages)
   → CRM → Meta → WhatsApp
```

## Variables de entorno (EasyPanel)

| Variable | Valor | Notas |
| --- | --- | --- |
| `CRM_API_KEY` | `wacrm_live_…` | Llave de API de la clínica (CRM → Configuración → Llaves de API). **Si está definida, el bot usa el CRM**; si no, usa Chatwoot como antes |
| `CRM_WEBHOOK_SECRET` | `whsec_…` | El `secret` que devolvió el CRM al registrar el webhook. Sin él, el endpoint no comprueba la firma (el log avisa) |
| `CRM_API_BASE` | `https://app.adlek.com.mx/api/v1` | Ya es el valor por defecto |
| `CRM_HANDOFF_TAG` | `requiere-humano` | Ya es el valor por defecto |
| `OPENAI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `CAL_API_KEY`, `CAL_EVENT_TYPE_ID` | igual que antes | |

`CHATWOOT_BOT_TOKEN` y `CHATWOOT_WEBHOOK_SECRET` ya no hacen falta con el CRM.

Permisos de la llave de API: `messages:send`, `messages:read`, `contacts:read`, `contacts:write`, `conversations:read` (y `webhooks:manage` solo para registrar el webhook).

## Registrar el webhook (una vez)

```bash
curl -X POST https://app.adlek.com.mx/api/v1/webhooks \
  -H "Authorization: Bearer $CRM_API_KEY" -H "Content-Type: application/json" \
  -d '{ "url": "https://DOMINIO-DEL-BOT/webhooks/crm", "events": ["message.received"] }'
```

La respuesta trae `secret` **una sola vez** → `CRM_WEBHOOK_SECRET`.

## Cómo se traduce cada acción del bot

| Acción del motor | Chatwoot | CRM |
| --- | --- | --- |
| `Send` / `SendOptions` | mensaje / `input_select` | `POST /messages` con `type: text` o `interactive` (botones ≤3, lista 4–10). Al tocar un botón, el CRM manda el **título** como texto, igual que antes |
| `Labels` | etiquetas de la conversación | **etiquetas del contacto** (`PATCH /contacts/{id}` con la lista completa) |
| `Handoff` ("asesor") | estado *open* | pone la etiqueta `requiere-humano` al contacto |
| `SetPending` | estado *pending* | quita la etiqueta `requiere-humano` |
| `Note` (nota privada) | nota privada | **no existe en la API del CRM todavía**: se escribe en el log (`crm_note_not_supported`) |
| `Attrs` (atributos) | atributos de la conversación | **no existe en la API del CRM todavía**: se escribe en el log (`crm_attributes_not_supported`) |

**¿Cuándo contesta el bot?** Solo si **nadie del equipo se asignó la conversación** en el CRM y el contacto **no** tiene la etiqueta `requiere-humano`. Para regresarle la conversación al bot: quitar la etiqueta y desasignarse. "reiniciar" funciona siempre.

**Notas de voz:** el aviso del CRM no trae la URL del audio; el bot la pide con `GET /conversations/{id}/messages` y la transcribe como antes. Si no hay URL pública, se trata como foto ("por ahora solo leo texto").

## Archivos

| Archivo | Qué hace |
| --- | --- |
| `app/crm.py` | Cliente del CRM con los mismos métodos que `ChatwootClient`, firma del webhook y utilidades |
| `app/processor.py` | `accept_crm()`: convierte el aviso del CRM en un mensaje entrante (pide teléfono y estado al CRM) |
| `app/main.py` | `POST /webhooks/crm` (comprueba `X-Wacrm-Signature`, responde de inmediato y procesa en segundo plano) |
| `app/store.py` | Tabla `crm_conversations`: UUID del CRM ⇄ entero del bot (+ contacto y teléfono) |
| `app/chatwoot.py` | `get_client()` elige CRM o Chatwoot según `CRM_API_KEY` |
| `tests/test_crm.py` | Pruebas del conector |

## Pendiente (para la siguiente sesión)

- Que el bot **mueva el pipeline** y **agende en la agenda del CRM** (hoy agenda en Cal.com). La API del CRM todavía no expone oportunidades ni citas.
- Notas privadas y campos personalizados del contacto en la API del CRM.
- Reintentos: el CRM manda cada aviso **una sola vez**; si el bot está caído en ese momento, ese mensaje no se contesta.
