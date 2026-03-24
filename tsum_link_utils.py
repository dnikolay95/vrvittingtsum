# -*- coding: utf-8 -*-
"""
Извлечение product ID из ссылки Tsum и получение данных по товару.
"""
import re
from typing import Optional, Dict, Any, List

import requests

TSUM_PRODUCT_API = "https://api.tsum.ru/v1/catalog/product/"


def extract_product_slug_prefix_from_url(url: str) -> Optional[str]:
    """
    Для ссылок вида https://www.tsum.ru/product/he00852863-sviter-.../
    числового id в URL нет (артикул/modelExtId в начале слага).
    Возвращает первый сегмент до дефиса: he00852863 — для поиска в HTML по modelExtId.
    """
    path = (url or "").strip().split("?")[0].split("#")[0].rstrip("/")
    m = re.search(r"/product/([^/?#]+)", path, re.IGNORECASE)
    if not m:
        return None
    first = (m.group(1) or "").split("-")[0].strip()
    if not first or len(first) < 3:
        return None
    # Буквы+цифры или только цифры (для нестандартных слагов)
    if re.match(r"^[A-Za-z]{0,4}\d+|^\d{5,}$", first):
        return first
    return None


def extract_product_id_from_url(url: str) -> Optional[str]:
    """
    Достаёт ID товара из URL Tsum.
    Поддерживает форматы:
    - .../13760679
    - .../product/13760679
    - .../7068545-slug-name (число в начале слага — может быть modelExtId, пробуем как id)
    """
    url = (url or "").strip()
    if not url or "tsum" not in url.lower():
        return None
    # Убираем query и якоря
    path = url.split("?")[0].split("#")[0]
    # Ищем числа 6-9 цифр (типичный id в Tsum)
    numbers = re.findall(r"\b(\d{6,9})\b", path)
    if not numbers:
        return None
    # Берём последнее число в path (часто id в конце)
    return numbers[-1]


def fetch_product_by_id(product_id: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Запрос к API Tsum по ID. Возвращает сырой JSON или None."""
    try:
        r = requests.get(
            TSUM_PRODUCT_API.rstrip("/") + "/" + str(product_id),
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "Accept": "application/json",
            },
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None


def _extract_product_id_from_html(url: str, fallback_number: Optional[str] = None, timeout: int = 10) -> Optional[str]:
    """
    Пытается достать product_id из HTML-страницы товара.
    Это нужно для ссылок вида
    https://www.tsum.ru/product/7088425-khlopkovaya-futbolka-.../
    где число в URL — это не product_id, а артикул/slug.
    """
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        if resp.status_code != 200:
            return None
        html = resp.text
    except Exception:
        return None

    slug_num = fallback_number

    # Если знаем число из URL (артикул), пробуем найти объект, где рядом modelExtId и id
    if slug_num:
        # TSUM в JSON часто хранит modelExtId в ВЕРХНЕМ регистре (HE00852863),
        # в URL слаг может быть ниже (he00852863).
        variants = list(
            dict.fromkeys(
                [slug_num, slug_num.upper(), slug_num.lower()]
            )
        )
        for sn in variants:
            esc = re.escape(sn)
            patterns = [
                rf'"id"\s*:\s*(\d+)[^{{}}]*"modelExtId"\s*:\s*"{esc}"',
                rf'"modelExtId"\s*:\s*"{esc}"[^{{}}]*"id"\s*:\s*(\d+)',
                rf"'id'\s*:\s*(\d+)[^{{}}]*'modelExtId'\s*:\s*'{esc}'",
                rf"'modelExtId'\s*:\s*'{esc}'[^{{}}]*'id'\s*:\s*(\d+)",
            ]
            for pat in patterns:
                m = re.search(pat, html, re.DOTALL | re.IGNORECASE)
                if m:
                    return m.group(1)
            blob = re.search(
                rf'"modelExtId"\s*:\s*"{esc}"[\s\S]{{0,500}}?"id"\s*:\s*(\d{{6,9}})',
                html,
                re.IGNORECASE,
            )
            if blob:
                return blob.group(1)
            blob2 = re.search(
                rf'"id"\s*:\s*(\d{{6,9}})[\s\S]{{0,500}}?"modelExtId"\s*:\s*"{esc}"',
                html,
                re.IGNORECASE,
            )
            if blob2:
                return blob2.group(1)

    # Фолбэк: берём первое правдоподобное \"id\": 123456 в JSON на странице
    m = re.search(r'"id"\s*:\s*(\d{6,9})', html)
    if m:
        return m.group(1)

    return None


def get_product_info_for_bot(product_id: str) -> Optional[Dict[str, Any]]:
    """
    По ID товара возвращает словарь для бота:
    - product_id, title, brand, w2000_1 (url первого фото), image_local_path (если скачали).
    Скачивание фото не делаем здесь — это делает tryon_processor.
    """
    raw = fetch_product_by_id(product_id)
    if not raw:
        return None
    title = raw.get("title") or ""
    brand = raw.get("brand")
    brand_name = brand.get("title") if isinstance(brand, dict) else ""
    images = raw.get("images") or []
    w2000_1 = None
    for img in images:
        if isinstance(img, dict) and img.get("w2000"):
            w2000_1 = img["w2000"]
            break
    return {
        "product_id": str(product_id),
        "title": title,
        "brand": brand_name,
        "w2000_1": w2000_1,
        "raw": raw,
    }


def get_product_id_and_info(link: str) -> Optional[Dict[str, Any]]:
    """
    По ссылке пытается получить product_id и данные товара.

    Алгоритм:
    1) Берём число из URL и считаем его product_id.
       Если API по нему отвечает — используем его.
    2) Если нет — скачиваем HTML и пытаемся найти настоящий product_id
       по связке id + modelExtId или по первому \"id\": в JSON на странице.
    """
    link = (link or "").strip()
    if not link:
        return None

    # 1. Пытаемся использовать число из URL как product_id
    pid_from_url = extract_product_id_from_url(link)
    slug_prefix = extract_product_slug_prefix_from_url(link)

    if pid_from_url:
        info = get_product_info_for_bot(pid_from_url)
        if info:
            info["product_link"] = link
            return info

    # 2. Пытаемся вытащить настоящий id из HTML (в т.ч. по артикулу/modelExtId в слаге)
    pid_html = _extract_product_id_from_html(
        link, fallback_number=pid_from_url or slug_prefix
    )
    if not pid_html:
        return None
    info = get_product_info_for_bot(pid_html)
    if not info:
        return None
    info["product_link"] = link
    return info
