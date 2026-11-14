"""Offline snapshots of the icon collections formerly searched online."""

import gzip
import json
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import NotRequired, TypedDict
from xml.sax.saxutils import quoteattr

from app.icons import IconAsset, IconError, normalize_image


class _Icon(TypedDict):
    body: str
    width: NotRequired[int | float]
    height: NotRequired[int | float]


class _Alias(TypedDict):
    parent: str


class _Collection(TypedDict):
    icons: dict[str, _Icon]
    aliases: NotRequired[dict[str, _Alias]]
    categories: NotRequired[dict[str, list[str]]]
    width: NotRequired[int | float]
    height: NotRequired[int | float]


@dataclass(frozen=True, slots=True)
class _Candidate:
    prefix: str
    name: str
    body: str
    width: int | float
    height: int | float
    searchable: str


DATA = Path(__file__).parents[1] / "icon_catalogue"
PREFIXES = ("ic", "material-symbols", "simple-icons", "logos")
MATERIAL_PREFIXES = {"ic", "material-symbols"}
COLLECTION_TERMS = {
    "ic": "material icons",
    "material-symbols": "material symbols",
    "simple-icons": "simple icons brand brands logo logos",
    "logos": "svg logos brand brands logo",
}
MAX_RESULTS = 100
LICENSES = {
    "ic": (
        "Apache-2.0",
        "Material Design Authors",
        "https://github.com/material-icons/material-icons",
    ),
    "material-symbols": (
        "Apache-2.0",
        "Google",
        "https://github.com/google/material-design-icons",
    ),
    "simple-icons": (
        "CC0-1.0",
        "Simple Icons Collaborators",
        "https://github.com/simple-icons/simple-icons",
    ),
    "logos": (
        "CC0-1.0",
        "Gil Barbara and contributors",
        "https://github.com/gilbarbara/logos",
    ),
}
LICENSE_URLS = {
    "Apache-2.0": "https://www.apache.org/licenses/LICENSE-2.0",
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
}
PREVIOUSLY_BUNDLED = (
    "outline-account-balance",
    "outline-account-balance-wallet",
    "outline-payments",
    "outline-credit-card",
    "outline-savings",
    "outline-home",
    "outline-shopping-cart",
    "outline-restaurant",
    "outline-local-cafe",
    "outline-directions-car",
    "outline-flight",
    "outline-train",
    "outline-local-hospital",
    "outline-school",
    "outline-pets",
    "outline-work",
    "outline-receipt-long",
    "outline-phone-android",
    "outline-sports-esports",
    "outline-fitness-center",
    "outline-card-giftcard",
    "outline-local-gas-station",
    "outline-checkroom",
    "outline-build",
    "outline-electric-bolt",
    "outline-water-drop",
    "outline-wifi",
    "outline-attach-money",
    "outline-trending-up",
    "outline-movie",
)
PREFERRED_ORDER = {name: index for index, name in enumerate(PREVIOUSLY_BUNDLED)}

ALIASES = {
    "account-balance": "bank банк",
    "account-balance-wallet": "wallet гаманець кошелек",
    "payments": "cash готівка наличные",
    "credit-card": "card картка карта",
    "savings": "save savings заощадження накопления",
    "home": "house житло дім дом",
    "shopping-cart": "groceries shopping продукти покупки магазин",
    "restaurant": "food їжа еда ресторан",
    "local-cafe": "coffee cafe кава кофе",
    "directions-car": "car transport авто транспорт",
    "flight": "travel plane подорожі літак самолет",
    "train": "train transport поїзд поезд",
    "local-hospital": "health medicine здоров'я ліки здоровье лекарства",
    "school": "education school освіта навчання образование",
    "pets": "pets тварини животные",
    "work": "work salary робота зарплата работа",
    "receipt-long": "bills fees commission рахунки комісія счета комиссия",
    "phone-android": "phone телефон",
    "sports-esports": "games ігри игры",
    "fitness-center": "sport gym спорт",
    "card-giftcard": "gift подарунки подарки",
    "local-gas-station": "fuel petrol пальне бензин",
    "checkroom": "clothes одяг одежда",
    "build": "repair ремонт",
    "electric-bolt": "electricity utilities електрика комунальні электричество",
    "water-drop": "water вода",
    "wifi": "internet інтернет интернет",
    "attach-money": "money currency гроші деньги",
    "trending-up": "income investments дохід інвестиції доход инвестиции",
    "movie": "entertainment cinema розваги кіно развлечения кино",
}


def _load_collection(prefix: str) -> _Collection:
    with gzip.open(DATA / f"{prefix}.json.gz", "rt", encoding="utf-8") as source:
        return json.load(source)


@lru_cache(maxsize=1)
def _candidates() -> tuple[_Candidate, ...]:
    candidates = []
    for prefix in PREFIXES:
        collection = _load_collection(prefix)
        aliases: dict[str, list[str]] = {}
        for alias, item in collection.get("aliases", {}).items():
            aliases.setdefault(item["parent"], []).append(alias)
        categories: dict[str, list[str]] = {}
        for category, names in collection.get("categories", {}).items():
            for name in names:
                categories.setdefault(name, []).append(category)
        for name, icon in collection["icons"].items():
            short = name.removeprefix("outline-")
            terms = [
                name,
                *aliases.get(name, []),
                *categories.get(name, []),
                ALIASES.get(short, ""),
                COLLECTION_TERMS[prefix],
            ]
            searchable = " ".join(
                term.casefold().replace("-", " ").replace("_", " ") for term in terms
            )
            candidates.append(
                _Candidate(
                    prefix,
                    name,
                    icon["body"],
                    icon.get("width", collection.get("width", 24)),
                    icon.get("height", collection.get("height", 24)),
                    searchable,
                )
            )
    return tuple(candidates)


def style_rank(prefix: str, name: str) -> int:
    if (
        prefix == "ic"
        and name.startswith("outline-")
        or prefix == "material-symbols"
        and "outline" in name
    ):
        return 0
    if prefix in MATERIAL_PREFIXES:
        return 1
    if prefix == "simple-icons":
        return 2
    return 3


def _rank(candidate: _Candidate) -> tuple[int, int, str]:
    if candidate.prefix == "ic" and candidate.name in PREFERRED_ORDER:
        return -1, PREFERRED_ORDER[candidate.name], f"{candidate.prefix}:{candidate.name}"
    return (
        style_rank(candidate.prefix, candidate.name),
        len(PREFERRED_ORDER),
        f"{candidate.prefix}:{candidate.name}",
    )


@lru_cache(maxsize=MAX_RESULTS)
def _asset(prefix: str, name: str, body: str, width: int | float, height: int | float) -> IconAsset:
    license_id, author, source = LICENSES[prefix]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox='
        + quoteattr(f"0 0 {width} {height}")
        + f">{body}</svg>"
    )
    normalized, vector = normalize_image(svg.encode())
    return IconAsset(
        name,
        normalized,
        prefix != "logos" and vector,
        f"{source} · {prefix}:{name}",
        license_id,
        f"{author}. Normalized for display; original artwork attribution retained.",
        LICENSE_URLS[license_id],
        (DATA / f"{license_id}.txt").read_text(encoding="utf-8"),
    )


def bundled_icons(query: str = "") -> list[IconAsset]:
    tokens = query.strip().casefold().replace("-", " ").replace("_", " ").split()
    matches = [
        candidate
        for candidate in _candidates()
        if all(token in candidate.searchable for token in tokens)
    ]
    matches.sort(key=_rank)
    result = []
    for candidate in matches:
        try:
            asset = _asset(
                candidate.prefix,
                candidate.name,
                candidate.body,
                candidate.width,
                candidate.height,
            )
        except IconError:
            continue
        result.append(asset)
        if len(result) == MAX_RESULTS:
            break
    return result


def search_icons(query: str, assigned: Iterable[IconAsset] = ()) -> list[IconAsset]:
    tokens = query.strip().casefold().replace("-", " ").replace("_", " ").split()
    result = []
    seen: set[IconAsset] = set()
    for asset in sorted(
        assigned, key=lambda item: (item.label.casefold(), item.source.casefold(), item.data)
    ):
        haystack = " ".join(
            value.casefold().replace("-", " ").replace("_", " ")
            for value in (asset.label, asset.source, asset.attribution)
        )
        if all(token in haystack for token in tokens) and asset not in seen:
            result.append(asset)
            seen.add(asset)
            if len(result) == MAX_RESULTS:
                return result
    for asset in bundled_icons(query):
        if asset not in seen:
            result.append(asset)
            seen.add(asset)
            if len(result) == MAX_RESULTS:
                break
    return result
