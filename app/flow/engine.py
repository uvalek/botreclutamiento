"""Máquina de estados de la entrevista.

Función pura: recibe la sesión y lo que llegó (texto, medio o temporizador) y
devuelve la sesión nueva + una lista de ACCIONES (mandar texto, mandar
botones, poner etiquetas…). No llama a Chatwoot ni a nada externo; eso lo
hace `app/executor.py`. Así se puede probar la conversación completa sin
WhatsApp.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from app import vacante as V
from app.flow import faq, rules
from app.flow.matcher import (
    contains_any,
    first_name,
    is_asesor,
    is_greeting,
    is_reset,
    looks_like_sensitive_id,
    match_option,
    normalize,
    parse_name,
    sanitize,
)

# ---------------------------------------------------------------------------
# Acciones que produce el motor
# ---------------------------------------------------------------------------


@dataclass
class Send:
    text: str


@dataclass
class SendOptions:
    text: str
    options: list[str]


@dataclass
class Note:
    text: str


@dataclass
class Labels:
    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)
    clear_bot: bool = False  # quita todas las etiquetas que maneja el bot


@dataclass
class Attrs:
    values: dict[str, str]
    replace: bool = False  # True = borra los demás atributos


@dataclass
class Handoff:
    """Pasa la conversación a un humano (estado abierta)."""


@dataclass
class SetPending:
    """Regresa la conversación al bot (estado pendiente)."""


@dataclass
class Schedule:
    kind: str  # "recordatorio" | "seguimiento"
    delay_seconds: int


@dataclass
class CancelJobs:
    kinds: list[str] | None = None  # None = todos


Action = Send | SendOptions | Note | Labels | Attrs | Handoff | SetPending | Schedule | CancelJobs

RECORDATORIO = "recordatorio"
SEGUIMIENTO = "seguimiento"

# ---------------------------------------------------------------------------
# Sesión y configuración
# ---------------------------------------------------------------------------


@dataclass
class Session:
    state: str = "INICIO"
    data: dict[str, str] = field(default_factory=dict)
    fails: int = 0
    prev_state: str | None = None
    followup_state: str | None = None  # paso en el que ya se mandó seguimiento
    abandoned: bool = False
    reagenda: bool = False
    # v2: id del prospecto (cambia con "reiniciar"), horarios ofrecidos por
    # Cal.com ([{start, title}]), cuándo se consultaron y últimos botones.
    prospect: str = ""
    offered: list[dict[str, str]] = field(default_factory=list)
    offered_at: float = 0.0
    last_options: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Session:
        if not d:
            return cls()
        known = {k: d[k] for k in cls.__dataclass_fields__ if k in d}
        return cls(**known)


@dataclass
class Conf:
    slots: list[str]
    reminder_delay: int = 120
    followup_delay: int = 180
    demo: bool = True


# Pasos que hacen una pregunta al candidato.
QUESTION_STATES = {
    "BIENVENIDA", "INFO", "NOMBRE", "EDAD", "MUNICIPIO", "TURNO", "DOCUMENTOS",
    "EXPERIENCIA", "HORARIO", "CONFIRMACION", "RECORDATORIO", "OFRECER_ASESOR",
}
# Pasos de la solicitud: si el candidato deja de responder, se manda seguimiento.
SOLICITUD_STATES = {
    "NOMBRE", "EDAD", "MUNICIPIO", "TURNO", "DOCUMENTOS", "EXPERIENCIA",
    "HORARIO", "CONFIRMACION",
}

Result = tuple[Session, list[Action]]

# ---------------------------------------------------------------------------
# Sinónimos por paso (normalizados: sin acentos, minúsculas)
# ---------------------------------------------------------------------------

_APLICAR = [
    "aplicar", "quiero aplicar", "aplico", "me interesa", "quiero trabajar", "quiero trabajo",
    "busco trabajo", "postularme", "postular", "si", "si quiero", "claro", "me apunto",
    "quiero entrar", "si me interesa",
]

SYN: dict[str, dict[int, list[str]]] = {
    "BIENVENIDA": {
        0: _APLICAR,
        1: [
            "sueldo", "beneficios", "prestaciones", "informacion", "info", "mas informacion",
            "mas info", "cuanto pagan", "ver sueldo", "sueldo y prestaciones",
            "ver sueldo y prestaciones", "detalles", "saber mas", "que ofrecen",
        ],
    },
    "INFO": {
        0: _APLICAR,
        1: [
            "no", "no gracias", "ahorita no", "despues", "luego", "no me interesa",
            "no por ahora", "mas tarde", "tal vez despues",
        ],
    },
    "EDAD": {
        0: ["18 a 24", "de 18 a 24", "entre 18 y 24"],
        1: ["25 a 35", "de 25 a 35", "entre 25 y 35"],
        2: ["36 a 45", "de 36 a 45", "entre 36 y 45"],
        3: ["46 o mas", "mas de 46", "46 y mas", "46 mas", "mayor de 46", "46 en adelante"],
    },
    "MUNICIPIO": {
        0: ["huamantla"],
        1: ["apizaco"],
        2: [
            "tlaxcala", "tlaxcala de xicohtencatl", "tlaxcala centro", "la capital",
            "capital", "tlax", "ciudad de tlaxcala",
        ],
        3: [
            "otro", "otra", "otro municipio", "ninguno de esos", "ninguno", "otro lugar",
            "de otro lado", "otra parte", "ninguna",
        ],
    },
    "TURNO": {
        0: [
            "manana", "en la manana", "mananas", "por la manana", "temprano", "de manana",
            "primer turno", "primero", "de dia",
        ],
        1: ["tarde", "en la tarde", "tardes", "por la tarde", "segundo turno", "segundo"],
        2: [
            "noche", "en la noche", "noches", "por la noche", "tercer turno", "tercero",
            "de noche", "nocturna",
        ],
        3: [
            "cualquier", "cualquier turno", "me da igual", "da igual", "el que sea", "todos",
            "todos los turnos", "indistinto", "rotativo", "rolar", "el que haya", "no importa",
            "cualquier horario", "cualquiera esta bien", "los tres", "el que me den",
        ],
    },
    "DOCUMENTOS": {
        0: [
            "si", "si todos", "si los tengo", "si tengo todos", "tengo todos", "todos",
            "completos", "los tengo", "si los tengo todos", "claro", "si tengo",
            "no me falta", "no me falta ninguno", "no me falta nada", "tengo todo", "todo",
            "si cuento con todos", "cuento con todos",
        ],
        1: [
            "me falta", "me faltan", "falta", "faltan", "no", "no tengo", "no los tengo",
            "me falta uno", "no tengo todos", "todos menos", "casi todos", "no todos",
            "incompletos", "me falta la", "me falta el",
        ],
    },
    "EXPERIENCIA": {
        0: [
            "mas de 6 meses", "mas de seis meses", "ano y medio", "un ano", "anos",
            "varios anos", "mucho tiempo", "bastante", "mas de un ano", "si mucha",
        ],
        1: [
            "menos de 6 meses", "menos de seis meses", "poco", "poca", "unos meses",
            "pocos meses", "algunos meses", "un poco", "poquita",
        ],
        2: [
            "no", "nunca", "ninguna", "nada", "sin experiencia", "no tengo",
            "no tengo experiencia", "primera vez", "primer trabajo", "cero", "ninguno",
        ],
    },
    "CONFIRMACION": {
        0: [
            "si", "si confirmar", "confirmar", "confirmo", "correcto", "esta bien", "ok",
            "de acuerdo", "perfecto", "va", "listo", "sale", "si esta bien", "todo bien",
            "confirmado", "si confirmo",
        ],
        1: [
            "cambiar", "cambiar horario", "otro horario", "cambio", "modificar", "no",
            "cambiar hora", "otra hora", "otro dia",
        ],
    },
    "RECORDATORIO": {
        0: [
            "confirmo", "confirmo asistencia", "si", "si voy", "ahi estare", "alli estare",
            "asistire", "claro", "ok", "si asistire", "confirmado", "de acuerdo",
            "ahi nos vemos", "si confirmo",
        ],
        1: [
            "cambiar", "cambiar horario", "no puedo", "no voy a poder", "otro horario",
            "reagendar", "no", "no podre", "cambio", "otra hora", "otro dia",
        ],
    },
    "OFRECER_ASESOR": {
        0: ["si", "si por favor", "hablar", "persona", "con una persona", "asesor"],
        1: [
            "seguir aqui", "seguir", "continuar", "no", "aqui", "sigamos", "no gracias",
            "sigo",
        ],
    },
}

_CAMBIAR = [
    "cambiar", "cambiar horario", "otro horario", "reagendar", "no puedo",
    "no voy a poder", "cambiar la hora", "cambiar mi cita", "cambiar cita", "cambio de horario",
]


def _wants_change(text: str) -> bool:
    return contains_any(normalize(text), _CAMBIAR)

_DAYS = {
    "lun": "lunes", "mar": "martes", "mie": "miercoles", "jue": "jueves",
    "vie": "viernes", "sab": "sabado", "dom": "domingo",
}
_MONTHS = {
    "ene": "enero", "feb": "febrero", "mar": "marzo", "abr": "abril", "may": "mayo",
    "jun": "junio", "jul": "julio", "ago": "agosto", "sep": "septiembre",
    "oct": "octubre", "nov": "noviembre", "dic": "diciembre",
}


def slot_synonyms(slots: list[str]) -> dict[int, list[str]]:
    """Frases con las que el candidato puede referirse a cada horario."""
    out: dict[int, list[str]] = {}
    for i, slot in enumerate(slots):
        n = normalize(slot)
        toks = n.split()
        syn: list[str] = []
        for j, tok in enumerate(toks):
            if tok in _DAYS and j == 0:
                syn.append(_DAYS[tok])
            if tok.isdigit() and j + 1 < len(toks) and toks[j + 1] in _MONTHS:
                syn += [f"{tok} de {_MONTHS[toks[j + 1]]}", f"dia {tok}", f"el {tok}"]
        m = re.search(r"(\d{1,2}):(\d{2})", slot)
        if m:
            h, mm = str(int(m.group(1))), m.group(2)
            syn += [f"a las {h}", f"{h} {mm}", f"a las {h} {mm}"]
            if mm == "00":
                syn += [h, f"{h} am" if int(h) < 12 else f"{h} pm", f"las {h}"]
            if mm == "30":
                syn += [f"{h} y media", f"a las {h} y media"]
        out[i] = syn
    return out


# ---------------------------------------------------------------------------
# Intérpretes especiales
# ---------------------------------------------------------------------------


def _parse_age(n: str) -> int | None:
    nums = [int(x) for x in re.findall(r"\d+", n)]
    if len(nums) != 1 or not (10 <= nums[0] <= 99):
        return None
    a = nums[0]
    if a < 18:
        return None  # por defecto: no válida (no inventamos requisitos)
    if a <= 24:
        return 0
    if a <= 35:
        return 1
    if a <= 45:
        return 2
    return 3


_NUM_WORDS = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5, "seis": 6,
    "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11, "doce": 12, "medio": 0.5,
}
_DURATION = re.compile(
    r"\b(\d+|" + "|".join(_NUM_WORDS) + r")\s*(anos|ano|meses|mes)\b"
)


def _parse_experience(n: str) -> int | None:
    m = _DURATION.search(n)
    if not m:
        return None
    raw, unit = m.group(1), m.group(2)
    qty = float(raw) if raw.isdigit() else float(_NUM_WORDS[raw])
    months = qty * 12 if unit.startswith("ano") else qty
    return 0 if months >= 6 else 1


_MUNI_PREFIX = re.compile(
    r"^(yo\s+)?(vivo en|soy de|radico en|estoy en|vivo por|mi municipio es|"
    r"el municipio de|municipio de|municipio|en el municipio de|en|de)\s+",
    re.IGNORECASE,
)
_STATE_SUFFIX = re.compile(r"[\s,]+(tlaxcala|tlax|tlx|mexico|edo de tlaxcala|estado de tlaxcala)\.?$", re.IGNORECASE)
_MUNI_STOP = {"no se", "nose", "nada", "si", "no", "ok", "va", "gracias", "aqui", "cerca"}
_LOWER_PARTICLES = {"de", "del", "la", "las", "los", "y", "el"}
# Títulos de botones de otros pasos: si el candidato toca un botón viejo no
# lo tomamos como nombre de municipio.
_OTHER_TITLES = {
    normalize(t)
    for group in (
        V.OPC_BIENVENIDA, V.OPC_INFO, V.OPC_EDAD, V.OPC_TURNO, V.OPC_DOCUMENTOS,
        V.OPC_EXPERIENCIA, V.OPC_CONFIRMACION, V.OPC_RECORDATORIO, V.OPC_OFRECER_ASESOR,
    )
    for t in group
} | {"aplicar", "cambiar", "confirmar", "sueldo", "asesor"}


def _clean_municipio(text: str) -> str:
    t = sanitize(text).strip(" .,!¡")
    t = _MUNI_PREFIX.sub("", t).strip(" .,")
    stripped = _STATE_SUFFIX.sub("", t).strip(" .,")
    return stripped or t


def _match_municipio(text: str) -> tuple[int, str] | None:
    """(índice de opción, detalle) o None. "vivo en Zacatelco" → (3, "Zacatelco")."""
    cleaned = _clean_municipio(text)
    idx = match_option(cleaned, V.OPC_MUNICIPIO, SYN["MUNICIPIO"], allow_partial=False)
    if idx is None:
        idx = match_option(text, V.OPC_MUNICIPIO, SYN["MUNICIPIO"], allow_partial=False)
    if idx is not None:
        return idx, ""
    # ¿Nombre de otro municipio?
    n = normalize(cleaned)
    if not n or len(n) < 3 or len(cleaned) > 40 or re.search(r"\d", n):
        return None
    if len(n.split()) > 4 or n in _MUNI_STOP or n in _OTHER_TITLES or is_greeting(cleaned):
        return None
    if faq.looks_like_question(text) or faq.detect_topics(text):
        return None
    words = cleaned.split()
    pretty = " ".join(
        w.lower() if i > 0 and w.lower() in _LOWER_PARTICLES else w[:1].upper() + w[1:].lower()
        for i, w in enumerate(words)
    )
    return 3, pretty


# ---------------------------------------------------------------------------
# Preguntas de cada paso
# ---------------------------------------------------------------------------


def slot_titles(s: Session, conf: Conf) -> list[str]:
    """Horarios a ofrecer: los de Cal.com si hay, si no los fijos de la config."""
    return [o["title"] for o in s.offered] if s.offered else list(conf.slots)


def _options_for(s: Session, conf: Conf) -> list[str] | None:
    return {
        "BIENVENIDA": V.OPC_BIENVENIDA,
        "INFO": V.OPC_INFO,
        "EDAD": V.OPC_EDAD,
        "MUNICIPIO": V.OPC_MUNICIPIO,
        "TURNO": V.OPC_TURNO,
        "DOCUMENTOS": V.OPC_DOCUMENTOS,
        "EXPERIENCIA": V.OPC_EXPERIENCIA,
        "HORARIO": slot_titles(s, conf),
        "CONFIRMACION": V.OPC_CONFIRMACION,
        "RECORDATORIO": V.OPC_RECORDATORIO,
        "OFRECER_ASESOR": V.OPC_OFRECER_ASESOR,
    }.get(s.state)


def _synonyms_for(s: Session, conf: Conf) -> dict[int, list[str]]:
    if s.state == "HORARIO":
        return slot_synonyms(slot_titles(s, conf))
    return SYN.get(s.state, {})


def _question_text(s: Session, conf: Conf, reask: bool) -> str:
    d = s.data
    nombre = first_name(d.get("nombre"))
    st = s.state
    if st == "BIENVENIDA":
        return V.BIENVENIDA_REPREGUNTA if reask else V.BIENVENIDA_2
    if st == "INFO":
        return V.INFO_PREGUNTA
    if st == "NOMBRE":
        return V.NOMBRE_REPREGUNTA if reask else V.NOMBRE_PREGUNTA
    if st == "EDAD":
        if reask or not nombre:
            return V.EDAD_PREGUNTA
        return V.EDAD_PREGUNTA_PRIMERA.format(nombre=nombre)
    if st == "MUNICIPIO":
        return V.MUNICIPIO_PREGUNTA
    if st == "TURNO":
        return V.TURNO_PREGUNTA
    if st == "DOCUMENTOS":
        return V.DOCUMENTOS_PREGUNTA
    if st == "EXPERIENCIA":
        return V.EXPERIENCIA_PREGUNTA
    if st == "HORARIO":
        return V.HORARIO_PREGUNTA_REAGENDA if (s.reagenda and not reask) else V.HORARIO_PREGUNTA
    if st == "CONFIRMACION":
        return V.CONFIRMACION_PREGUNTA.format(
            nombre=d.get("nombre", ""), horario=d.get("horario", "")
        )
    if st == "RECORDATORIO":
        if reask:
            return V.RECORDATORIO_REPREGUNTA
        encabezado = V.RECORDATORIO_ENCABEZADO_DEMO if conf.demo else V.RECORDATORIO_ENCABEZADO
        return V.RECORDATORIO.format(
            encabezado=encabezado, nombre=nombre or "Hola", horario=d.get("horario", "")
        )
    if st == "OFRECER_ASESOR":
        return V.OFRECER_ASESOR
    return ""


def _lines(text: str) -> int:
    return text.count("\n") + 1 if text else 0


def ask(
    s: Session,
    conf: Conf,
    *,
    reask: bool = False,
    prefix: str | None = None,
    arm_followup: bool = True,
) -> list[Action]:
    """Acciones para (re)hacer la pregunta del paso actual.

    `prefix` es un texto previo ("No te entendí", respuesta de una pregunta
    frecuente…). Si cabe en 4 líneas junto con la pregunta, va en la misma
    burbuja; si no, en una burbuja aparte.
    """
    actions: list[Action] = []
    question = _question_text(s, conf, reask)
    if prefix and _lines(prefix) + _lines(question) <= 4:
        question = f"{prefix}\n{question}"
    elif prefix:
        actions.append(Send(prefix))
    options = _options_for(s, conf)
    actions.append(SendOptions(question, list(options)) if options else Send(question))
    if arm_followup and s.state in SOLICITUD_STATES:
        actions.append(Schedule(SEGUIMIENTO, conf.followup_delay))
    return actions


def _etapa(state: str) -> str:
    return f"En proceso: {V.NOMBRE_PASO.get(state, state)}"


def _goto(
    s: Session,
    state: str,
    conf: Conf,
    *,
    prefix: str | None = None,
    attrs: dict[str, str] | None = None,
    extra: list[Action] | None = None,
) -> Result:
    s.state = state
    s.fails = 0
    actions = ask(s, conf, prefix=prefix)
    values = {"etapa": _etapa(state)}
    values.update(attrs or {})
    actions += list(extra or [])
    actions.append(Attrs(values))
    return s, actions


# ---------------------------------------------------------------------------
# Entrada: conversación nueva, reinicio, asesor
# ---------------------------------------------------------------------------


def start(conf: Conf, *, fresh: bool = False) -> Result:
    """Bienvenida. `fresh=True` (reinicio) además limpia las etiquetas del bot y
    reemplaza los atributos, en una sola llamada cada uno (sin mandar listas
    vacías a Chatwoot)."""
    s = Session(state="BIENVENIDA")
    actions: list[Action] = [
        Send(V.BIENVENIDA_1),
        SendOptions(V.BIENVENIDA_2, list(V.OPC_BIENVENIDA)),
        Labels(add=[V.ETIQUETA_RECLUTAMIENTO], clear_bot=fresh),
        Attrs({"vacante": V.VACANTE_RESUMEN, "etapa": _etapa("BIENVENIDA")}, replace=fresh),
    ]
    return s, actions


def reset(s: Session | None, conf: Conf) -> Result:
    actions: list[Action] = []
    if s and s.data:
        actions.append(
            Note(rules.summary(s.data, context="🔄 Demo reiniciada (este fue el prospecto anterior)."))
        )
    actions += [CancelJobs(None), SetPending()]
    new, welcome = start(conf, fresh=True)
    return new, actions + welcome


def _handoff(s: Session, conf: Conf) -> Result:
    from_state = s.prev_state if s.state in ("OFRECER_ASESOR", "ASESOR") else s.state
    paso = V.NOMBRE_PASO.get(from_state or "", from_state or "")
    actions: list[Action] = [Send(V.ASESOR), CancelJobs(None)]
    if s.state == "INICIO":
        actions.append(Labels(add=[V.ETIQUETA_RECLUTAMIENTO]))
    s.prev_state = from_state
    s.state = "ASESOR"
    s.fails = 0
    actions += [
        Attrs({"etapa": "Con asesor", "vacante": V.VACANTE_RESUMEN}),
        Note(rules.summary(s.data, context=f"🙋 Pidió hablar con un asesor (paso: {paso}).")),
        Handoff(),
    ]
    return s, actions


def _resume(s: Session, text: str | None, conf: Conf) -> Result:
    """La conversación regresó al bot después de un asesor."""
    prev = s.prev_state or "BIENVENIDA"
    if prev in ("INICIO", "ASESOR", "OFRECER_ASESOR"):
        prev = "BIENVENIDA"
    s.state = prev
    s.prev_state = None
    s.fails = 0
    if prev in QUESTION_STATES or text is None:
        if prev not in QUESTION_STATES:
            return s, [Send(V.REGRESO_DE_ASESOR)]
        actions = ask(s, conf, reask=True, prefix=V.REGRESO_DE_ASESOR)
        actions.append(Attrs({"etapa": _etapa(prev)}))
        return s, actions
    return _dispatch_text(s, text, conf)


# ---------------------------------------------------------------------------
# Punto de entrada público
# ---------------------------------------------------------------------------


def handle_text(s: Session | None, text: str, conf: Conf) -> Result:
    text = sanitize(text)
    if s is None:
        s = Session()
    if is_reset(text):
        return reset(s, conf)
    if is_asesor(text):
        return _handoff(s, conf)
    if s.state == "INICIO":
        return start(conf)
    if s.state == "ASESOR":
        return _resume(s, text, conf)

    was_abandoned = s.abandoned
    s.abandoned = False
    s, actions = _dispatch_text(s, text, conf)
    if was_abandoned:
        # Regresó después del seguimiento: ya no cuenta como abandono.
        actions.append(Labels(remove=[V.ETIQUETA_ABANDONO]))
        if not any(isinstance(a, Attrs) and "etapa" in a.values for a in actions):
            actions.append(Attrs({"etapa": _etapa(s.state)}))
    return s, actions


def handle_media(s: Session | None, conf: Conf) -> Result:
    if s is None or s.state == "INICIO":
        return start(conf)
    if s.state == "ASESOR":
        return _resume(s, None, conf)
    if s.state in QUESTION_STATES:
        return s, ask(s, conf, reask=True, prefix=V.SOLO_TEXTO)
    return s, [Send(V.SOLO_TEXTO)]


def on_timer(s: Session | None, kind: str, state_at_schedule: str | None, conf: Conf) -> Result:
    if s is None:
        return Session(), []
    if kind == RECORDATORIO:
        if s.state != "AGENDADO":
            return s, []
        s.state = "RECORDATORIO"
        s.fails = 0
        actions = ask(s, conf)
        actions.append(Attrs({"etapa": "Recordatorio enviado"}))
        return s, actions
    if kind == SEGUIMIENTO:
        if (
            s.state not in SOLICITUD_STATES
            or s.state != state_at_schedule
            or s.followup_state == s.state
        ):
            return s, []
        s.followup_state = s.state
        s.abandoned = True
        nombre = first_name(s.data.get("nombre"))
        prefix = (
            V.SEGUIMIENTO_CON_NOMBRE.format(nombre=nombre) if nombre else V.SEGUIMIENTO_SIN_NOMBRE
        )
        paso = V.NOMBRE_PASO.get(s.state, s.state)
        actions = ask(s, conf, reask=True, prefix=prefix, arm_followup=False)
        actions += [
            Labels(add=[V.ETIQUETA_ABANDONO]),
            Attrs({"etapa": f"Abandonó en: {paso}"}),
            Note(rules.summary(s.data, context=f"⏳ Abandonó en: {paso} (se le mandó seguimiento).")),
        ]
        return s, actions
    return s, []


# ---------------------------------------------------------------------------
# Lógica por paso
# ---------------------------------------------------------------------------


def _dispatch_text(s: Session, text: str, conf: Conf) -> Result:
    handler = _HANDLERS.get(s.state)
    if handler is None:
        return start(conf)
    return handler(s, text, conf)


def _not_matched(s: Session, text: str, conf: Conf) -> Result:
    """El texto no responde la pregunta: privacidad, pregunta frecuente,
    saludo, pregunta desconocida o "no entendí" (2 veces → oferta de asesor)."""
    if looks_like_sensitive_id(text):
        return s, ask(s, conf, reask=True, prefix=V.PRIVACIDAD)
    ans = faq.answer(text)
    if ans:
        return s, ask(s, conf, reask=True, prefix=ans)
    if is_greeting(text):
        saludo = "¡Hola! 👋" if s.state in ("BIENVENIDA", "INFO") else V.SALUDO_A_MEDIO_FLUJO
        return s, ask(s, conf, reask=True, prefix=saludo)
    if faq.looks_like_question(text):
        return s, ask(s, conf, reask=True, prefix=V.FAQ_DESCONOCIDA)
    s.fails += 1
    if s.fails >= 2:
        s.fails = 0
        s.prev_state = s.state
        s.state = "OFRECER_ASESOR"
        return s, ask(s, conf)
    prefix = V.NO_ENTENDI_NOMBRE if s.state == "NOMBRE" else V.NO_ENTENDI
    return s, ask(s, conf, reask=True, prefix=prefix)


def _match(s: Session, text: str, conf: Conf, parser: Callable[[str], int | None] | None = None):
    options = _options_for(s, conf) or []
    return match_option(text, options, _synonyms_for(s, conf), parser)


def _h_bienvenida(s: Session, text: str, conf: Conf) -> Result:
    idx = _match(s, text, conf)
    if idx == 0:
        return _goto(s, "NOMBRE", conf)
    if idx == 1:
        s.state = "INFO"
        s.fails = 0
        actions: list[Action] = [Send(V.INFO_TARJETA)] + ask(s, conf)
        actions.append(Attrs({"etapa": _etapa("INFO")}))
        return s, actions
    return _not_matched(s, text, conf)


def _h_info(s: Session, text: str, conf: Conf) -> Result:
    idx = _match(s, text, conf)
    if idx == 0:
        return _goto(s, "NOMBRE", conf)
    if idx == 1:
        s.state = "NO_INTERESADO"
        s.fails = 0
        return s, [
            Send(V.NO_INTERESADO),
            CancelJobs([SEGUIMIENTO]),
            Attrs({"etapa": "No interesado"}),
            Note(rules.summary(s.data, context="🙅 Vio sueldo y prestaciones; por ahora no le interesa.")),
        ]
    return _not_matched(s, text, conf)


def _h_no_interesado(s: Session, text: str, conf: Conf) -> Result:
    if match_option(text, V.OPC_INFO[:1], {0: _APLICAR}) == 0:
        return _goto(s, "NOMBRE", conf)
    ans = faq.answer(text)
    if ans:
        return s, [Send(ans), Send(V.NO_INTERESADO_OTRO_TEXTO)]
    return s, [Send(V.NO_INTERESADO_OTRO_TEXTO)]


def _h_nombre(s: Session, text: str, conf: Conf) -> Result:
    if looks_like_sensitive_id(text) or faq.answer(text) or is_greeting(text):
        return _not_matched(s, text, conf)
    name = parse_name(text)
    if name:
        s.data["nombre"] = name
        return _goto(s, "EDAD", conf, attrs={"candidato_nombre": name})
    return _not_matched(s, text, conf)


def _h_edad(s: Session, text: str, conf: Conf) -> Result:
    idx = _match(s, text, conf, _parse_age)
    if idx is None:
        return _not_matched(s, text, conf)
    s.data["edad"] = V.OPC_EDAD[idx]
    return _goto(s, "MUNICIPIO", conf, attrs={"candidato_edad": V.OPC_EDAD[idx]})


def _h_municipio(s: Session, text: str, conf: Conf) -> Result:
    found = _match_municipio(text)
    if found is None:
        return _not_matched(s, text, conf)
    idx, detalle = found
    s.data["municipio"] = V.OPC_MUNICIPIO[idx]
    if detalle:
        s.data["municipio_detalle"] = detalle
    else:
        s.data.pop("municipio_detalle", None)
    return _goto(s, "TURNO", conf, attrs={"candidato_municipio": rules.municipio_display(s.data)})


def _h_turno(s: Session, text: str, conf: Conf) -> Result:
    idx = _match(s, text, conf)
    if idx is None:
        return _not_matched(s, text, conf)
    s.data["turno"] = V.OPC_TURNO[idx]
    return _goto(s, "DOCUMENTOS", conf, attrs={"candidato_turno": V.OPC_TURNO[idx]})


def _h_documentos(s: Session, text: str, conf: Conf) -> Result:
    idx = _match(s, text, conf)
    if idx is None:
        return _not_matched(s, text, conf)
    s.data["documentos"] = "Completos" if idx == 0 else "Incompletos"
    return _goto(s, "EXPERIENCIA", conf, attrs={"candidato_documentos": s.data["documentos"]})


def _h_experiencia(s: Session, text: str, conf: Conf) -> Result:
    idx = _match(s, text, conf, _parse_experience)
    if idx is None:
        return _not_matched(s, text, conf)
    s.data["experiencia"] = V.OPC_EXPERIENCIA[idx]
    clasificacion, lineas = rules.classify(s.data)
    s.data["clasificacion"] = clasificacion
    if rules.is_califica(clasificacion):
        labels = Labels(add=[V.ETIQUETA_CALIFICA], remove=[V.ETIQUETA_REVISAR])
    else:
        labels = Labels(add=[V.ETIQUETA_REVISAR], remove=[V.ETIQUETA_CALIFICA])
    return _goto(
        s,
        "HORARIO",
        conf,
        prefix="\n".join(lineas),
        attrs={"candidato_experiencia": V.OPC_EXPERIENCIA[idx], "clasificacion": clasificacion},
        extra=[labels],
    )


def _h_horario(s: Session, text: str, conf: Conf) -> Result:
    idx = _match(s, text, conf)
    if idx is None:
        return _not_matched(s, text, conf)
    titles = slot_titles(s, conf)
    s.data["horario"] = titles[idx]
    if s.offered and idx < len(s.offered):
        s.data["horario_iso"] = s.offered[idx]["start"]
    else:
        s.data.pop("horario_iso", None)
    return _goto(s, "CONFIRMACION", conf, attrs={"entrevista_horario": titles[idx]})


def _espera_legible(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} segundos"
    minutes = round(seconds / 60)
    return "1 minuto" if minutes == 1 else f"{minutes} minutos"


def _h_confirmacion(s: Session, text: str, conf: Conf) -> Result:
    idx = _match(s, text, conf)
    if idx == 1:
        return _goto(s, "HORARIO", conf)
    if idx != 0:
        return _not_matched(s, text, conf)
    reagendo = s.reagenda
    s.state = "AGENDADO"
    s.fails = 0
    s.reagenda = False
    s.data["asistencia"] = "Por confirmar"
    nombre = first_name(s.data.get("nombre"))
    msg = V.AGENDADO.format(nombre=nombre, horario=s.data.get("horario", ""))
    if conf.demo:
        msg += "\n" + V.AGENDADO_DEMO.format(espera=_espera_legible(conf.reminder_delay))
    context = "📅 Reagendó su entrevista." if reagendo else "📅 Entrevista agendada."
    return s, [
        Send(msg),
        CancelJobs([SEGUIMIENTO, RECORDATORIO]),
        Schedule(RECORDATORIO, conf.reminder_delay),
        Labels(add=[V.ETIQUETA_AGENDADA]),
        Attrs(
            {
                "entrevista_horario": s.data.get("horario", ""),
                "entrevista_asistencia": "Por confirmar",
                "etapa": "Entrevista agendada",
            }
        ),
        Note(rules.summary(s.data, context=context)),
    ]


def _reagendar(s: Session, conf: Conf) -> Result:
    s.reagenda = True
    s.data["asistencia"] = "Pidió cambio de horario"
    return _goto(
        s,
        "HORARIO",
        conf,
        attrs={"entrevista_asistencia": "Pidió cambio de horario"},
        extra=[CancelJobs([RECORDATORIO]), Labels(add=[V.ETIQUETA_CAMBIO])],
    )


def _h_recordatorio(s: Session, text: str, conf: Conf) -> Result:
    idx = _match(s, text, conf)
    if idx == 1:
        return _reagendar(s, conf)
    if idx != 0:
        return _not_matched(s, text, conf)
    s.state = "CONFIRMADO"
    s.fails = 0
    s.data["asistencia"] = "Confirmó"
    nombre = first_name(s.data.get("nombre"))
    msg = V.CONFIRMADO.format(nombre=nombre, horario=s.data.get("horario", ""))
    if conf.demo:
        msg += "\n\n" + V.TIP_REINICIAR
    return s, [
        Send(msg),
        Labels(add=[V.ETIQUETA_CONFIRMO]),
        Attrs({"entrevista_asistencia": "Confirmó", "etapa": "Confirmó asistencia"}),
        Note(rules.summary(s.data, context="✅ Confirmó asistencia en el recordatorio.")),
    ]


def _passive(s: Session, text: str, conf: Conf, template: str) -> Result:
    """Agendado / confirmado: responde dudas o le recuerda su cita."""
    if _wants_change(text):
        return _reagendar(s, conf)
    if looks_like_sensitive_id(text):
        return s, [Send(V.PRIVACIDAD)]
    ans = faq.answer(text)
    if ans:
        return s, [Send(ans)]
    msg = template.format(horario=s.data.get("horario", ""))
    if conf.demo:
        msg += "\n\n" + V.TIP_REINICIAR
    return s, [Send(msg)]


def _h_agendado(s: Session, text: str, conf: Conf) -> Result:
    return _passive(s, text, conf, V.AGENDADO_OTRO_TEXTO)


def _h_confirmado(s: Session, text: str, conf: Conf) -> Result:
    return _passive(s, text, conf, V.CONFIRMADO_OTRO_TEXTO)


def _h_ofrecer_asesor(s: Session, text: str, conf: Conf) -> Result:
    idx = _match(s, text, conf)
    if idx == 0:
        return _handoff(s, conf)
    prev = s.prev_state or "BIENVENIDA"
    s.state = prev
    s.prev_state = None
    s.fails = 0
    if idx == 1:
        return s, ask(s, conf, reask=True, prefix=V.SEGUIR_AQUI)
    # Quizá ya respondió la pregunta original: la intentamos en ese paso.
    return _dispatch_text(s, text, conf)


_HANDLERS: dict[str, Callable[[Session, str, Conf], Result]] = {
    "BIENVENIDA": _h_bienvenida,
    "INFO": _h_info,
    "NO_INTERESADO": _h_no_interesado,
    "NOMBRE": _h_nombre,
    "EDAD": _h_edad,
    "MUNICIPIO": _h_municipio,
    "TURNO": _h_turno,
    "DOCUMENTOS": _h_documentos,
    "EXPERIENCIA": _h_experiencia,
    "HORARIO": _h_horario,
    "CONFIRMACION": _h_confirmacion,
    "AGENDADO": _h_agendado,
    "RECORDATORIO": _h_recordatorio,
    "CONFIRMADO": _h_confirmado,
    "OFRECER_ASESOR": _h_ofrecer_asesor,
}


# ---------------------------------------------------------------------------
# v2: ¿el guion entiende este texto sin ayuda de la IA?
# ---------------------------------------------------------------------------

_PARSERS: dict[str, Callable[[str], int | None]] = {
    "EDAD": _parse_age,
    "EXPERIENCIA": _parse_experience,
}

# Campo que llena cada paso de la solicitud (para datos extra de la IA).
FIELD_BY_STATE = {
    "NOMBRE": "nombre",
    "EDAD": "edad",
    "MUNICIPIO": "municipio",
    "TURNO": "turno",
    "DOCUMENTOS": "documentos",
    "EXPERIENCIA": "experiencia",
}


def quick_match(s: Session | None, text: str, conf: Conf) -> bool:
    """True si el guion puede resolver el texto por sí solo (botón, número,
    palabra clave, comando). Sin efectos secundarios."""
    if s is None or s.state in ("INICIO", "ASESOR"):
        return True
    if is_reset(text) or is_asesor(text):
        return True
    if s.state == "NOMBRE":
        return (
            parse_name(text) is not None
            and not faq.answer(text)
            and not is_greeting(text)
            and not faq.looks_like_question(text)
        )
    if s.state == "MUNICIPIO":
        return _match_municipio(text) is not None
    if s.state in ("AGENDADO", "CONFIRMADO", "NO_INTERESADO"):
        return False
    options = _options_for(s, conf)
    if not options:
        return False
    return _match(s, text, conf, _PARSERS.get(s.state)) is not None


def current_question(s: Session, conf: Conf) -> tuple[str, list[str]]:
    """Pregunta y opciones del paso actual (para la IA)."""
    return _question_text(s, conf, reask=True), list(_options_for(s, conf) or [])
