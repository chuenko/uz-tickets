"""Парсинг відповіді UZ /api/v3/trips у зручну структуру.

Стійкий до різних форматів: список або {direct|data|trips|list}, різні ключі
часу (depart_at / departure / from.date) і місць (wagon_classes / types / wagons).
"""
from datetime import datetime
from typing import Any

WAGON_TYPE_NAMES = {
    "П": "Плацкарт", "К": "Купе", "Л": "Люкс/СВ",
    "С": "Сидячий", "О": "Загальний", "М": "М'який",
}


def _items(data: Any) -> list:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("direct", "data", "trips", "list", "items"):
            v = data.get(key)
            if isinstance(v, list):
                return v
    return []


def _fmt_ts(ts) -> str:
    if ts in (None, "", 0):
        return "—"
    # epoch-секунди
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%d.%m %H:%M")
    except (ValueError, TypeError, OSError):
        pass
    # ISO-рядок
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).strftime("%d.%m %H:%M")
    except ValueError:
        return str(ts)


def _pick_time(raw: dict, *keys) -> Any:
    for k in keys:
        if k in raw and raw[k]:
            return raw[k]
    return None


def _wagon_list(train_obj: dict, raw: dict) -> list:
    for src in (train_obj, raw):
        for key in ("wagon_classes", "types", "wagons", "wagon_types"):
            v = src.get(key)
            if isinstance(v, list) and v:
                return v
    return []


def parse_trains(data: Any) -> list[dict]:
    trains = []
    for raw in _items(data):
        if not isinstance(raw, dict):
            continue
        train_obj = raw.get("train") if isinstance(raw.get("train"), dict) else raw
        number = str(train_obj.get("number") or train_obj.get("num") or raw.get("number") or "?")

        departure = _fmt_ts(_pick_time(raw, "depart_at", "departure", "from_date"))
        arrival = _fmt_ts(_pick_time(raw, "arrive_at", "arrival", "to_date"))

        seats = {}
        for wc in _wagon_list(train_obj, raw):
            if not isinstance(wc, dict):
                continue
            code = str(wc.get("id") or wc.get("letter") or wc.get("code") or "?")
            title = wc.get("name") or wc.get("title") or WAGON_TYPE_NAMES.get(code, code)
            count = int(wc.get("free_seats") or wc.get("places") or wc.get("free") or 0)
            price_raw = wc.get("price") or wc.get("cost") or 0
            try:
                price = round(float(price_raw) / 100)  # UZ віддає копійки
            except (ValueError, TypeError):
                price = 0
            seats[code] = {"title": title, "seats": count, "price": price}

        trains.append({
            "number": number,
            "departure": departure,
            "arrival": arrival,
            "seats": seats,
            "total_free": sum(s["seats"] for s in seats.values()),
        })
    return trains


def apply_wagon_filter(trains: list[dict], wagon_filter: str) -> list[dict]:
    """Лишає лише вагони з потрібними кодами (порожній фільтр = всі)."""
    codes = {c.strip().upper() for c in (wagon_filter or "").split(",") if c.strip()}
    if not codes:
        return trains
    out = []
    for t in trains:
        seats = {c: s for c, s in t["seats"].items() if c.upper() in codes}
        out.append({**t, "seats": seats, "total_free": sum(s["seats"] for s in seats.values())})
    return out


def seats_snapshot(trains: list[dict]) -> dict:
    """{ номер_поїзда: {код_вагона: к-сть_місць} } — для порівняння між перевірками."""
    return {
        t["number"]: {code: info["seats"] for code, info in t["seats"].items()}
        for t in trains
    }


def diff_seats(old: dict, new: dict) -> list[tuple]:
    """Усі зміни кількості місць: поява/зростання ('up') і спад ('down').

    Повертає (номер, код, було, стало, напрям). Старе -1 = вагон уперше бачимо
    (повідомляємо лише якщо вже є місця).
    """
    changes = []
    for num, new_seats in new.items():
        old_seats = old.get(num, {})
        for code, now in new_seats.items():
            was = old_seats.get(code, -1)
            if now == was:
                continue
            if was < 0:
                # вперше побачили вагон — цікаво лише якщо є місця
                if now > 0:
                    changes.append((num, code, was, now, "up"))
            else:
                changes.append((num, code, was, now, "up" if now > was else "down"))
    return changes
