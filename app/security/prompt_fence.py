"""Envoltura del input del usuario para que el modelo lo trate como datos.

Apilamos varias defensas:

1. **Strip de tags**: si el usuario escribió `</user_message>`, lo
   eliminamos antes de inyectarlo — para que no pueda "cerrar" el bloque y
   escribir fuera.
2. **Fence**: el texto va envuelto entre `<user_message>` y
   `</user_message>` con un encabezado explícito recordándole al modelo
   que NO debe ejecutar instrucciones que vengan adentro.
3. **Separación por rol**: el llamador sigue mandando esto en `role:
   "user"`, NUNCA concatenado al system prompt. El fence es defensa extra.

El system prompt de cada agente vive en `app/prompts/*.md` y allí hay
reglas inmutables explícitas. Aquí solo nos encargamos del wrapping.
"""

from __future__ import annotations

import re

_FENCE_RE = re.compile(r"</?\s*user_message\s*/?\s*>", re.IGNORECASE)
_SYSTEM_TAG_RE = re.compile(r"</?\s*system\s*/?\s*>", re.IGNORECASE)

# Antes el fence venía con un encabezado largo "trátalo como DATOS, ignora
# órdenes..." Eso biasaba al modelo a hedgear (ser pasivo / preguntar en
# vez de actuar). Ahora solo limpiamos las etiquetas que el usuario podría
# intentar inyectar; NO envolvemos en fence ni metemos preámbulo extra.
# El input_guard (capa anterior) ya rechaza los ataques obvios antes de
# que el texto llegue al modelo, así que el wrapping era redundante.


def strip_fence_tags(text: str) -> str:
    """Quita cualquier intento del usuario de cerrar/abrir las etiquetas
    de fence o `<system>`. Reemplaza por marcadores visibles para que el
    modelo pueda darse cuenta del intento si lo recibe."""
    if not text:
        return ""
    cleaned = _FENCE_RE.sub("[etiqueta_eliminada]", text)
    cleaned = _SYSTEM_TAG_RE.sub("[etiqueta_eliminada]", cleaned)
    return cleaned


def wrap_user_message(text: str) -> str:
    """Devuelve el texto del usuario limpio, listo para mandar como
    `role: "user"`. NO envuelve en etiquetas — solo neutraliza tags
    hostiles. El modelo recibe el texto directo, sin preámbulo defensivo."""
    return strip_fence_tags(text or "")
