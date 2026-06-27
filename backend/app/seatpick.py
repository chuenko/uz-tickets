"""Вибір місць за пріоритетом для автоброні.

Працює лише коли увімкнена автобронь. На вхід — множина ВІЛЬНИХ номерів місць
у вагоні + тип вагона + скільки треба. На вихід — обрані місця за пріоритетом.

Типи:
  kupe        — Купе (36): нижні непарні 5–31 → верхні парні 6–32 → решта
  plats       — Плацкарт (54): як купе, бокові (37–54) лише в крайньому разі
  intercity1  — ІС 1 клас (56): спершу біля вікна (4k+1, 4k+4), тоді прохід
  intercity2  — ІС 2 клас (80): спершу біля вікна (5k+2, 5k+3), тоді решта
"""
from typing import Iterable


# ── Пріоритетні послідовності номерів (від найкращого) ──
def _kupe_priority() -> list[int]:
    odd = list(range(5, 32, 2))      # 5,7,…,31  нижні
    even = list(range(6, 33, 2))     # 6,8,…,32  верхні
    rest = [n for n in range(1, 37) if n not in odd and n not in even]  # 1–4, 33–36
    return odd + even + rest


def _plats_priority() -> list[int]:
    odd = list(range(5, 32, 2))
    even = list(range(6, 33, 2))
    main_rest = [n for n in range(1, 37) if n not in odd and n not in even]
    side = list(range(37, 55))       # бокові — в останню чергу
    return odd + even + main_rest + side


def _intercity1_priority() -> list[int]:
    window = [n for n in range(1, 57) if n % 4 in (0, 1)]   # 1,4,5,8,…,53,56
    aisle = [n for n in range(1, 57) if n % 4 in (2, 3)]    # 2,3,6,7,…
    return window + aisle


def _intercity2_priority() -> list[int]:
    window = [n for n in range(1, 82) if n % 5 in (2, 3)]   # 2,3,7,8,…,77,78
    rest = [n for n in range(1, 82) if n % 5 not in (2, 3)]
    return window + rest


_PRIORITY = {
    "kupe": _kupe_priority(),
    "plats": _plats_priority(),
    "intercity1": _intercity1_priority(),
    "intercity2": _intercity2_priority(),
}

# розмір «купе» (для сусідства) — у купе/плацкарті по 4 місця в відсіку
_COMPARTMENT = {"kupe": 4, "plats": 4}


def _compartment(seat: int) -> int:
    return (seat - 1) // 4


def pick_seats(free: Iterable[int], kind: str, qty: int = 1) -> list[int]:
    """Повертає до `qty` обраних місць за пріоритетом, або менше якщо бракує."""
    free = set(free)
    pr = _PRIORITY.get(kind)
    if pr is None:
        return []
    avail = [n for n in pr if n in free]      # вільні, відсортовані за пріоритетом
    if qty <= 1:
        return avail[:1]

    # ── кілька місць: спершу пробуємо поруч (для купе/плацкарта — один відсік) ──
    if kind in _COMPARTMENT:
        from collections import defaultdict
        by_comp: dict[int, list[int]] = defaultdict(list)
        for n in avail:
            by_comp[_compartment(n)].append(n)
        # 1) цілий відсік / достатньо місць в одному відсіку (за пріоритетом)
        for comp in sorted(by_comp, key=lambda c: pr.index(by_comp[c][0])):
            if len(by_comp[comp]) >= qty:
                return by_comp[comp][:qty]
        # 2) добираємо із сусідніх відсіків — беремо найкращі за пріоритетом
        return avail[:qty]

    # ── інтерсіті: беремо поспіль за пріоритетом (вікно йде першим) ──
    return avail[:qty]
