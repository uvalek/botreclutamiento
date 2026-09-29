import pytest

from app import vacante as V
from app.flow import faq
from app.flow.engine import (
    SYN,
    _match_municipio,
    _parse_age,
    _parse_experience,
    slot_synonyms,
)
from app.flow.matcher import (
    is_asesor,
    is_reset,
    looks_like_sensitive_id,
    match_option,
    normalize,
    parse_name,
)

SLOTS = ["Mié 30 sep 9:00", "Mié 30 sep 11:30", "Jue 1 oct 9:00"]


def test_normalize():
    assert normalize("¡En la MAÑANA! 🌞") == "en la manana"
    assert normalize("18–24") == "18 24"
    assert normalize("1️⃣") == "1"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Matutino", 0),
        ("matutino", 0),
        ("1", 0),
        ("opción 2", 1),
        ("en la mañana", 0),
        ("prefiero el turno de la noche", 2),
        ("matuino", 0),
        ("vespertno", 1),
        ("cualquiera", 3),
        ("me da igual", 3),
        ("el que sea", 3),
        ("si", None),
        ("banana", None),
    ],
)
def test_turno(text, expected):
    assert match_option(text, V.OPC_TURNO, SYN["TURNO"]) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("18–24", 0),
        ("18-24", 0),
        ("25 a 35", 1),
        ("27", 1),
        ("tengo 40 años", 2),
        ("46 o más", 3),
        ("60", 3),
        ("2", 1),
        ("17", None),
    ],
)
def test_edad(text, expected):
    assert match_option(text, V.OPC_EDAD, SYN["EDAD"], _parse_age) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Sí, todos", 0),
        ("si", 0),
        ("sí los tengo todos", 0),
        ("no me falta ninguno", 0),
        ("Me falta alguno", 1),
        ("me falta la curp", 1),
        ("no tengo todos", 1),
        ("todos menos el nss", 1),
        ("no", 1),
    ],
)
def test_documentos(text, expected):
    assert match_option(text, V.OPC_DOCUMENTOS, SYN["DOCUMENTOS"]) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Más de 6 meses", 0),
        ("2 años", 0),
        ("trabajé un año en una maquiladora", 0),
        ("año y medio", 0),
        ("3 meses", 1),
        ("menos de 6 meses", 1),
        ("no, solo 8 meses", 0),
        ("no", 2),
        ("nunca", 2),
        ("sin experiencia", 2),
        ("sí", None),
    ],
)
def test_experiencia(text, expected):
    assert match_option(text, V.OPC_EXPERIENCIA, SYN["EXPERIENCIA"], _parse_experience) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Huamantla", (0, "")),
        ("vivo en Apizaco", (1, "")),
        ("Tlaxcala", (2, "")),
        ("huamanta", (0, "")),
        ("Huamantla, Tlaxcala", (0, "")),
        ("4", (3, "")),
        ("Otro", (3, "")),
        ("vivo en Zacatelco", (3, "Zacatelco")),
        ("Chiautempan, Tlaxcala", (3, "Chiautempan")),
        ("san pablo del monte", (3, "San Pablo del Monte")),
        ("¿cuánto pagan?", None),
        ("hola", None),
        ("Quiero aplicar", None),
        ("no sé", None),
    ],
)
def test_municipio(text, expected):
    assert _match_municipio(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1", 0),
        ("Mié 30 sep 11:30", 1),
        ("el jueves", 2),
        ("miércoles a las 11", 1),
        ("a las 11:30", 1),
        ("miércoles", None),
        ("a las 9", None),
    ],
)
def test_horario(text, expected):
    assert match_option(text, SLOTS, slot_synonyms(SLOTS)) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Juan Pérez López", "Juan Pérez López"),
        ("me llamo juan perez", "Juan Perez"),
        ("Soy María de la Luz Hernández", "María de la Luz Hernández"),
        ("hola", None),
        ("¿cuánto pagan?", None),
        ("123", None),
        ("Quiero aplicar", None),
        ("sí", None),
    ],
)
def test_parse_name(text, expected):
    assert parse_name(text) == expected


def test_commands():
    assert is_reset("reiniciar")
    assert is_reset("Reiniciar ")
    assert not is_reset("quiero reiniciar mi vida")
    assert is_asesor("asesor")
    assert is_asesor("quiero hablar con un asesor")
    assert not is_asesor("trabajé de agente de seguridad")


def test_sensitive_ids():
    assert looks_like_sensitive_id("PEPJ900101HTLRRN09")
    assert looks_like_sensitive_id("mi nss es 12345678901")
    assert not looks_like_sensitive_id("tengo 27 años")


def test_faq_topics():
    assert faq.detect_topics("¿cuánto pagan?") == ["sueldo"]
    assert faq.detect_topics("hay transporte?") == ["transporte"]
    assert faq.detect_topics("¿dónde es la entrevista?") == ["ubicacion"]
    assert faq.detect_topics("¿qué tengo que llevar?") == ["llevar"]
    assert faq.detect_topics("¿esto es real?") == ["demo"]
    assert faq.detect_topics("cuánto pagan y hay transporte") == ["sueldo", "transporte"]
    assert faq.answer("¿dan vales de despensa?") is None
    assert faq.looks_like_question("¿dan vales de despensa?")
