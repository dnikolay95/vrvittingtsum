import csv
import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


PRODUCT_IDS_FILE = "productids.txt"
OUTPUT_CSV_FILE = "product_info.csv"
TSUM_PRODUCT_URL = "https://api.tsum.ru/v1/catalog/product/"


def read_product_ids(path: str) -> List[str]:
    """Читает ID товаров из текстового файла (по одному ID на строку)."""
    file_path = Path(path)
    if not file_path.exists():
        print(f"[WARN] Файл с ID товаров не найден: {path}")
        return []

    ids: List[str] = []
    with file_path.open("r", encoding="utf-8") as f:
        for line in f:
            value = line.strip()
            if value:
                ids.append(value)
    return ids


def fetch_product(id_value: str, timeout: int = 10) -> Optional[Dict[str, Any]]:
    """Запрашивает данные по товару из API TSUM.

    Допущение: API не требует авторизации и возвращает JSON-объект товара.
    """
    url = TSUM_PRODUCT_URL.rstrip("/") + "/" + str(id_value)
    try:
        # Допущение: достаточно "browser-like" User-Agent и accept JSON
        resp = requests.get(
            url,
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
            },
        )
        if resp.status_code != 200:
            print(f"[ERROR] ID {id_value}: HTTP {resp.status_code}")
            return None
        try:
            return resp.json()
        except json.JSONDecodeError:
            print(f"[ERROR] ID {id_value}: не удалось распарсить JSON")
            return None
    except requests.RequestException as e:
        print(f"[ERROR] ID {id_value}: ошибка сети: {e}")
        return None


def extract_product_info(raw: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Достаёт нужные поля из ответа API.

    Ожидаемые поля:
      - title
      - color.title
      - первое и второе значение по ключу 'w2000'
      - information.properties[*] при label == "Состав" → value
    """
    title = raw.get("title")

    # Категория товара
    category_title: Optional[str] = None
    category = raw.get("category")
    if isinstance(category, dict):
        category_title = category.get("title")

    color_title: Optional[str] = None
    color = raw.get("color")
    if isinstance(color, dict):
        color_title = color.get("title")

    # Берём первые три значения w2000 из массива images
    w2000_first: Optional[str] = None
    w2000_second: Optional[str] = None
    w2000_third: Optional[str] = None
    images = raw.get("images")
    if isinstance(images, list):
        urls: List[str] = []
        for img in images:
            if isinstance(img, dict):
                val = img.get("w2000")
                if isinstance(val, str):
                    urls.append(val)
        if len(urls) >= 1:
            w2000_first = urls[0]
        if len(urls) >= 2:
            w2000_second = urls[1]
        if len(urls) >= 3:
            w2000_third = urls[2]

    composition: Optional[str] = None
    size_info1: Optional[str] = None
    size_info2: Optional[str] = None
    information = raw.get("information")

    def _extract_composition_from_props(props_val: Any) -> Optional[str]:
        if isinstance(props_val, list):
            for prop in props_val:
                if not isinstance(prop, dict):
                    continue
                if prop.get("label") == "Состав":
                    return prop.get("value")
        return None

    def _extract_sizes_from_block(block: Dict[str, Any]):
        """Извлекает первые 2 value из properties блока sizes"""
        props = block.get("properties")
        if not isinstance(props, list):
            return None, None
        
        values = []
        for prop in props:
            if isinstance(prop, dict):
                value = prop.get("value")
                if value:
                    values.append(str(value))
        
        # Берем максимум первые 2
        info1 = values[0] if len(values) >= 1 else None
        info2 = values[1] if len(values) >= 2 else None
        return info1, info2

    # В API information может быть объектом или списком блоков
    if isinstance(information, dict):
        composition = _extract_composition_from_props(information.get("properties"))
        # Проверяем, не является ли это блоком sizes
        if information.get("id") == "sizes":
            size_info1, size_info2 = _extract_sizes_from_block(information)
    elif isinstance(information, list):
        for block in information:
            if not isinstance(block, dict):
                continue
            # Ищем блок "Состав"
            found = _extract_composition_from_props(block.get("properties"))
            if found:
                composition = found
            # Ищем блок "sizes"
            if block.get("id") == "sizes":
                size_info1, size_info2 = _extract_sizes_from_block(block)

    return {
        "title": str(title) if title is not None else None,
        "color_title": str(color_title) if color_title is not None else None,
        "category_title": str(category_title) if category_title is not None else None,
        "w2000_1": w2000_first,
        "w2000_2": w2000_second,
        "w2000_3": w2000_third,
        "composition": composition,
        "size_info1": size_info1,
        "size_info2": size_info2,
    }


def enrich_products(
    ids_path: str = PRODUCT_IDS_FILE,
    output_csv: str = OUTPUT_CSV_FILE,
    delay_sec: float = 0.2,
) -> None:
    """Основная функция: обогащает список ID товаров данными из API и пишет в CSV."""
    product_ids = read_product_ids(ids_path)
    if not product_ids:
        print(f"[INFO] В файле {ids_path} нет ID товаров.")
        return

    rows: List[Dict[str, Optional[str]]] = []

    for idx, pid in enumerate(product_ids, start=1):
        print(f"[INFO] ({idx}/{len(product_ids)}) Обрабатываю ID {pid}...")
        data = fetch_product(pid)
        if not data:
            rows.append(
                {
                    "product_id": pid,
                    "title": None,
                    "color_title": None,
                    "category_title": None,
                    "w2000_1": None,
                    "w2000_2": None,
                    "w2000_3": None,
                    "composition": None,
                    "size_info1": None,
                    "size_info2": None,
                }
            )
            time.sleep(delay_sec)
            continue

        info = extract_product_info(data)
        rows.append(
            {
                "product_id": pid,
                **info,
            }
        )
        time.sleep(delay_sec)

    fieldnames = [
        "product_id",
        "title",
        "color_title",
        "category_title",
        "w2000_1",
        "w2000_2",
        "w2000_3",
        "composition",
        "size_info1",
        "size_info2",
    ]

    with open(output_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)

    print(f"[OK] Таблица сохранена в {output_csv}")


if __name__ == "__main__":
    enrich_products()


