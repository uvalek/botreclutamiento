"""Datos de la vacante y TODOS los textos que ve el candidato.

Única fuente de verdad: si hay que cambiar un texto o un dato de la vacante,
se cambia aquí. El bot nunca responde con datos que no estén en este archivo.

Formato: Markdown. Chatwoot lo convierte a formato de WhatsApp
(**negritas** → *negritas*, _cursiva_ → _cursiva_). Evitar líneas que
empiecen con "+", "-", "*", "#" o ">" porque Markdown las vuelve listas.
Máximo 4 líneas por burbuja.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Datos de la vacante (ficticios)
# ---------------------------------------------------------------------------

EMPRESA = "Componentes del Centro"
PUESTO = "Operador de producción"
VACANTE_RESUMEN = "Operador de producción · Planta Huamantla"
# Lugar de las entrevistas (debe coincidir con LOCATION_URL).
LUGAR_ENTREVISTA = "Centro de Convenciones de Tlaxcala"

# ---------------------------------------------------------------------------
# Opciones (títulos de botones: máximo 20 caracteres; filas de lista: 24)
# ---------------------------------------------------------------------------

OPC_BIENVENIDA = ["Quiero aplicar", "Sueldo y beneficios"]
OPC_INFO = ["Quiero aplicar", "Por ahora no"]
OPC_EDAD = ["18–24", "25–35", "36–45", "46 o más"]
OPC_MUNICIPIO = ["Huamantla", "Apizaco", "Tlaxcala", "Otro"]
OPC_TURNO = ["Matutino", "Vespertino", "Nocturno", "Cualquiera"]
OPC_DOCUMENTOS = ["Sí, todos", "Me falta alguno"]
OPC_EXPERIENCIA = ["Más de 6 meses", "Menos de 6 meses", "Sin experiencia"]
OPC_CONFIRMACION = ["Sí, confirmar", "Cambiar horario"]
OPC_RECORDATORIO = ["Confirmo asistencia", "Cambiar horario"]
OPC_OFRECER_ASESOR = ["Hablar con asesor", "Seguir aquí"]

# Municipios con ruta de transporte (el resto se clasifica "fuera de ruta").
MUNICIPIOS_CON_RUTA = ["Huamantla", "Apizaco", "Tlaxcala"]

# ---------------------------------------------------------------------------
# Mensajes del flujo
# ---------------------------------------------------------------------------

BIENVENIDA_1 = (
    f"¡Hola! 👋 Soy el asistente de reclutamiento de **{EMPRESA}**.\n"
    "_Esto es una demostración de Adlek: la empresa y la vacante son ficticias._"
)
BIENVENIDA_2 = (
    f"Tenemos vacante de **{PUESTO}** en planta Huamantla, Tlaxcala.\n"
    "¿Qué te gustaría hacer?"
)
BIENVENIDA_REPREGUNTA = "¿Qué te gustaría hacer?"

INFO_TARJETA = (
    "💰 **$2,450 semanales** más bono de puntualidad y asistencia\n"
    "🚌 Transporte de personal: rutas Huamantla, Apizaco y Tlaxcala\n"
    "🍽️ Comedor y prestaciones de ley desde el primer día\n"
    "🕐 Turnos matutino, vespertino y nocturno"
)
INFO_PREGUNTA = "¿Te gustaría aplicar?"

NOMBRE_PREGUNTA = "¡Excelente! Para empezar, ¿cuál es tu **nombre completo**?"
NOMBRE_REPREGUNTA = "¿Me compartes tu **nombre completo**? Por ejemplo: Juan Pérez López"

EDAD_PREGUNTA_PRIMERA = "Mucho gusto, {nombre} 🙂\n¿En qué rango de edad estás?"
EDAD_PREGUNTA = "¿En qué rango de edad estás?"
MUNICIPIO_PREGUNTA = "¿En qué municipio vives?"
TURNO_PREGUNTA = "¿Qué turno prefieres?"
DOCUMENTOS_PREGUNTA = (
    "¿Tienes estos documentos?\n"
    "INE, CURP, NSS y comprobante de domicilio.\n"
    "_Solo dime si los tienes; no me mandes números ni fotos._"
)
EXPERIENCIA_PREGUNTA = "¿Tienes experiencia en producción o manufactura?"

CLASIF_CALIFICA = "¡Cumples con el perfil! 🙌 Vamos a agendar tu entrevista."
CLASIF_FALTAN_DOCS = (
    "Si te falta algún documento no hay problema: agenda y llévalo el día de tu entrevista."
)
CLASIF_FUERA_RUTA = (
    "Tu municipio no está en nuestras rutas de transporte, "
    "pero puedes agendar y lo revisamos en tu entrevista."
)

HORARIO_PREGUNTA = f"Elige el horario de tu entrevista en el **{LUGAR_ENTREVISTA}**:"
HORARIO_PREGUNTA_REAGENDA = "Sin problema. Elige tu nuevo horario de entrevista:"
# Agenda con Cal.com (dos pasos: día → hora)
DIA_PREGUNTA = f"¿Qué día te acomoda para tu entrevista en el **{LUGAR_ENTREVISTA}**?"
DIA_PREGUNTA_REAGENDA = "Sin problema. ¿Qué día te acomoda para tu nueva entrevista?"
HORA_PREGUNTA = "El **{dia}** tengo disponible {rangos}.\n¿A qué hora te acomoda?"
HORA_OCUPADA = "A esa hora ya no tengo lugar 😕"

# Correo opcional (solo cuando se agenda con Cal.com)
OPC_CORREO = ["Sin correo"]
CORREO_PREGUNTA = (
    "¿Quieres que también te llegue la confirmación a tu correo?\n"
    "Escríbelo aquí o toca **Sin correo**."
)
CORREO_NO_VALIDO = "Mmm, ese correo no parece válido 🤔"


CONFIRMACION_PREGUNTA = (
    "Confirma tus datos:\n"
    "👤 {nombre}{correo}\n"
    f"📅 {{horario}} · {LUGAR_ENTREVISTA}\n"
    "📄 Lleva INE y solicitud de empleo"
)

AGENDADO = (
    "¡Listo, {nombre}! ✅ Tu entrevista quedó para el **{horario}**.\n"
    "Te enviaré un recordatorio un día antes."
)
AGENDADO_DEMO = "_En esta demo el recordatorio llega en {espera}._"
AGENDADO_OTRO_TEXTO = (
    f"Tu entrevista es el **{{horario}}** en el {LUGAR_ENTREVISTA}.\n"
    "Si necesitas algo, escribe **asesor**."
)

RECORDATORIO_ENCABEZADO_DEMO = "⏰ _Recordatorio (en la vida real llega un día antes)_"
RECORDATORIO_ENCABEZADO = "⏰ Recordatorio de tu entrevista"
RECORDATORIO = (
    "{encabezado}\n"
    f"{{nombre}}, te esperamos el **{{horario}}** en el {LUGAR_ENTREVISTA}. "
    "Lleva INE y solicitud de empleo.\n"
    "Responde **1** para confirmar tu asistencia o **2** para cambiar el horario."
)
RECORDATORIO_REPREGUNTA = (
    "Responde **1** para confirmar tu asistencia o **2** para cambiar el horario."
)

CONFIRMADO = (
    "¡Gracias, {nombre}! 🙌 Te esperamos el **{horario}**.\n"
    "Recuerda llevar INE y solicitud de empleo."
)
CONFIRMADO_OTRO_TEXTO = f"Te esperamos el **{{horario}}** en el {LUGAR_ENTREVISTA} 🙌"
TIP_REINICIAR = "Demo: escribe **reiniciar** para verla desde el inicio."

NO_INTERESADO = (
    "Sin problema 🙂 Si cambias de opinión, escríbeme **aplicar** y seguimos.\n"
    "¡Mucho éxito!"
)
NO_INTERESADO_OTRO_TEXTO = "Si cambias de opinión, escríbeme **aplicar** y seguimos 🙂"

ASESOR = "Te comunico con una persona de Recursos Humanos 🙋\nEn breve te responde por aquí."
REGRESO_DE_ASESOR = "¡Hola de nuevo! 👋 Sigamos donde nos quedamos."

SOLO_TEXTO = "Por ahora solo puedo leer texto ✍️"
NO_ENTENDI = "No te entendí 🙏 Elige una opción o escribe el número."
NO_ENTENDI_NOMBRE = "No te entendí 🙏"
OFRECER_ASESOR = (
    "Parece que no logro entenderte 😅\n"
    "¿Quieres que te atienda una persona de Recursos Humanos?"
)
SEGUIR_AQUI = "¡Va! Sigamos 🙂"
SALUDO_A_MEDIO_FLUJO = "¡Hola! 👋 Sigamos con tu solicitud."
PRIVACIDAD = "🔒 No necesito tus números ni fotos de documentos, solo saber si los tienes."

SEGUIMIENTO_CON_NOMBRE = (
    "¿Sigues ahí, {nombre}? 🙂 Tu solicitud quedó a medias.\nCuando quieras, seguimos:"
)
SEGUIMIENTO_SIN_NOMBRE = "¿Sigues ahí? 🙂 Tu solicitud quedó a medias.\nCuando quieras, seguimos:"

UBICACION_ENLACE = "📍 Aquí tienes la ubicación para llegar:\n{url}"

# ---------------------------------------------------------------------------
# Preguntas frecuentes: respuestas fijas (nunca se generan)
# ---------------------------------------------------------------------------

FAQ_RESPUESTAS = {
    "sueldo": "💰 El sueldo es de **$2,450 semanales** más bono de puntualidad y asistencia.",
    "prestaciones": "🍽️ Tienes comedor, transporte de personal y prestaciones de ley desde el primer día.",
    "transporte": "🚌 Hay transporte de personal con rutas desde Huamantla, Apizaco y Tlaxcala.",
    "turnos": "🕐 Hay turnos matutino, vespertino y nocturno.",
    "ubicacion": f"📍 Las entrevistas son en el **{LUGAR_ENTREVISTA}**. La planta está en Huamantla.",
    "llevar": "📄 Para la entrevista lleva tu **INE** y tu **solicitud de empleo**.",
    "puesto": f"👷 La vacante es para **{PUESTO}** en la planta Huamantla.",
    "demo": (
        "🤖 Soy un asistente automático. Esta es una demostración de **Adlek**: "
        "la empresa y la vacante son ficticias."
    ),
}
FAQ_DESCONOCIDA = (
    "Ese dato te lo confirma Recursos Humanos en tu entrevista. "
    "Si quieres hablar con una persona, escribe **asesor**."
)

# ---------------------------------------------------------------------------
# Registro en Chatwoot
# ---------------------------------------------------------------------------

ETIQUETA_RECLUTAMIENTO = "reclutamiento"
ETIQUETA_CALIFICA = "califica"
ETIQUETA_REVISAR = "revisar"
ETIQUETA_AGENDADA = "entrevista-agendada"
ETIQUETA_CONFIRMO = "confirmó-asistencia"
ETIQUETA_CAMBIO = "cambio-de-horario"
ETIQUETA_ABANDONO = "abandonó"

ETIQUETAS_DEL_BOT = [
    ETIQUETA_RECLUTAMIENTO,
    ETIQUETA_CALIFICA,
    ETIQUETA_REVISAR,
    ETIQUETA_AGENDADA,
    ETIQUETA_CONFIRMO,
    ETIQUETA_CAMBIO,
    ETIQUETA_ABANDONO,
]

# Atributos personalizados de la conversación: clave → nombre visible.
ATRIBUTOS = {
    "candidato_nombre": "Nombre del candidato",
    "candidato_edad": "Edad",
    "candidato_municipio": "Municipio",
    "candidato_turno": "Turno",
    "candidato_documentos": "Documentos",
    "candidato_experiencia": "Experiencia",
    "candidato_correo": "Correo",
    "clasificacion": "Clasificación",
    "entrevista_horario": "Horario de entrevista",
    "entrevista_asistencia": "Asistencia",
    "etapa": "Etapa",
    "vacante": "Vacante",
}

# Nombre legible de cada paso (para la etapa y las notas de RH).
NOMBRE_PASO = {
    "INICIO": "Inicio",
    "BIENVENIDA": "Bienvenida",
    "INFO": "Información de la vacante",
    "NOMBRE": "Nombre",
    "EDAD": "Edad",
    "MUNICIPIO": "Municipio",
    "TURNO": "Turno",
    "DOCUMENTOS": "Documentos",
    "EXPERIENCIA": "Experiencia",
    "HORARIO": "Horario",
    "CORREO": "Correo",
    "CONFIRMACION": "Confirmación",
    "AGENDADO": "Entrevista agendada",
    "RECORDATORIO": "Recordatorio",
    "CONFIRMADO": "Confirmó asistencia",
    "NO_INTERESADO": "No interesado",
    "OFRECER_ASESOR": "Oferta de asesor",
    "ASESOR": "Con asesor",
}
