"""Interpreta lo que escribe el candidato y lo convierte en una opción.

Sin IA: reglas deterministas, rápidas y predecibles. Orden de intentos:
1. Coincidencia exacta con el título de la opción o un sinónimo.
2. Número de opción ("1", "opción 2", "la 3", "2️⃣").
3. Intérprete especial del paso (edad escrita, "2 años" de experiencia…).
4. Frases contenidas en el texto ("prefiero el turno de la noche").
5. Errores de dedo ("matuino" → Matutino).
Si nada coincide (o hay empate), devuelve None y el flujo dice "no entendí".
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from difflib import SequenceMatcher

# Caracteres invisibles usados para esconder texto (mismo criterio que
# input_guard.sanitize de la plantilla): tag chars, zero-width y bidi.
_INVISIBLE = re.compile("[\U000e0000-\U000e007f​-‏‪-‮⁠-⁤﻿]")
_NON_ALNUM = re.compile(r"[^a-z0-9ñ ]+")
_SPACES = re.compile(r"\s+")

_FUZZY_RATIO = 0.85


def sanitize(text: str | None) -> str:
    """Quita caracteres invisibles y espacios sobrantes (conserva el texto)."""
    if not text:
        return ""
    return _INVISIBLE.sub("", unicodedata.normalize("NFC", text)).strip()


def normalize(text: str | None) -> str:
    """Minúsculas, sin acentos, sin emojis ni signos, espacios simples.

    "¡En la MAÑANA! 🌞" → "en la manana"
    """
    if not text:
        return ""
    t = sanitize(text).lower()
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace("ñ", "n")
    t = _NON_ALNUM.sub(" ", t)
    return _SPACES.sub(" ", t).strip()


def _contains(haystack: str, phrase: str) -> bool:
    return f" {phrase} " in f" {haystack} "


Parser = Callable[[str], int | None]


def match_option(
    text: str,
    options: Sequence[str],
    synonyms: dict[int, Sequence[str]] | None = None,
    parser: Parser | None = None,
    allow_partial: bool = True,
) -> int | None:
    """Devuelve el índice de la opción elegida o None.

    `allow_partial=False` desactiva la búsqueda de frases dentro del texto
    (se usa en municipio: "Chiautempan, Tlaxcala" no debe contar como Tlaxcala).
    """
    n = normalize(text)
    if not n:
        return None

    phrases: dict[int, list[str]] = {}
    for i, title in enumerate(options):
        items = [normalize(title)]
        for s in (synonyms or {}).get(i, []):
            items.append(normalize(s))
        phrases[i] = [p for p in dict.fromkeys(items) if p]

    # 1. Exacto (si la frase sirve para varias opciones, es ambigua)
    exact = [i for i, items in phrases.items() if n in items]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None

    # 2. Número de opción
    m = re.fullmatch(r"(?:opcion|opc|la|el|numero|num)?\s*(\d{1,2})", n)
    if m:
        idx = int(m.group(1)) - 1
        if 0 <= idx < len(options):
            return idx

    # 3. Intérprete especial del paso
    if parser:
        idx = parser(n)
        if idx is not None:
            return idx

    # 4. Frases contenidas: gana la opción con más texto coincidente
    scores: dict[int, int] = {}
    if not allow_partial:
        phrases_for_partial: dict[int, list[str]] = {}
    else:
        phrases_for_partial = phrases
    for i, items in phrases_for_partial.items():
        score = sum(len(p) for p in items if _contains(n, p))
        if score:
            scores[i] = score
    if scores:
        best = max(scores.values())
        winners = [i for i, s in scores.items() if s == best]
        if len(winners) == 1:
            return winners[0]
        return None  # empate: mejor preguntar de nuevo

    # 5. Errores de dedo (frase completa o palabra suelta de 5+ letras)
    best_i: int | None = None
    best_r = 0.0
    tie = False
    words = [w for w in n.split() if len(w) >= 5] if allow_partial else []
    for i, items in phrases.items():
        for p in items:
            candidates = [n] + (words if " " not in p else [])
            for c in candidates:
                r = SequenceMatcher(None, c, p).ratio()
                if r > best_r + 1e-9:
                    best_i, best_r, tie = i, r, False
                elif abs(r - best_r) < 1e-9 and best_i != i:
                    tie = True
    if best_i is not None and best_r >= _FUZZY_RATIO and not tie:
        return best_i
    return None


def contains_any(text_normalized: str, phrases: Sequence[str]) -> bool:
    return any(_contains(text_normalized, normalize(p)) for p in phrases)


# ---------------------------------------------------------------------------
# Comandos globales
# ---------------------------------------------------------------------------

_RESET_WORDS = {"reiniciar", "reinicia", "reiniciar demo", "reset"}


def is_reset(text: str) -> bool:
    return normalize(text) in _RESET_WORDS


def is_asesor(text: str) -> bool:
    n = normalize(text)
    return contains_any(
        n,
        [
            "asesor",
            "asesora",
            "asesores",
            "hablar con alguien",
            "hablar con una persona",
            "hablar con un humano",
            "con un humano",
            "una persona real",
        ],
    )


_GREETINGS = {
    "hola",
    "holi",
    "ola",
    "buenas",
    "buen dia",
    "buenos dias",
    "buenas tardes",
    "buenas noches",
    "hey",
    "que tal",
    "hola buenas",
    "hola buen dia",
    "hola buenos dias",
    "hola buenas tardes",
    "hola buenas noches",
    "saludos",
}


def is_greeting(text: str) -> bool:
    return normalize(text) in _GREETINGS


# CURP (18 caracteres), NSS (11 dígitos), clave de elector (18) o números
# largos: el candidato está mandando datos que NO pedimos.
_CURP = re.compile(r"[A-Z]{4}\d{6}[HM][A-Z]{5}[A-Z0-9]\d", re.IGNORECASE)
_LONG_NUMBER = re.compile(r"\d{10,}")


def looks_like_sensitive_id(text: str) -> bool:
    t = sanitize(text)
    if any(_CURP.fullmatch(tok) for tok in re.split(r"[\s,;:.]+", t) if tok):
        return True
    return bool(_LONG_NUMBER.search(re.sub(r"[\s-]", "", t)))


# ---------------------------------------------------------------------------
# Nombre
# ---------------------------------------------------------------------------

_NAME_PREFIXES = re.compile(
    r"^(hola\s*,?\s*)?(me llamo|mi nombre es|mi nombre completo es|mi nombre|nombre|soy)\s*:?\s*",
    re.IGNORECASE,
)
_NAME_STOP = {
    "si",
    "no",
    "ok",
    "va",
    "gracias",
    "aplicar",
    "quiero aplicar",
    "sueldo y beneficios",
    "por ahora no",
    "nada",
    "ninguno",
    "no se",
    "prueba",
    "test",
}
_LOWER_PARTICLES = {"de", "del", "la", "las", "los", "y", "van", "von"}
_LETTERS_ONLY = re.compile(r"^[^\W\d_]+(?:[ '\-.][^\W\d_]+)*$", re.UNICODE)


def parse_name(text: str) -> str | None:
    """Extrae un nombre razonable o None si no parece un nombre."""
    t = sanitize(text)
    if not t or "?" in t or "¿" in t:
        return None
    t = _NAME_PREFIXES.sub("", t).strip(" .,!¡:;")
    t = _SPACES.sub(" ", t)
    if len(t) < 2 or len(t) > 60:
        return None
    if not _LETTERS_ONLY.match(t):
        return None
    words = t.split(" ")
    if len(words) > 6:
        return None
    n = normalize(t)
    if n in _NAME_STOP or n in _GREETINGS:
        return None
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if i > 0 and lw in _LOWER_PARTICLES:
            out.append(lw)
        else:
            out.append(lw[:1].upper() + lw[1:])
    return " ".join(out)


def first_name(full_name: str | None) -> str:
    return (full_name or "").split(" ")[0]
