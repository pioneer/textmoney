"""Offline Material catalogue and explicitly requested, bounded Iconify searches."""

import asyncio
import json
import re
import time
from pathlib import Path
from xml.sax.saxutils import quoteattr

import httpx

from app.icons import MAX_INPUT, IconAsset, IconError, normalize_image

DATA = Path(__file__).parents[1] / "icon_catalogue"
PREFIXES = ("ic", "material-symbols", "simple-icons", "logos")
LICENSES = {
    "ic": (
        "Apache-2.0",
        "Material Design Authors",
        "https://github.com/material-icons/material-icons",
    ),
    "material-symbols": ("Apache-2.0", "Google", "https://github.com/google/material-design-icons"),
    "arcticons": (
        "CC-BY-SA-4.0",
        "Arcticons contributors",
        "https://github.com/Arcticons-Team/Arcticons",
    ),
    "simple-icons": (
        "CC0-1.0",
        "Simple Icons Collaborators",
        "https://github.com/simple-icons/simple-icons",
    ),
    "logos": ("CC0-1.0", "Gil Barbara and contributors", "https://github.com/gilbarbara/logos"),
}
LICENSE_URLS = {
    "Apache-2.0": "https://www.apache.org/licenses/LICENSE-2.0",
    "CC-BY-SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
}

# Alternative-language terms remain local unless the user presses online search.
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


def _asset(prefix: str, name: str, data: bytes, *, monochrome: bool) -> IconAsset:
    normalized, vector = normalize_image(data)
    license_id, author, source = LICENSES[prefix]
    return IconAsset(
        name,
        normalized,
        monochrome and vector,
        f"{source} · {prefix}:{name}",
        license_id,
        f"{author}. Normalized for display; original artwork attribution retained.",
        LICENSE_URLS[license_id],
        (DATA / f"{license_id}.txt").read_text(encoding="utf-8"),
    )


def bundled_icons(query: str = "") -> list[IconAsset]:
    catalogue = json.loads((DATA / "material.json").read_text(encoding="utf-8"))
    tokens = query.strip().casefold().split()
    result = []
    for name, icon in catalogue["icons"].items():
        short = name.removeprefix("outline-")
        haystack = f"{short.replace('-', ' ')} {ALIASES.get(short, '')}"
        if all(token in haystack for token in tokens):
            svg = (
                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{icon["body"]}</svg>'
            )
            result.append(_asset("ic", name, svg.encode(), monochrome=True))
    return result


def style_rank(identifier: str) -> tuple[int, str]:
    prefix, _, name = identifier.partition(":")
    if (
        prefix == "ic"
        and name.startswith("outline-")
        or prefix == "material-symbols"
        and "outline" in name
    ):
        return 0, identifier
    if prefix in {"ic", "material-symbols"}:
        return 1, identifier
    if prefix == "simple-icons":
        return 2, identifier
    return 3, identifier


class IconifySearch:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport
        self.cache: dict[str, list[IconAsset]] = {}
        self.next_request = 0.0
        self.lock = asyncio.Lock()

    async def _get(self, client: httpx.AsyncClient, path: str, **params: str) -> bytes:
        try:
            async with client.stream("GET", path, params=params) as response:
                if response.status_code == 429:
                    self.next_request = time.monotonic() + 60
                    raise IconError("icon_rate_limited")
                if not response.is_success:
                    raise IconError("icon_online_failed")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_INPUT:
                        raise IconError("icon_size")
                return bytes(data)
        except httpx.HTTPError as exc:
            raise IconError("icon_online_failed") from exc

    async def search(self, query: str) -> list[IconAsset]:
        query = query.strip()
        if not query or len(query) > 120 or any(ord(char) < 32 for char in query):
            raise IconError("icon_query_invalid")
        async with self.lock:
            if query in self.cache:
                return list(self.cache[query])
            if time.monotonic() < self.next_request:
                raise IconError("icon_rate_limited")
            self.next_request = time.monotonic() + 2
            async with httpx.AsyncClient(
                base_url="https://api.iconify.design",
                timeout=15,
                follow_redirects=False,
                headers={
                    "User-Agent": "TextMoney icon picker (https://github.com/pioneer/textmoney)"
                },
                transport=self.transport,
            ) as client:
                try:
                    payload = json.loads(
                        await self._get(
                            client, "/search", query=query, prefixes=",".join(PREFIXES), limit="64"
                        )
                    )
                    names = payload["icons"]
                    if (
                        not isinstance(names, list)
                        or len(names) > 100
                        or any(
                            not isinstance(name, str)
                            or not re.fullmatch(r"[a-z0-9-]+:[a-z0-9-]+", name)
                            for name in names
                        )
                    ):
                        raise IconError("icon_online_failed")
                    names = sorted(
                        (name for name in names if name.split(":")[0] in PREFIXES), key=style_rank
                    )[:32]
                    results: list[IconAsset] = []
                    rejected = 0
                    for prefix in PREFIXES:
                        selected = [
                            name.split(":")[1] for name in names if name.startswith(prefix + ":")
                        ]
                        if not selected:
                            continue
                        collection = json.loads(
                            await self._get(client, f"/{prefix}.json", icons=",".join(selected))
                        )
                        width, height = collection.get("width", 24), collection.get("height", 24)
                        for name in selected:
                            icon = collection["icons"].get(name)
                            if not isinstance(icon, dict):
                                continue
                            svg = (
                                '<svg xmlns="http://www.w3.org/2000/svg" viewBox='
                                + quoteattr(
                                    f"0 0 {icon.get('width', width)} {icon.get('height', height)}"
                                )
                                + f">{icon['body']}</svg>"
                            )
                            try:
                                results.append(
                                    await asyncio.to_thread(
                                        _asset,
                                        prefix,
                                        name,
                                        svg.encode(),
                                        monochrome=prefix != "logos",
                                    )
                                )
                            except IconError:
                                rejected += 1
                    if not results and rejected:
                        raise IconError("icon_no_suitable")
                    results.sort(key=lambda asset: style_rank(asset.source.rsplit(" · ", 1)[-1]))
                except (KeyError, ValueError, TypeError) as exc:
                    raise IconError("icon_online_failed") from exc
            if len(self.cache) >= 20:
                self.cache.pop(next(iter(self.cache)))
            self.cache[query] = results
            return list(results)
