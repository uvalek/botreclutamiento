"""Conversaciones completas contra la máquina de estados (sin Chatwoot)."""

from app import vacante as V
from app.flow import engine as E

CONF = E.Conf(slots=["Mié 30 sep 9:00", "Mié 30 sep 11:30", "Jue 1 oct 9:00"])


def texts(actions):
    return [a.text for a in actions if isinstance(a, (E.Send, E.SendOptions))]


def of(actions, cls):
    return [a for a in actions if isinstance(a, cls)]


def talk(session, *messages):
    actions = []
    for m in messages:
        session, actions = E.handle_text(session, m, CONF)
    return session, actions


def happy_until_horario():
    return talk(None, "hola", "Quiero aplicar", "Juan Pérez", "25–35", "Apizaco", "Matutino",
                "Sí, todos", "Más de 6 meses")


def test_bienvenida_con_aviso_de_demo():
    s, actions = E.handle_text(None, "Hola", CONF)
    assert s.state == "BIENVENIDA"
    assert "demostración de Adlek" in texts(actions)[0]
    assert of(actions, E.SendOptions)[0].options == V.OPC_BIENVENIDA
    assert of(actions, E.Labels)[0].add == [V.ETIQUETA_RECLUTAMIENTO]


def test_camino_feliz_hasta_confirmado():
    s, actions = happy_until_horario()
    assert s.state == "HORARIO"
    assert s.data["clasificacion"] == "Califica"
    assert V.CLASIF_CALIFICA in texts(actions)[0]
    assert of(actions, E.Labels)[0].add == [V.ETIQUETA_CALIFICA]

    s, actions = talk(s, "1")
    assert s.state == "CONFIRMACION"
    assert "Juan Pérez" in texts(actions)[0]

    s, actions = talk(s, "Sí, confirmar")
    assert s.state == "AGENDADO"
    assert "2 minutos" in texts(actions)[0]
    sched = of(actions, E.Schedule)
    assert sched and sched[0].kind == E.RECORDATORIO and sched[0].delay_seconds == 120
    note = of(actions, E.Note)[0].text
    assert "Califica · 25–35 · Apizaco · Matutino · +6 meses · Entrevista Mié 30 sep 9:00" in note

    s, actions = E.on_timer(s, E.RECORDATORIO, "AGENDADO", CONF)
    assert s.state == "RECORDATORIO"
    assert "Responde **1**" in texts(actions)[0]

    s, actions = talk(s, "1")
    assert s.state == "CONFIRMADO"
    assert of(actions, E.Labels)[0].add == [V.ETIQUETA_CONFIRMO]
    assert "Confirmó" in of(actions, E.Note)[0].text


def test_recordatorio_opcion_2_reagenda():
    s, _ = happy_until_horario()
    s, _ = talk(s, "1", "si")
    s, _ = E.on_timer(s, E.RECORDATORIO, "AGENDADO", CONF)
    s, actions = talk(s, "2")
    assert s.state == "HORARIO"
    assert s.reagenda
    assert V.ETIQUETA_CAMBIO in of(actions, E.Labels)[0].add
    assert texts(actions)[0] == V.HORARIO_PREGUNTA_REAGENDA
    s, _ = talk(s, "el jueves")
    s, actions = talk(s, "sí")
    assert s.state == "AGENDADO"
    assert s.data["horario"] == "Jue 1 oct 9:00"
    assert "Reagendó" in of(actions, E.Note)[0].text
    assert of(actions, E.Schedule)[0].kind == E.RECORDATORIO


def test_info_y_por_ahora_no():
    s, actions = talk(None, "hola", "Sueldo y beneficios")
    assert s.state == "INFO"
    assert "$2,450" in texts(actions)[0]
    s, actions = talk(s, "Por ahora no")
    assert s.state == "NO_INTERESADO"
    s, actions = talk(s, "aplicar")
    assert s.state == "NOMBRE"


def test_revisar_por_documentos_y_ruta():
    s, actions = talk(None, "hola", "1", "Ana López", "18–24", "vivo en Zacatelco", "noche",
                      "Me falta alguno", "Sin experiencia")
    assert s.data["clasificacion"] == "Revisar: faltan documentos y fuera de ruta"
    body = texts(actions)[0]
    assert V.CLASIF_FALTAN_DOCS in body and V.CLASIF_FUERA_RUTA in body
    assert of(actions, E.Labels)[0].add == [V.ETIQUETA_REVISAR]
    assert s.state == "HORARIO"  # sí puede agendar


def test_pregunta_frecuente_regresa_al_paso():
    s, _ = talk(None, "hola", "1", "Juan Pérez")
    assert s.state == "EDAD"
    s, actions = talk(s, "¿cuánto pagan?")
    assert s.state == "EDAD"
    body = "\n".join(texts(actions))
    assert "$2,450" in body and V.EDAD_PREGUNTA in body
    assert of(actions, E.SendOptions)[-1].options == V.OPC_EDAD


def test_pregunta_frecuente_en_nombre_no_se_toma_como_nombre():
    s, actions = talk(None, "hola", "1", "cuanto pagan")
    assert s.state == "NOMBRE"
    assert "nombre" not in s.data


def test_pregunta_desconocida_no_inventa():
    s, _ = talk(None, "hola", "1", "Juan Pérez")
    s, actions = talk(s, "¿dan vales de despensa?")
    assert V.FAQ_DESCONOCIDA in "\n".join(texts(actions))
    assert s.state == "EDAD"


def test_no_entendi_dos_veces_ofrece_asesor():
    s, _ = talk(None, "hola", "1", "Juan Pérez", "25–35", "Apizaco")
    assert s.state == "TURNO"
    s, actions = talk(s, "blablabla")
    assert s.state == "TURNO"
    assert V.NO_ENTENDI in texts(actions)[0]
    s, actions = talk(s, "xyzxyz")
    assert s.state == "OFRECER_ASESOR"
    assert of(actions, E.SendOptions)[0].options == V.OPC_OFRECER_ASESOR
    # "Seguir aquí" regresa al paso
    s, actions = talk(s, "Seguir aquí")
    assert s.state == "TURNO"
    # Y si responde directamente la pregunta original también sirve
    s, _ = talk(s, "zzz", "qqq")
    assert s.state == "OFRECER_ASESOR"
    s, _ = talk(s, "vespertino")
    assert s.state == "DOCUMENTOS"
    assert s.data["turno"] == "Vespertino"


def test_ofrecer_asesor_y_aceptar():
    s, _ = talk(None, "hola", "1", "Juan Pérez", "25–35", "Apizaco", "zzz", "qqq")
    s, actions = talk(s, "Hablar con asesor")
    assert s.state == "ASESOR"
    assert s.prev_state == "TURNO"
    assert of(actions, E.Handoff)


def test_asesor_en_cualquier_momento():
    s, _ = talk(None, "hola", "1", "Juan Pérez")
    s, actions = talk(s, "quiero hablar con un asesor")
    assert s.state == "ASESOR"
    assert texts(actions) == [V.ASESOR]
    assert of(actions, E.Handoff)
    assert of(actions, E.CancelJobs)[0].kinds is None
    assert "Pidió hablar con un asesor (paso: Edad)" in of(actions, E.Note)[0].text


def test_regreso_despues_de_asesor_retoma_el_paso():
    s, _ = talk(None, "hola", "1", "Juan Pérez", "asesor")
    s, actions = talk(s, "hola")
    assert s.state == "EDAD"
    assert V.REGRESO_DE_ASESOR in texts(actions)[0]


def test_reiniciar_limpia_todo():
    s, _ = happy_until_horario()
    s, actions = talk(s, "reiniciar")
    assert s.state == "BIENVENIDA"
    assert s.data == {}
    kinds = [type(a).__name__ for a in actions]
    assert kinds[:3] == ["Note", "CancelJobs", "SetPending"]
    assert "Demo reiniciada" in of(actions, E.Note)[0].text
    labels = of(actions, E.Labels)[0]
    assert labels.clear_bot and labels.add == [V.ETIQUETA_RECLUTAMIENTO]
    attrs = of(actions, E.Attrs)[0]
    assert attrs.replace and attrs.values["etapa"] == "En proceso: Bienvenida"
    assert "demostración de Adlek" in texts(actions)[0]


def test_medios_repite_la_pregunta():
    s, _ = talk(None, "hola", "1", "Juan Pérez")
    s, actions = E.handle_media(s, CONF)
    assert s.state == "EDAD"
    assert V.SOLO_TEXTO in texts(actions)[0]
    assert of(actions, E.SendOptions)[-1].options == V.OPC_EDAD


def test_privacidad_no_guarda_numeros():
    s, _ = talk(None, "hola", "1", "Juan Pérez", "25–35", "Apizaco", "Matutino")
    s, actions = talk(s, "mi curp es PEPJ900101HTLRRN09")
    assert s.state == "DOCUMENTOS"
    assert V.PRIVACIDAD in texts(actions)[0]
    assert "PEPJ" not in str(s.data)


def test_saludo_a_medio_flujo_no_reinicia():
    s, _ = talk(None, "hola", "1", "Juan Pérez")
    s, actions = talk(s, "hola")
    assert s.state == "EDAD"
    assert s.data["nombre"] == "Juan Pérez"


def test_seguimiento_por_abandono():
    s, actions = talk(None, "hola", "1", "Juan Pérez")
    assert of(actions, E.Schedule)[0].kind == E.SEGUIMIENTO
    s, actions = E.on_timer(s, E.SEGUIMIENTO, "EDAD", CONF)
    assert "¿Sigues ahí, Juan?" in texts(actions)[0]
    assert V.ETIQUETA_ABANDONO in of(actions, E.Labels)[0].add
    assert not of(actions, E.Schedule)  # un solo seguimiento por paso
    assert s.abandoned
    # Un segundo temporizador del mismo paso no manda nada
    s, again = E.on_timer(s, E.SEGUIMIENTO, "EDAD", CONF)
    assert again == []
    # Regresa: se quita la etiqueta y sigue
    s, actions = talk(s, "27")
    assert s.state == "MUNICIPIO"
    assert not s.abandoned
    assert any(isinstance(a, E.Labels) and V.ETIQUETA_ABANDONO in a.remove for a in actions)


def test_seguimiento_viejo_se_ignora():
    s, _ = talk(None, "hola", "1", "Juan Pérez", "27")
    s, actions = E.on_timer(s, E.SEGUIMIENTO, "EDAD", CONF)  # ya está en MUNICIPIO
    assert actions == []


def test_recordatorio_solo_si_sigue_agendado():
    s, _ = talk(None, "hola", "1", "Juan Pérez")
    s, actions = E.on_timer(s, E.RECORDATORIO, "AGENDADO", CONF)
    assert actions == []


def test_agendado_uno_no_reagenda():
    s, _ = happy_until_horario()
    s, _ = talk(s, "1", "si")
    s, actions = talk(s, "1")
    assert s.state == "AGENDADO"
    s, actions = talk(s, "necesito cambiar mi cita")
    assert s.state == "HORARIO"


def test_boton_viejo_en_nombre():
    s, actions = talk(None, "hola", "Quiero aplicar", "Quiero aplicar")
    assert s.state == "NOMBRE"
    assert "nombre" not in s.data


def test_mensajes_cortos():
    """Ninguna burbuja pasa de 4 líneas."""
    s = None
    script = ["hola", "2", "1", "Juan Pérez", "¿cuánto pagan?", "27", "vivo en Zacatelco",
              "xx", "noche", "Me falta alguno", "3 meses", "2", "si"]
    for m in script:
        s, actions = E.handle_text(s, m, CONF)
        for t in texts(actions):
            assert t.count("\n") + 1 <= 4, t
    s, actions = E.on_timer(s, E.RECORDATORIO, "AGENDADO", CONF)
    for t in texts(actions):
        assert t.count("\n") + 1 <= 4, t
