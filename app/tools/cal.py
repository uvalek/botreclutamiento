"""Cal.com v2: horarios libres, agendar, reagendar y cancelar.

Basado en `app/tools/cal.py` de la plantilla (uvalek/chatbot). El bot SOLO
ofrece horarios que Cal.com dice que están libres, así que nunca agenda
fuera del horario de atención configurado en el tipo de evento.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import structlog

from app.config import get_settings

log = structlog.get_logger(__name__)

BASE = "https://api.cal.com/v2"
_DAYS = ["Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom"]
_MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def ready() -> bool:
    return get_settings().cal_ready


def _headers(version: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {get_settings().cal_api_key}",
        "cal-api-version": version,
        "Content-Type": "application/json",
    }


_DAYS_LONG = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_MONTHS_LONG = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
    "septiembre", "octubre", "noviembre", "diciembre",
]


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(
        ZoneInfo(get_settings().timezone)
    )


def fmt_time(minutes: int) -> str:
    """780 → '1:00 pm' (formato de 12 horas, como se dice en México)."""
    h, m = divmod(minutes % (24 * 60), 60)
    suffix = "am" if h < 12 else "pm"
    h12 = h % 12 or 12
    return f"{h12}:{m:02d} {suffix}"


def enrich(iso: str) -> dict[str, Any]:
    """Datos de un horario libre, en hora de México."""
    dt = _local(iso)
    minutes = dt.hour * 60 + dt.minute
    day_title = f"{_DAYS[dt.weekday()]} {dt.day} {_MONTHS[dt.month - 1]}"
    return {
        "start": iso,
        "day": dt.date().isoformat(),
        "day_title": day_title,  # "Jue 1 oct" (botón)
        "day_long": f"{_DAYS_LONG[dt.weekday()]} {dt.day} de {_MONTHS_LONG[dt.month - 1]}",
        "minutes": minutes,
        "time": fmt_time(minutes),  # "1:00 pm"
        "title": f"{day_title} {fmt_time(minutes)}",  # "Jue 1 oct 1:00 pm"
    }


def slot_title(iso: str) -> str:
    """'2026-10-01T19:00:00Z' → 'Jue 1 oct 1:00 pm' (hora de México)."""
    return enrich(iso)["title"]


def free_slots(starts: list[str], *, now: datetime, max_days: int) -> list[dict[str, Any]]:
    """Todos los horarios libres de los primeros `max_days` días con lugar
    (sin los que empiezan en menos de 1 hora)."""
    out: list[dict[str, Any]] = []
    days: list[str] = []
    for iso in sorted(set(starts)):
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        if dt <= now + timedelta(minutes=60):
            continue
        item = enrich(iso)
        if item["day"] not in days:
            if len(days) >= max_days:
                break
            days.append(item["day"])
        out.append(item)
    return out


async def get_slots() -> list[dict[str, Any]] | None:
    """Horarios libres de los próximos días. None si Cal.com falla."""
    s = get_settings()
    if not ready():
        return None
    now = datetime.now(UTC)
    params = {
        "eventTypeId": str(s.cal_event_type_id),
        "start": now.date().isoformat(),
        "end": (now + timedelta(days=s.cal_days_ahead)).date().isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(f"{BASE}/slots", params=params, headers=_headers("2024-09-04"))
    except httpx.HTTPError as e:
        log.warning("cal_slots_unreachable", error=str(e)[:160])
        return None
    if r.status_code >= 400:
        log.warning("cal_slots_error", status=r.status_code, body=r.text[:300])
        return None
    data = (r.json() or {}).get("data") or {}
    starts = [slot["start"] for day in data.values() for slot in (day or []) if slot.get("start")]
    return free_slots(starts, now=now, max_days=s.cal_max_days)


def attendee_email(phone: str | None, fallback: str) -> str:
    digits = "".join(c for c in (phone or "") if c.isdigit()) or fallback
    return f"wa{digits}@{get_settings().cal_attendee_email_domain}"


def normalize_phone(raw: str | None) -> str | None:
    """E.164 para Cal.com (México: +521XXXXXXXXXX → +52XXXXXXXXXX). De la plantilla."""
    if not raw:
        return None
    cleaned = "".join(c for c in str(raw) if c.isdigit() or c == "+")
    digits = cleaned.lstrip("+")
    if not digits.isdigit():
        return None
    if digits.startswith("521") and len(digits) == 13:
        digits = "52" + digits[3:]
    if len(digits) == 10:
        digits = "52" + digits
    if not (10 <= len(digits) <= 15):
        return None
    return "+" + digits


async def book(*, start: str, name: str, phone: str | None, ref: str) -> str | None:
    """Agenda y devuelve el uid de la cita, o None si falló (p. ej. se ocupó)."""
    s = get_settings()
    attendee: dict[str, Any] = {
        "name": name or "Candidato",
        "email": attendee_email(phone, ref),
        "timeZone": s.timezone,
        "language": "es",
    }
    e164 = normalize_phone(phone)
    if e164:
        attendee["phoneNumber"] = e164
    payload = {
        "eventTypeId": s.cal_event_type_id,
        "start": start,
        "attendee": attendee,
        "metadata": {"origen": "whatsapp-demo-reclutamiento", "ref": ref[:40]},
    }
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post(f"{BASE}/bookings", json=payload, headers=_headers("2024-08-13"))
    except httpx.HTTPError as e:
        log.warning("cal_book_unreachable", error=str(e)[:160])
        return None
    if r.status_code >= 400:
        log.warning("cal_book_error", status=r.status_code, body=r.text[:300])
        return None
    data = (r.json() or {}).get("data") or {}
    if isinstance(data, list):
        data = data[0] if data else {}
    return data.get("uid")


async def reschedule(uid: str, start: str) -> str | None:
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post(
                f"{BASE}/bookings/{uid}/reschedule",
                json={"start": start, "reschedulingReason": "Cambio desde WhatsApp"},
                headers=_headers("2024-08-13"),
            )
    except httpx.HTTPError as e:
        log.warning("cal_reschedule_unreachable", error=str(e)[:160])
        return None
    if r.status_code >= 400:
        log.warning("cal_reschedule_error", status=r.status_code, body=r.text[:300])
        return None
    data = (r.json() or {}).get("data") or {}
    return data.get("uid") or uid


async def cancel(uid: str, reason: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post(
                f"{BASE}/bookings/{uid}/cancel",
                json={"cancellationReason": reason},
                headers=_headers("2024-08-13"),
            )
    except httpx.HTTPError:
        return False
    return r.status_code < 400


async def log_event_types() -> None:
    """Si falta CAL_EVENT_TYPE_ID, registra en el log los tipos de evento (id y
    nombre) para configurarlo sin tener que ver la API key."""
    s = get_settings()
    if not s.cal_api_key or s.cal_event_type_id:
        return
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.get(f"{BASE}/event-types", headers=_headers("2024-06-14"))
        items = (r.json() or {}).get("data") or []
        for et in items if isinstance(items, list) else []:
            log.warning(
                "cal_event_type_available",
                id=et.get("id"), title=et.get("title"), slug=et.get("slug"),
                minutes=et.get("lengthInMinutes"),
            )
    except Exception as e:  # noqa: BLE001
        log.warning("cal_event_types_failed", error=str(e)[:160])
