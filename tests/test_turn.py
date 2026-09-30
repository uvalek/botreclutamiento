"""v2: orquestador del turno con IA simulada (sin llamadas reales)."""

import pytest

from app import llm, memory, turn
from app import vacante as V
from app.agents import redactor
from app.flow import engine as E
from app.tools import cal

CONF = E.Conf(slots=["Mié 30 sep 9:00", "Mié 30 sep 11:30", "Jue 1 oct 9:00"])


class FakeLLM:
    """Responde según el propósito de la llamada."""

    def __init__(self):
        self.interp = None
        self.redactor = None
        self.m1 = None
        self.calls = []

    async def chat_json(self, system, messages, *, chat_id="", max_tokens=600, purpose="llm"):
        self.calls.append(purpose)
        value = {"interprete": self.interp, "redactor": self.redactor, "m1": self.m1}.get(purpose)
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def fake(monkeypatch):
    f = FakeLLM()
    monkeypatch.setattr(llm, "chat_json", f.chat_json)
    monkeypatch.setattr(llm, "available", lambda chat_id="": True)

    async def no_history(*a, **k):
        return []

    monkeypatch.setattr(memory, "load", no_history)
    monkeypatch.setattr(cal, "ready", lambda: False)
    return f


def texts(actions):
    return [a.text for a in actions if isinstance(a, (E.Send, E.SendOptions))]


async def at_state(state_msgs):
    s = None
    for m in state_msgs:
        s, _ = E.handle_text(s, m, CONF)
    return s


async def run(s, text, phone="+522411234567"):
    return await turn.run(s, turn.Batch(conversation_id=7, status="pending", texts=[text], phone=phone), CONF)


async def test_boton_no_usa_interprete(fake):
    s = await at_state(["hola"])
    fake.redactor = {"burbujas": ["¡Perfecto! Para empezar, ¿me dices tu **nombre completo**?"]}
    r = await run(s, "Quiero aplicar")
    assert r.session.state == "NOMBRE"
    assert "interprete" not in fake.calls
    assert r.via == "ia"  # el redactor sí lo hizo natural
    assert texts(r.actions) == ["¡Perfecto! Para empezar, ¿me dices tu **nombre completo**?"]


async def test_varios_datos_en_un_mensaje(fake):
    s = await at_state(["hola", "1"])
    fake.interp = {
        "respuesta_paso": "Juan Pérez",
        "datos_extra": {"edad": "27", "municipio": "Apizaco", "turno": "Matutino"},
        "pregunta": None,
        "quiere_asesor": False,
    }
    fake.redactor = None  # sin redactor: textos del guion
    r = await run(s, "soy Juan Pérez, tengo 27, vivo en Apizaco y prefiero en la mañana")
    s = r.session
    assert s.state == "DOCUMENTOS"
    assert s.data["nombre"] == "Juan Pérez"
    assert s.data["edad"] == "25–35"
    assert s.data["municipio"] == "Apizaco"
    assert s.data["turno"] == "Matutino"
    msgs = [a for a in r.actions if isinstance(a, (E.Send, E.SendOptions))]
    assert len(msgs) == 1 and msgs[0].options == V.OPC_DOCUMENTOS  # solo la última pregunta
    attrs = [a.values for a in r.actions if isinstance(a, E.Attrs)]
    assert any("candidato_turno" in v for v in attrs)
    assert s.data["telefono"] == "+522411234567"


async def test_dato_extra_invalido_se_detiene(fake):
    s = await at_state(["hola", "1"])
    fake.interp = {"respuesta_paso": "Ana Ruiz", "datos_extra": {"edad": "banana"}}
    r = await run(s, "Ana Ruiz y tengo banana años")
    assert r.session.state == "EDAD"


async def test_respuesta_mas_pregunta(fake, monkeypatch):
    s = await at_state(["hola", "1", "Juan Pérez"])
    fake.interp = {"respuesta_paso": "25–35", "datos_extra": {}, "pregunta": "¿hay transporte?"}
    r = await run(s, "tengo 30, ¿y hay transporte?")
    assert r.session.state == "MUNICIPIO"
    body = texts(r.actions)
    assert V.FAQ_RESPUESTAS["transporte"] in body[0]


async def test_solo_pregunta_rag(fake, monkeypatch):
    s = await at_state(["hola", "1", "Juan Pérez"])
    fake.interp = {"respuesta_paso": None, "datos_extra": {}, "pregunta": "¿qué fabrican?"}
    fake.m1 = {"respuesta": "Fabricamos arneses eléctricos y piezas plásticas.", "encontrado": True}

    async def fake_retrieve(q, k=4, min_similarity=0.2):
        return ["La planta fabrica arneses eléctricos y piezas plásticas inyectadas."]

    from app import rag

    monkeypatch.setattr(rag, "retrieve", fake_retrieve)
    r = await run(s, "oye y qué fabrican ahí")
    assert r.session.state == "EDAD"
    body = "\n".join(texts(r.actions))
    assert "arneses" in body and V.EDAD_PREGUNTA in body


async def test_rag_sin_dato_no_inventa(fake, monkeypatch):
    s = await at_state(["hola", "1", "Juan Pérez"])
    fake.interp = {"respuesta_paso": None, "pregunta": "¿dan vales de despensa?"}
    fake.m1 = {"respuesta": "No tengo ese dato", "encontrado": False}
    from app import rag

    async def none(*a, **k):
        return []

    monkeypatch.setattr(rag, "retrieve", none)
    r = await run(s, "¿dan vales de despensa?")
    assert V.FAQ_DESCONOCIDA in "\n".join(texts(r.actions))


async def test_ia_caida_usa_guion(fake):
    s = await at_state(["hola", "1", "Juan Pérez"])
    fake.interp = None  # la IA no respondió
    fake.redactor = None
    r = await run(s, "pues fíjate que tengo veintitantos")
    assert r.session.state == "EDAD"
    assert V.NO_ENTENDI in texts(r.actions)[0]


def test_redactor_rechaza_si_cambia_datos():
    base = ["¡Listo, Juan! ✅ Tu entrevista quedó para el **Jue 1 oct 9:00**."]
    assert redactor.validate(["¡Listo! Te esperamos el jueves."], base, ["Juan"]) is None
    ok = redactor.validate(["¡Listo, Juan! Quedó para el **Jue 1 oct 9:00** 🙌"], base, ["Juan"])
    assert ok


def test_redactor_exige_pregunta():
    base = ["¿En qué municipio vives?"]
    assert redactor.validate(["Gracias por la info."], base, []) is None
    assert redactor.validate(["Gracias 🙂", "¿En qué municipio vives?"], base, [])


def test_redactor_limita_burbujas():
    base = ["¿Qué turno prefieres?"]
    assert redactor.validate(["a", "b", "c", "¿Qué turno?"], base, []) is None


async def test_bienvenida_no_se_humaniza(fake):
    fake.redactor = {"burbujas": ["Hola"]}
    r = await run(None, "hola")
    assert "demostración de Adlek" in texts(r.actions)[0]
    assert "redactor" not in fake.calls


async def test_reiniciar_nuevo_prospecto(fake):
    s = await at_state(["hola", "1", "Juan Pérez"])
    s.prospect = "abc"
    r = await run(s, "reiniciar")
    assert r.session.prospect and r.session.prospect != "abc"
    assert r.session.state == "BIENVENIDA"


def _slots_jueves(busy_minutes=()):
    from app.tools import cal as C

    out = []
    for m in range(9 * 60, 17 * 60, 30):  # 9:00 a 16:30
        if m in busy_minutes:
            continue
        h, mm = divmod(m + 6 * 60, 60)  # México = UTC-6
        out.append(C.enrich(f"2026-10-01T{h:02d}:{mm:02d}:00.000Z"))
    return out


@pytest.fixture
def calmock(monkeypatch):
    monkeypatch.setattr(cal, "ready", lambda: True)
    state = {"slots": _slots_jueves(busy_minutes=(12 * 60,)), "booked": {}, "uid": "uid-123",
             "taken": False}

    async def slots():
        return state["slots"]

    async def book(**kw):
        state["booked"].update(kw)
        return cal.BookingResult(uid=state["uid"], taken=state["taken"])

    monkeypatch.setattr(cal, "get_slots", slots)
    monkeypatch.setattr(cal, "book", book)
    return state


async def test_calcom_rangos_y_agenda(fake, calmock):
    s = await at_state(["hola", "1", "Juan Pérez", "27", "Apizaco", "Matutino", "Sí, todos"])
    r = await run(s, "Más de 6 meses")
    s = r.session
    assert s.state == "HORARIO"
    assert [a.options for a in r.actions if isinstance(a, E.SendOptions)][-1] == ["Jue 1 oct"]
    r = await run(s, "el jueves")
    s = r.session
    assert s.state == "HORA"
    body = texts(r.actions)[-1]
    assert "**jueves 1 de octubre**" in body
    assert "**de 9:00 am a 12:00 pm**" in body and "**de 12:30 pm a 5:00 pm**" in body
    r = await run(s, "a las 12")
    assert r.session.state == "HORA" and V.HORA_OCUPADA in texts(r.actions)[0]
    r = await run(r.session, "a las 3 de la tarde")
    s = r.session
    assert s.state == "CORREO"
    assert s.data["horario"] == "Jue 1 oct 3:00 pm"
    assert [a.options for a in r.actions if isinstance(a, E.SendOptions)][-1] == ["Sin correo"]
    r = await run(s, "Sin correo")
    assert r.session.state == "CONFIRMACION"
    r = await run(r.session, "Sí, confirmar")
    assert r.session.state == "AGENDADO"
    assert r.session.data["cal_booking_uid"] == "uid-123"
    assert calmock["booked"]["start"] == "2026-10-01T21:00:00.000Z"


async def test_calcom_dia_y_hora_de_una_vez(fake, calmock):
    s = await at_state(["hola", "1", "Juan Pérez", "27", "Apizaco", "Matutino", "Sí, todos"])
    r = await run(s, "Más de 6 meses")
    r = await run(r.session, "el jueves a las 10:30")
    assert r.session.state == "CORREO"
    assert r.session.data["horario"] == "Jue 1 oct 10:30 am"


async def test_calcom_horario_ocupado_al_confirmar(fake, calmock):
    s = await at_state(["hola", "1", "Juan Pérez", "27", "Apizaco", "Matutino", "Sí, todos"])
    r = await run(s, "Más de 6 meses")
    r = await run(r.session, "1")
    r = await run(r.session, "9:00 am")
    r = await run(r.session, "Sin correo")
    calmock["uid"] = None  # alguien lo ganó
    calmock["taken"] = True
    r = await run(r.session, "Sí, confirmar")
    assert r.session.state == "HORARIO"
    assert "se acaba de ocupar" in texts(r.actions)[0]
    assert not any(isinstance(a, E.Schedule) and a.kind == E.RECORDATORIO for a in r.actions)


def test_rangos_y_horas():
    from app.tools import cal as C

    slots = _slots_jueves(busy_minutes=(12 * 60, 12 * 60 + 30))
    assert E.ranges(slots) == [(540, 720), (780, 1020)]
    assert [x["time"] for x in E.suggestions(slots)][0] == "9:00 am"
    assert C.fmt_time(12 * 60) == "12:00 pm" and C.fmt_time(13 * 60 + 30) == "1:30 pm"
    assert C.slot_title("2026-10-01T15:00:00Z") == "Jue 1 oct 9:00 am"
    assert cal.normalize_phone("+5212411234567") == "+522411234567"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("a las 10", 600),
        ("10:30", 630),
        ("10:30 am", 630),
        ("diez y media", 630),
        ("a las 3 de la tarde", 900),
        ("3", 900),
        ("1:00 pm", 780),
        ("mediodía", 720),
        ("a las doce", 720),
        ("como a las 9", 540),
        ("hola", None),
    ],
)
def test_parse_time(text, expected):
    assert E.parse_time(text) == expected


async def test_calcom_error_distinto_no_atora(fake, calmock):
    s = await at_state(["hola", "1", "Juan Pérez", "27", "Apizaco", "Matutino", "Sí, todos"])
    r = await run(s, "Más de 6 meses")
    r = await run(r.session, "1")
    r = await run(r.session, "9:00 am")
    r = await run(r.session, "no")
    calmock["uid"] = None  # p. ej. correo rechazado: NO es horario ocupado
    r = await run(r.session, "Sí, confirmar")
    assert r.session.state == "AGENDADO"
    assert any(isinstance(a, E.Schedule) and a.kind == E.RECORDATORIO for a in r.actions)


def test_correo_del_candidato():
    assert cal.attendee_email("+522411234567", "x") == "adlekcontact+wa522411234567@gmail.com"
    assert cal._is_taken(400, '{"message":"User either already has booking at this time or is not available"}')
    assert not cal._is_taken(400, '{"message":"email_domain_cannot_receive_mail"}')


async def test_correo_opcional_y_reserva_con_correo(fake, calmock):
    s = await at_state(["hola", "1", "Juan Pérez", "27", "Apizaco", "Matutino", "Sí, todos"])
    r = await run(s, "Más de 6 meses")
    r = await run(r.session, "el jueves a las 10")
    r = await run(r.session, "claro, es Juan.Perez@Gmail.com")
    s = r.session
    assert s.state == "CONFIRMACION"
    assert s.data["correo"] == "juan.perez@gmail.com"
    assert "juan.perez@gmail.com" in texts(r.actions)[-1]
    r = await run(s, "Sí, confirmar")
    assert calmock["booked"]["email"] == "juan.perez@gmail.com"
    # Reagendar no vuelve a pedir correo
    r.session.state = "RECORDATORIO"
    r = await run(r.session, "2")
    r = await run(r.session, "1")
    r = await run(r.session, "9:00 am")
    assert r.session.state == "CONFIRMACION"


async def test_correo_rechazado_reintenta_con_interno(fake, monkeypatch):
    monkeypatch.setattr(cal, "ready", lambda: True)
    calls = []

    async def slots():
        return _slots_jueves()

    async def book(**kw):
        calls.append(kw.get("email"))
        return cal.BookingResult(uid=None if kw.get("email") else "uid-ok")

    monkeypatch.setattr(cal, "get_slots", slots)
    monkeypatch.setattr(cal, "book", book)
    s = await at_state(["hola", "1", "Juan Pérez", "27", "Apizaco", "Matutino", "Sí, todos"])
    r = await run(s, "Más de 6 meses")
    r = await run(r.session, "el jueves a las 10")
    r = await run(r.session, "juan arroba gmail punto com")
    r = await run(r.session, "si")
    assert calls == ["juan@gmail.com", None]
    assert r.session.data["cal_booking_uid"] == "uid-ok"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("juan.perez@gmail.com", "juan.perez@gmail.com"),
        ("mi correo es Ana_R@hotmail.com.mx", "ana_r@hotmail.com.mx"),
        ("juan arroba gmail punto com", "juan@gmail.com"),
        ("juan@gmail", None),
        ("no tengo", None),
    ],
)
def test_extract_email(text, expected):
    assert E.extract_email(text) == expected
