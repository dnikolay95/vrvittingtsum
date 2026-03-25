import os
import threading
import json
import csv
import shutil
import logging
from flask import Flask, request, redirect, url_for, send_from_directory, render_template_string
from werkzeug.utils import secure_filename

from tryon_processor import TsumTryOnProcessor
from enrich_products import enrich_products

# Логи примерки (tryon_processor) в тот же терминал, что и web_app
tryon_logger = logging.getLogger("tryon_processor")
if not tryon_logger.handlers:
    _stream = logging.StreamHandler()
    _stream.setLevel(logging.INFO)
    _formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    _stream.setFormatter(_formatter)
    tryon_logger.addHandler(_stream)
tryon_logger.setLevel(logging.INFO)

logger = logging.getLogger(__name__)


PHOTOS_DIR = "photos"
PRODUCTS_FILE = "producturl.txt"
PRODUCT_IDS_FILE = "productids.txt"
PRODUCT_SETS_FILE = "productsets.txt"
PRODUCT_INFO_CSV = "product_info.csv"
PRODUCT_PHOTOS_DIR = "product-photos"
ROOM_DIR = "room"
PROMPTS_FILE = "prompts.txt"
OUTPUT_DIR = "photoresult"
TEMP_DIR = "temp_photos"

app = Flask(__name__)

state = {
    "running": False,
    "last_results": [],
    "last_error": "",
    "status": "",
}
state_lock = threading.Lock()


def read_prompts(path: str):
    processor = TsumTryOnProcessor(prompts_file=path)
    return processor.prompts


def write_prompts(path: str, prompts: dict):
    keys = ["banana", "banana_multi", "banana_room", "flux", "flux_multi", "flux_room", "local_photos"]
    with open(path, "w", encoding="utf-8") as f:
        for key in keys:
            value = prompts.get(key, "").strip()
            f.write(f"{key}:\n{value}\n\n")


def read_products(path: str):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def write_products(path: str, urls):
    with open(path, "w", encoding="utf-8") as f:
        for url in urls:
            f.write(url.strip() + "\n")


def read_product_ids(path: str):
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [line.strip() for line in f if line.strip()]


def write_product_ids(path: str, ids):
    with open(path, "w", encoding="utf-8") as f:
        for product_id in ids:
            f.write(product_id.strip() + "\n")


def read_product_sets(path: str):
    """
    Читает наборы ID товаров для мульти-примерки.
    Ожидается формат: одна строка = один набор, ID разделены пробелом или запятой.
    """
    if not os.path.exists(path):
        return []
    sets = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            raw = line.strip()
            if not raw:
                continue
            # Разделяем по запятой или пробелу
            if "," in raw:
                parts = [p.strip() for p in raw.split(",") if p.strip()]
            else:
                parts = [p.strip() for p in raw.split() if p.strip()]
            if parts:
                sets.append(parts)
    return sets


def write_product_sets(path: str, sets):
    """
    Сохраняет наборы ID товаров в текстовый файл.
    """
    with open(path, "w", encoding="utf-8") as f:
        for id_list in sets:
            line = ", ".join(str(x).strip() for x in id_list if str(x).strip())
            if line:
                f.write(line + "\n")


def read_product_info_csv(path: str):
    """Читает CSV с информацией о товарах и возвращает список словарей"""
    if not os.path.exists(path):
        return []
    try:
        rows = []
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
        return rows
    except Exception as e:
        return []


def list_photos():
    if not os.path.exists(PHOTOS_DIR):
        os.makedirs(PHOTOS_DIR, exist_ok=True)
    exts = (".jpg", ".jpeg", ".png", ".webp")
    return sorted([p for p in os.listdir(PHOTOS_DIR) if p.lower().endswith(exts)])


def list_product_photos():
    """Возвращает список локальных фото товаров из папки product-photos."""
    if not os.path.exists(PRODUCT_PHOTOS_DIR):
        os.makedirs(PRODUCT_PHOTOS_DIR, exist_ok=True)
    exts = (".jpg", ".jpeg", ".png", ".webp")
    return sorted([p for p in os.listdir(PRODUCT_PHOTOS_DIR) if p.lower().endswith(exts)])


def run_tryon(person_path: str, adapter: str, body_part: str, product_photo_key: str):
    with state_lock:
        state.update({"running": True, "status": "Запущено", "last_error": ""})
    try:
        print(
            f"[WEB] run_tryon: adapter_mode={adapter}, body_part={body_part}, "
            f"product_photo_key={product_photo_key}, person_path={person_path}"
        )
        processor = TsumTryOnProcessor(prompts_file=PROMPTS_FILE)
        # Читаем все ID товаров
        all_ids = read_product_ids(PRODUCT_IDS_FILE)
        if not all_ids:
            raise Exception("Нет ID товаров в productids.txt")

        results = []
        # Батчами по 10 товаров
        for offset in range(0, len(all_ids), 10):
            batch_ids = all_ids[offset:offset + 10]
            print(f"[WEB] run_tryon: batch {offset//10 + 1}, ids={batch_ids}")
            batch_results = processor.process_all(
                person_photo_path=person_path,
                output_dir=OUTPUT_DIR,
                temp_dir=TEMP_DIR,
                adapter=adapter,
                body_part=body_part or "upper",
                product_info_csv=PRODUCT_INFO_CSV,
                product_ids_file=PRODUCT_IDS_FILE,
                product_ids_override=batch_ids,
                product_photo_key=product_photo_key,
            )
            results.extend(batch_results)
        with state_lock:
            state["last_results"] = results
            state["status"] = "Готово"
        print(f"[WEB] run_tryon: completed, results={len(results)}")
    except Exception as e:
        print(f"[WEB] run_tryon: ERROR: {e}")
        with state_lock:
            state["last_error"] = str(e)
            state["status"] = "Ошибка"
    finally:
        with state_lock:
            state["running"] = False


def run_tryon_with_example(person_path: str, adapter: str, body_part: str):
    """Запускает примерку с примером (3 файла: person, product, product2)"""
    with state_lock:
        state.update({"running": True, "status": "Запущено (с примером)", "last_error": ""})
    try:
        print(f"[WEB] run_tryon_with_example: adapter_mode={adapter}, body_part={body_part}, person_path={person_path}")
        processor = TsumTryOnProcessor(prompts_file=PROMPTS_FILE)
        
        # Загружаем информацию о товарах
        product_info_map = processor.load_product_info_csv(PRODUCT_INFO_CSV)
        product_ids_list = read_product_ids(PRODUCT_IDS_FILE)
        
        if not product_ids_list:
            raise Exception("Нет ID товаров в productids.txt")
        
        if not product_info_map:
            raise Exception("Не удалось загрузить информацию о товарах из product_info.csv")
        
        # Находим фото человека
        if not person_path:
            person_path = processor.find_person_photo()
        
        if not person_path or not os.path.exists(person_path):
            raise Exception(f"Фото человека не найдено: {person_path}")
        
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(TEMP_DIR, exist_ok=True)
        
        results = []

        # Определяем, какие адаптеры запускать по выбранному режиму
        if adapter == "banana":
            adapters = ["banana"]
        elif adapter == "flux":
            adapters = ["flux"]
        elif adapter == "gemini":
            adapters = ["gemini-3.1-flash-image-preview"]
        elif adapter == "banana_flux":
            adapters = ["banana", "flux"]
        elif adapter == "banana_gemini":
            adapters = ["banana", "gemini-3.1-flash-image-preview"]
        elif adapter == "flux_gemini":
            adapters = ["flux", "gemini-3.1-flash-image-preview"]
        elif adapter == "all_three":
            adapters = ["banana", "flux", "gemini-3.1-flash-image-preview"]
        else:
            adapters = ["banana"]
        
        # Обрабатываем все товары
        for i, product_id in enumerate(product_ids_list, 1):
            product_info = product_info_map.get(product_id)
            if not product_info:
                logger.warning(f"Товар #{i} (ID: {product_id}) не найден в CSV, пропускаю")
                continue
            
            # Получаем URL фото товаров
            product_image_url = product_info.get("w2000_1")
            product_image2_url = product_info.get("w2000_2")
            
            if not product_image_url:
                logger.warning(f"Нет w2000_1 для товара #{i}, пропускаю")
                continue
            
            # Скачиваем фото товаров
            product_image_path = processor.download_product_image(product_image_url, i, TEMP_DIR)
            if not product_image_path:
                logger.warning(f"Не удалось скачать фото товара #{i}, пропускаю")
                continue
            
            product_image2_path = None
            if product_image2_url:
                product_image2_path = processor.download_product_image(product_image2_url, i, TEMP_DIR)
                if not product_image2_path:
                    logger.warning(f"Не удалось скачать второе фото товара #{i}, продолжаю без него")
            
            # Выполняем примерку с примером для выбранных моделей
            for ad in adapters:
                tryon_result_path = processor.process_tryon_with_example(
                    person_image_path=person_path,
                    product_image_path=product_image_path,
                    product_image2_path=product_image2_path,
                    product_id=int(product_id),
                    adapter=ad,
                    body_part=body_part or "upper",
                    product_info=product_info
                )
                
                if tryon_result_path:
                    # Сохраняем результат
                    final_filename = f"tryon_example_{i:04d}_{ad}.png"
                    final_path = os.path.join(OUTPUT_DIR, final_filename)
                    shutil.copy2(tryon_result_path, final_path)
                    
                    # Загружаем на ImageBan
                    imageban_link = processor.upload_to_imageban(tryon_result_path, final_filename)
                    
                    results.append({
                        'index': i,
                        'product_id': product_id,
                        'adapter': ad,
                        'local_path': final_path,
                        'link': imageban_link,
                        'status': 'success'
                    })
        
        with state_lock:
            state["last_results"] = results
            state["status"] = "Готово (с примером)"
    except Exception as e:
        import traceback
        print(f"[WEB] run_tryon_with_example: ERROR: {e}")
        logger.error(f"Ошибка при примерке с примером: {e}")
        traceback.print_exc()
        with state_lock:
            state["last_error"] = str(e)
            state["status"] = "Ошибка"
    finally:
        with state_lock:
            state["running"] = False


def run_tryon_local_photos(person_path: str, adapter: str, body_part: str):
    """Запускает примерку по локальным фото товаров из product-photos."""
    with state_lock:
        state.update({"running": True, "status": "Запущено (локальные фото)", "last_error": ""})
    try:
        print(f"[WEB] run_tryon_local_photos: adapter={adapter}, body_part={body_part}, person_path={person_path}")
        processor = TsumTryOnProcessor(prompts_file=PROMPTS_FILE)

        # Находим фото человека
        if not person_path:
            person_path = processor.find_person_photo()

        if not person_path or not os.path.exists(person_path):
            raise Exception(f"Фото человека не найдено: {person_path}")

        results = processor.process_local_photos(
            person_photo_path=person_path,
            product_photos_dir=PRODUCT_PHOTOS_DIR,
            output_dir=OUTPUT_DIR,
            adapter=adapter,
            body_part=body_part or "upper",
        )

        with state_lock:
            state["last_results"] = results
            state["status"] = "Готово (локальные фото)"
        print(f"[WEB] run_tryon_local_photos: completed, results={len(results)}")
    except Exception as e:
        import traceback
        print(f"[WEB] run_tryon_local_photos: ERROR: {e}")
        logger.error(f"Ошибка при примерке по локальным фото: {e}")
        traceback.print_exc()
        with state_lock:
            state["last_error"] = str(e)
            state["status"] = "Ошибка"
    finally:
        with state_lock:
            state["running"] = False


def run_tryon_multi_sets(person_path: str, adapter: str, body_part: str):
    """
    Запускает мульти-примерку:
    одно фото человека + несколько товаров в каждой строке (наборе).
    """
    with state_lock:
        state.update(
            {"running": True, "status": "Запущено (мульти-наборы)", "last_error": ""}
        )
    try:
        print(
            f"[WEB] run_tryon_multi_sets: adapter={adapter}, "
            f"body_part={body_part}, person_path={person_path}"
        )
        processor = TsumTryOnProcessor(prompts_file=PROMPTS_FILE)

        # Загружаем наборы товаров
        product_id_sets = read_product_sets(PRODUCT_SETS_FILE)
        if not product_id_sets:
            raise Exception(
                "Нет наборов товаров в productsets.txt. Добавьте строки с ID товаров."
            )

        # Находим фото человека, если не передано явно
        if not person_path:
            person_path = processor.find_person_photo()

        if not person_path or not os.path.exists(person_path):
            raise Exception(f"Фото человека не найдено: {person_path}")

        os.makedirs(OUTPUT_DIR, exist_ok=True)
        os.makedirs(TEMP_DIR, exist_ok=True)

        results = processor.process_multi_sets(
            product_id_sets=product_id_sets,
            person_photo_path=person_path,
            output_dir=OUTPUT_DIR,
            temp_dir=TEMP_DIR,
            adapter=adapter,
            body_part=body_part or "upper",
            product_info_csv=PRODUCT_INFO_CSV,
        )

        with state_lock:
            state["last_results"] = results
            state["status"] = "Готово (мульти-наборы)"
        print(f"[WEB] run_tryon_multi_sets: completed, results={len(results)}")
    except Exception as e:
        import traceback

        print(f"[WEB] run_tryon_multi_sets: ERROR: {e}")
        logger.error(f"Ошибка при мульти-примерке: {e}")
        traceback.print_exc()
        with state_lock:
            state["last_error"] = str(e)
            state["status"] = "Ошибка"
    finally:
        with state_lock:
            state["running"] = False


def run_tryon_with_room(person_path: str, adapter: str, body_part: str):
    """Запускает обычную примерку, но с дополнительной фотографией примерочной (room)."""
    with state_lock:
        state.update({"running": True, "status": "Запущено (с примерочной)", "last_error": ""})
    try:
        print(f"[WEB] run_tryon_with_room: adapter={adapter}, body_part={body_part}, person_path={person_path}")
        processor = TsumTryOnProcessor(prompts_file=PROMPTS_FILE)

        if not person_path:
            person_path = processor.find_person_photo()

        if not person_path or not os.path.exists(person_path):
            raise Exception(f"Фото человека не найдено: {person_path}")

        # process_all с room_dir включает room-режим
        results = processor.process_all(
            person_photo_path=person_path,
            output_dir=OUTPUT_DIR,
            temp_dir=TEMP_DIR,
            adapter=adapter,
            body_part=body_part or "upper",
            product_info_csv=PRODUCT_INFO_CSV,
            product_ids_file=PRODUCT_IDS_FILE,
            room_dir=ROOM_DIR,
        )

        with state_lock:
            state["last_results"] = results
            state["status"] = "Готово (с примерочной)"
        print(f"[WEB] run_tryon_with_room: completed, results={len(results)}")
    except Exception as e:
        import traceback
        print(f"[WEB] run_tryon_with_room: ERROR: {e}")
        logger.error(f"Ошибка при примерке с примерочной: {e}")
        traceback.print_exc()
        with state_lock:
            state["last_error"] = str(e)
            state["status"] = "Ошибка"
    finally:
        with state_lock:
            state["running"] = False


PAGE_TEMPLATE = """
<!doctype html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>Try-on UI</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 20px; }
    form { margin-bottom: 20px; padding: 12px; border: 1px solid #ccc; border-radius: 6px; }
    h2 { margin-top: 0; }
    textarea { width: 100%; min-height: 120px; }
    .row { display: flex; gap: 16px; }
    .col { flex: 1; }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    td, th { border: 1px solid #ddd; padding: 6px; }
    img { max-width: 180px; height: auto; display: block; }
    .status { padding: 8px; border-radius: 4px; background: #eef; }
    .fname { max-width: 130px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; margin: 0 auto; }
    .photo-card { border:1px solid #ccc; padding:4px; width:140px; text-align:center; }
    .result-row { display:flex; gap:10px; margin-bottom:12px; flex-wrap:wrap; }
    .result-cell { flex:1; min-width:220px; border:1px solid #ddd; padding:6px; }
    .result-tag { font-weight:bold; margin-bottom:4px; }
    /* Вкладки */
    .tabs { margin-top: 20px; border-bottom: 1px solid #ccc; display:flex; gap:8px; }
    .tab-btn {
      padding: 6px 12px;
      border: 1px solid #ccc;
      border-bottom: none;
      border-radius: 6px 6px 0 0;
      background: #f5f5f5;
      cursor: pointer;
      font-size: 0.95rem;
    }
    .tab-btn.active {
      background: #fff;
      font-weight: bold;
    }
    .tab-content { display: none; padding-top: 10px; }
    .tab-content.active { display: block; }
  </style>
</head>
<body>
  <h1>Try-on UI</h1>
  <div class="status">
    <div>Статус: {{ status }}</div>
    {% if running %}<div>Идёт обработка... подождите.</div>{% endif %}
    {% if last_error %}<div style="color:red">Ошибка: {{ last_error }}</div>{% endif %}
  </div>

  <div class="tabs">
    <button class="tab-btn active" data-tab="tab-single">Обычная примерка</button>
    <button class="tab-btn" data-tab="tab-example">С примером</button>
    <button class="tab-btn" data-tab="tab-room">С примерочной</button>
    <button class="tab-btn" data-tab="tab-multi">Мульти по товарам</button>
    <button class="tab-btn" data-tab="tab-local">По фото товаров</button>
    <button class="tab-btn" data-tab="tab-prompts">Промты</button>
  </div>

  <!-- Обычная примерка -->
  <div id="tab-single" class="tab-content active">
  <form action="{{ url_for('start_run') }}" method="post" enctype="multipart/form-data">
    <h2>Обычная примерка</h2>
    <label>Фото товара:
      <select name="product_photo">
        <option value="w2000_1">первое (w2000_1)</option>
        <option value="w2000_2">второе (w2000_2)</option>
        <option value="w2000_3">третье (w2000_3)</option>
      </select>
    </label>
    <label style="margin-left:10px;">Модели:
      <select name="adapter">
        <option value="banana" {% if adapter=='banana' %}selected{% endif %}>banana (только)</option>
        <option value="flux" {% if adapter=='flux' %}selected{% endif %}>flux (только)</option>
        <option value="gemini" {% if adapter=='gemini' %}selected{% endif %}>gemini-3.1-flash-image-preview (только)</option>
        <option value="default" {% if adapter=='default' %}selected{% endif %}>по умолчанию (без adapter/prompt)</option>
        <option value="banana_flux" {% if adapter=='banana_flux' %}selected{% endif %}>banana + flux</option>
        <option value="banana_gemini" {% if adapter=='banana_gemini' %}selected{% endif %}>banana + gemini</option>
        <option value="flux_gemini" {% if adapter=='flux_gemini' %}selected{% endif %}>flux + gemini</option>
        <option value="all_three" {% if adapter=='all_three' %}selected{% endif %}>все три</option>
      </select>
    </label>
    <label style="margin-left:10px;">body_part:
      <select name="body_part">
        <option value="upper" {% if body_part=='upper' %}selected{% endif %}>upper</option>
        <option value="lower" {% if body_part=='lower' %}selected{% endif %}>lower</option>
      </select>
    </label>
    <div style="margin-top:10px;">
      <strong>Фото человека:</strong>
      <div style="margin-top:6px; display:flex; flex-wrap:wrap; gap:10px;">
        <label class="photo-card">
          <input type="radio" name="person_choice" value="" {% if selected_person=='' %}checked{% endif %}>
          <div class="fname">(авто)</div>
        </label>
        {% for photo in photos %}
          <label class="photo-card" style="cursor:pointer;">
            <input type="radio" name="person_choice" value="{{ photo }}" {% if selected_person==photo %}checked{% endif %}>
            <div class="fname" title="{{ photo }}">{{ photo }}</div>
            <img src="{{ url_for('serve_photo', filename=photo) }}" style="max-width:130px; max-height:150px; display:block; margin:4px auto;">
          </label>
        {% endfor %}
      </div>
      <div style="margin-top:6px;">
        Загрузить новое фото: <input type="file" name="person_file">
      </div>
    </div>
    <div style="margin-top:10px;">
      <button type="submit" {% if running %}disabled{% endif %}>Запустить примерку</button>
    </div>
  </form>

  <form action="{{ url_for('save_product_ids') }}" method="post">
    <h2>Список ID товаров</h2>
    <textarea name="product_ids">{{ product_ids_text }}</textarea>
    <div style="margin-top:8px;">
      <button type="submit">Сохранить ID товаров</button>
    </div>
  </form>

  <form action="{{ url_for('run_enrichment') }}" method="post">
    <h3>Обогащение информации по товарам</h3>
    <p>По текущему списку ID будет заново создан файл product_info.csv.</p>
    <button type="submit">Обновить информацию по товарам</button>
  </form>

  <h2>Информация о товарах</h2>
  {% if product_info %}
    <div style="overflow-x: auto;">
      <table>
        <thead>
          <tr>
            <th>ID</th>
            <th>Название</th>
            <th>Цвет</th>
            <th>Категория</th>
            <th>Фото 1</th>
            <th>Фото 2</th>
            <th>Состав</th>
            <th>Размеры 1</th>
            <th>Размеры 2</th>
          </tr>
        </thead>
        <tbody>
          {% for item in product_info %}
            <tr>
              <td>{{ item.get('product_id', '') }}</td>
              <td>{{ item.get('title', '') }}</td>
              <td>{{ item.get('color_title', '') }}</td>
              <td>{{ item.get('category_title', '') }}</td>
              <td>
                {% if item.get('w2000_1') %}
                  <img src="{{ item.w2000_1 }}" style="max-width:150px; height:auto;" alt="Фото 1">
                {% else %}
                  -
                {% endif %}
              </td>
              <td>
                {% if item.get('w2000_2') %}
                  <img src="{{ item.w2000_2 }}" style="max-width:150px; height:auto;" alt="Фото 2">
                {% else %}
                  -
                {% endif %}
              </td>
              <td style="max-width:300px; word-wrap:break-word;">{{ item.get('composition', '') }}</td>
              <td style="max-width:200px; word-wrap:break-word;">{{ item.get('size_info1', '') }}</td>
              <td style="max-width:200px; word-wrap:break-word;">{{ item.get('size_info2', '') }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <div>Таблица пуста. Запустите обогащение информации по товарам.</div>
  {% endif %}

  </div>

  <!-- Примерка с примером -->
  <div id="tab-example" class="tab-content">
  <form action="{{ url_for('start_run_with_example') }}" method="post" enctype="multipart/form-data">
    <h2>Примерка с примером</h2>
    <label>Модели:
      <select name="adapter">
        <option value="banana" {% if adapter=='banana' %}selected{% endif %}>banana (только)</option>
        <option value="flux" {% if adapter=='flux' %}selected{% endif %}>flux (только)</option>
        <option value="gemini" {% if adapter=='gemini' %}selected{% endif %}>gemini-3.1-flash-image-preview (только)</option>
        <option value="banana_flux" {% if adapter=='banana_flux' %}selected{% endif %}>banana + flux</option>
        <option value="banana_gemini" {% if adapter=='banana_gemini' %}selected{% endif %}>banana + gemini</option>
        <option value="flux_gemini" {% if adapter=='flux_gemini' %}selected{% endif %}>flux + gemini</option>
        <option value="all_three" {% if adapter=='all_three' %}selected{% endif %}>все три</option>
      </select>
    </label>
    <label style="margin-left:10px;">body_part:
      <select name="body_part">
        <option value="upper" {% if body_part=='upper' %}selected{% endif %}>upper</option>
        <option value="lower" {% if body_part=='lower' %}selected{% endif %}>lower</option>
      </select>
    </label>
    <div style="margin-top:10px;">
      <strong>Фото человека:</strong>
      <div style="margin-top:6px; display:flex; flex-wrap:wrap; gap:10px;">
        <label class="photo-card">
          <input type="radio" name="person_choice" value="" {% if selected_person=='' %}checked{% endif %}>
          <div class="fname">(авто)</div>
        </label>
        {% for photo in photos %}
          <label class="photo-card" style="cursor:pointer;">
            <input type="radio" name="person_choice" value="{{ photo }}" {% if selected_person==photo %}checked{% endif %}>
            <div class="fname" title="{{ photo }}">{{ photo }}</div>
            <img src="{{ url_for('serve_photo', filename=photo) }}" style="max-width:130px; max-height:150px; display:block; margin:4px auto;">
          </label>
        {% endfor %}
      </div>
      <div style="margin-top:6px;">
        Загрузить новое фото: <input type="file" name="person_file">
      </div>
    </div>
    <div style="margin-top:10px;">
      <button type="submit" {% if running %}disabled{% endif %}>Запустить примерку с примером</button>
      <div style="margin-top:6px; font-size:0.9em; color:#666;">
        Использует PRODUCT_IMAGE, PERSON_IMAGE и PRODUCT_IMAGE2 (второе фото товара из CSV)
      </div>
    </div>
  </form>
  </div>

  <!-- Примерка с примерочной -->
  <div id="tab-room" class="tab-content">
  <form action="{{ url_for('start_run_with_room') }}" method="post" enctype="multipart/form-data">
    <h2>Примерка с примерочной</h2>
    <p style="font-size:0.9em; color:#555;">
      Использует фото примерочной из папки <code>room</code> (ROOM_IMAGE).
    </p>
    <label>Адаптер:
      <select name="adapter">
        <option value="banana" {% if adapter=='banana' %}selected{% endif %}>banana</option>
        <option value="flux" {% if adapter=='flux' %}selected{% endif %}>flux</option>
        <option value="both" {% if adapter=='both' %}selected{% endif %}>оба</option>
      </select>
    </label>
    <label style="margin-left:10px;">body_part:
      <select name="body_part">
        <option value="upper" {% if body_part=='upper' %}selected{% endif %}>upper</option>
        <option value="lower" {% if body_part=='lower' %}selected{% endif %}>lower</option>
      </select>
    </label>
    <div style="margin-top:10px;">
      <strong>Фото человека:</strong>
      <div style="margin-top:6px; display:flex; flex-wrap:wrap; gap:10px;">
        <label class="photo-card">
          <input type="radio" name="person_choice" value="" {% if selected_person=='' %}checked{% endif %}>
          <div class="fname">(авто)</div>
        </label>
        {% for photo in photos %}
          <label class="photo-card" style="cursor:pointer;">
            <input type="radio" name="person_choice" value="{{ photo }}" {% if selected_person==photo %}checked{% endif %}>
            <div class="fname" title="{{ photo }}">{{ photo }}</div>
            <img src="{{ url_for('serve_photo', filename=photo) }}" style="max-width:130px; max-height:150px; display:block; margin:4px auto;">
          </label>
        {% endfor %}
      </div>
      <div style="margin-top:6px;">
        Загрузить новое фото: <input type="file" name="person_file">
      </div>
    </div>
    <div style="margin-top:10px;">
      <button type="submit" {% if running %}disabled{% endif %}>Запустить примерку с примерочной</button>
      <div style="margin-top:6px; font-size:0.9em; color:#666;">
        В запрос добавляется дополнительный файл ROOM_IMAGE из папки <code>room</code>.
      </div>
    </div>
  </form>
  </div>

  <!-- Мульти по товарам -->
  <div id="tab-multi" class="tab-content">
  <form action="{{ url_for('save_product_sets') }}" method="post">
    <h2>Наборы товаров для мульти-примерки</h2>
    <p style="font-size:0.9em; color:#555;">
      Одна строка — один набор. ID товаров разделяйте запятой или пробелом.
      Пример: <code>13760679, 13747749</code>
    </p>
    <textarea name="product_sets">{{ product_sets_text }}</textarea>
    <div style="margin-top:8px;">
      <button type="submit">Сохранить наборы товаров</button>
    </div>
  </form>

  <h2>Мульти-примерка по наборам товаров</h2>
  <p style="font-size:0.9em; color:#555;">
    Для каждого набора товаров будет выполнена одна мульти-примерка
    (используется промт <code>banana_multi</code> или <code>flux_multi</code>).
  </p>
  {% if product_sets_display %}
    <div style="overflow-x:auto; margin-bottom:8px;">
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>ID товаров в наборе</th>
          </tr>
        </thead>
        <tbody>
          {% for idx, ids in product_sets_display %}
            <tr>
              <td>{{ idx }}</td>
              <td>{{ ids }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <div style="margin-bottom:8px;">
      Наборы товаров не заданы. Добавьте строки выше и сохраните.
    </div>
  {% endif %}

  <form action="{{ url_for('start_run_multi_sets') }}" method="post" enctype="multipart/form-data">
    <h3>Запуск мульти-примерки</h3>
    <label>Адаптер:
      <select name="adapter">
        <option value="banana" {% if adapter=='banana' %}selected{% endif %}>banana</option>
        <option value="flux" {% if adapter=='flux' %}selected{% endif %}>flux</option>
        <option value="both" {% if adapter=='both' %}selected{% endif %}>оба</option>
      </select>
    </label>
    <label style="margin-left:10px;">body_part:
      <select name="body_part">
        <option value="upper" {% if body_part=='upper' %}selected{% endif %}>upper</option>
        <option value="lower" {% if body_part=='lower' %}selected{% endif %}>lower</option>
      </select>
    </label>
    <div style="margin-top:10px;">
      <strong>Фото человека:</strong>
      <div style="margin-top:6px; display:flex; flex-wrap:wrap; gap:10px;">
        <label class="photo-card">
          <input type="radio" name="person_choice" value="" {% if selected_person=='' %}checked{% endif %}>
          <div class="fname">(авто)</div>
        </label>
        {% for photo in photos %}
          <label class="photo-card" style="cursor:pointer;">
            <input type="radio" name="person_choice" value="{{ photo }}" {% if selected_person==photo %}checked{% endif %}>
            <div class="fname" title="{{ photo }}">{{ photo }}</div>
            <img src="{{ url_for('serve_photo', filename=photo) }}" style="max-width:130px; max-height:150px; display:block; margin:4px auto;">
          </label>
        {% endfor %}
      </div>
      <div style="margin-top:6px;">
        Загрузить новое фото: <input type="file" name="person_file">
      </div>
    </div>
    <div style="margin-top:8px;">
      <button type="submit" {% if running %}disabled{% endif %}>Запустить мульти-примерку</button>
    </div>
  </form>

  </div> <!-- /tab-multi -->

  <!-- По фото товаров -->
  <div id="tab-local" class="tab-content">

  <h2>Фото товаров (локальные)</h2>
  <p>Файлы берутся из папки <code>product-photos</code>. Они будут использованы в режиме «примерка по фото».</p>
  {% if product_photos %}
    <div style="display:flex; flex-wrap:wrap; gap:12px; margin-bottom:12px;">
      {% for fname in product_photos %}
        <div style="border:1px solid #ccc; padding:6px; width:180px; text-align:center;">
          <div class="fname" title="{{ fname }}">{{ fname }}</div>
          <img src="{{ url_for('serve_product_photo', filename=fname) }}" style="max-width:160px; max-height:180px; margin-top:4px;">
        </div>
      {% endfor %}
    </div>
    <form action="{{ url_for('start_run_local_photos') }}" method="post">
      <h3>Запуск примерки по локальным фото товаров</h3>
      <label>Адаптер:
        <select name="adapter">
          <option value="banana" {% if adapter=='banana' %}selected{% endif %}>banana</option>
          <option value="flux" {% if adapter=='flux' %}selected{% endif %}>flux</option>
          <option value="both" {% if adapter=='both' %}selected{% endif %}>оба</option>
        </select>
      </label>
      <label style="margin-left:10px;">body_part:
        <select name="body_part">
          <option value="upper" {% if body_part=='upper' %}selected{% endif %}>upper</option>
          <option value="lower" {% if body_part=='lower' %}selected{% endif %}>lower</option>
        </select>
      </label>
      <div style="margin-top:8px;">
        <strong>Фото человека:</strong>
        <div style="margin-top:6px; display:flex; flex-wrap:wrap; gap:10px;">
          <label class="photo-card">
            <input type="radio" name="person_choice" value="" {% if selected_person=='' %}checked{% endif %}>
            <div class="fname">(авто)</div>
          </label>
          {% for photo in photos %}
            <label class="photo-card" style="cursor:pointer;">
              <input type="radio" name="person_choice" value="{{ photo }}" {% if selected_person==photo %}checked{% endif %}>
              <div class="fname" title="{{ photo }}">{{ photo }}</div>
              <img src="{{ url_for('serve_photo', filename=photo) }}" style="max-width:130px; max-height:150px; display:block; margin:4px auto;">
            </label>
          {% endfor %}
        </div>
      </div>
      <div style="margin-top:8px;">
        <button type="submit" {% if running %}disabled{% endif %}>Запустить примерку по фото товаров</button>
      </div>
    </form>
  {% else %}
    <div>В папке <code>product-photos</code> пока нет файлов.</div>
  {% endif %}

  </div> <!-- /tab-local -->

  <!-- Промты -->
  <div id="tab-prompts" class="tab-content">

  <form action="{{ url_for('save_prompts') }}" method="post">
    <h2>Промты</h2>
    <div style="background:#f0f0f0; padding:8px; margin-bottom:12px; border-radius:4px; font-size:0.9em;">
      <strong>Доступные плейсхолдеры:</strong> product-title, product-category, product-color, product-material, size-info1, size-info2
      <br>Пример: "Реалистично примерьте product-title на человека. Цвет product-color, изготовлен из product-material"
    </div>
    <div class="row">
      <div class="col">
        <label>banana:<br>
          <textarea name="banana">{{ prompts.banana }}</textarea>
        </label>
      </div>
      <div class="col">
        <label>flux:<br>
          <textarea name="flux">{{ prompts.flux }}</textarea>
        </label>
      </div>
    </div>
    <div class="row">
      <div class="col">
        <label>banana_multi:<br>
          <textarea name="banana_multi">{{ prompts.banana_multi }}</textarea>
        </label>
      </div>
      <div class="col">
        <label>flux_multi:<br>
          <textarea name="flux_multi">{{ prompts.flux_multi }}</textarea>
        </label>
      </div>
    </div>
    <div class="row">
      <div class="col">
        <label>banana_room:<br>
          <textarea name="banana_room">{{ prompts.banana_room }}</textarea>
        </label>
      </div>
      <div class="col">
        <label>flux_room:<br>
          <textarea name="flux_room">{{ prompts.flux_room }}</textarea>
        </label>
      </div>
    </div>
    <div style="margin-top:12px;">
      <label>local_photos (для режима локальных фото):<br>
        <textarea name="local_photos">{{ prompts.local_photos }}</textarea>
      </label>
    </div>
    <div><button type="submit">Сохранить промты</button></div>
  </form>

  </div> <!-- /tab-prompts -->

  <h2>Результаты</h2>
  {% if last_results %}
    {% for r in last_results %}
      <div class="result-row">
        <div class="result-cell">
          <div class="result-tag">Человек</div>
          {% if r.person_path %}
            <img src="{{ url_for('serve_photo', filename=r.person_path.split(os_sep)[-1]) }}" style="max-width:100%; height:auto;">
          {% endif %}
        </div>
        <div class="result-cell">
          <div class="result-tag">Товар</div>
          {% if r.product_path %}
            <img src="{{ url_for('serve_temp', filename=r.product_path.split(os_sep)[-1]) }}" style="max-width:100%; height:auto;">
          {% endif %}
        </div>
        <div class="result-cell">
          <div class="result-tag">banana</div>
          {% if r.banana_local_path %}
            <img src="{{ url_for('serve_result', filename=r.banana_local_path.split(os_sep)[-1]) }}" style="max-width:100%; height:auto;">
          {% endif %}
          {% if r.banana_link %}
            <div><a href="{{ r.banana_link }}" target="_blank">CDN</a></div>
          {% endif %}
        </div>
        <div class="result-cell">
          <div class="result-tag">flux</div>
          {% if r.flux_local_path %}
            <img src="{{ url_for('serve_result', filename=r.flux_local_path.split(os_sep)[-1]) }}" style="max-width:100%; height:auto;">
          {% endif %}
          {% if r.flux_link %}
            <div><a href="{{ r.flux_link }}" target="_blank">CDN</a></div>
          {% endif %}
        </div>
        <div class="result-cell">
          <div class="result-tag">gemini</div>
          {% if r.gemini_local_path %}
            <img src="{{ url_for('serve_result', filename=r.gemini_local_path.split(os_sep)[-1]) }}" style="max-width:100%; height:auto;">
          {% endif %}
          {% if r.gemini_link %}
            <div><a href="{{ r.gemini_link }}" target="_blank">CDN</a></div>
          {% endif %}
        </div>
        <div class="result-cell">
          <div class="result-tag">default</div>
          {% if r.default_local_path %}
            <img src="{{ url_for('serve_result', filename=r.default_local_path.split(os_sep)[-1]) }}" style="max-width:100%; height:auto;">
          {% endif %}
          {% if r.default_link %}
            <div><a href="{{ r.default_link }}" target="_blank">CDN</a></div>
          {% endif %}
        </div>
      </div>
    {% endfor %}
  {% else %}
    <div>Пока нет результатов.</div>
  {% endif %}

  <script>
    (function() {
      const buttons = document.querySelectorAll('.tab-btn');
      const tabs = document.querySelectorAll('.tab-content');
      buttons.forEach(btn => {
        btn.addEventListener('click', () => {
          const id = btn.getAttribute('data-tab');
          buttons.forEach(b => b.classList.remove('active'));
          tabs.forEach(t => t.classList.remove('active'));
          btn.classList.add('active');
          const el = document.getElementById(id);
          if (el) el.classList.add('active');
        });
      });
    })();
  </script>

</body>
</html>
"""


@app.route("/", methods=["GET"])
def index():
    prompts = read_prompts(PROMPTS_FILE)
    product_ids = read_product_ids(PRODUCT_IDS_FILE)
    product_sets = read_product_sets(PRODUCT_SETS_FILE)
    product_info = read_product_info_csv(PRODUCT_INFO_CSV)
    photos = list_photos()
    product_photos = list_product_photos()
    with state_lock:
        running = state["running"]
        last_results = state["last_results"]
        last_error = state["last_error"]
        status = state["status"]
    return render_template_string(
        PAGE_TEMPLATE,
        prompts=prompts,
        product_ids_text="\n".join(product_ids),
        product_sets_text="\n".join([", ".join(s) for s in product_sets]),
        product_sets_display=[(i + 1, ", ".join(s)) for i, s in enumerate(product_sets)],
        product_info=product_info,
        photos=photos,
        product_photos=product_photos,
        selected_person="",
        adapter="banana",
        body_part="upper",
        running=running,
        last_results=last_results,
        last_error=last_error,
        status=status,
        os_sep=os.sep,
    )


@app.route("/save_prompts", methods=["POST"])
def save_prompts():
    prompts = {
        "banana": request.form.get("banana", ""),
        "banana_multi": request.form.get("banana_multi", ""),
        "banana_room": request.form.get("banana_room", ""),
        "flux": request.form.get("flux", ""),
        "flux_multi": request.form.get("flux_multi", ""),
        "flux_room": request.form.get("flux_room", ""),
        "local_photos": request.form.get("local_photos", ""),
    }
    write_prompts(PROMPTS_FILE, prompts)
    return redirect(url_for("index"))


@app.route("/save_products", methods=["POST"])
def save_products():
    raw = request.form.get("products", "")
    urls = [line.strip() for line in raw.splitlines() if line.strip()]
    write_products(PRODUCTS_FILE, urls)
    return redirect(url_for("index"))


@app.route("/save_product_ids", methods=["POST"])
def save_product_ids():
    raw = request.form.get("product_ids", "")
    ids = [line.strip() for line in raw.splitlines() if line.strip()]
    write_product_ids(PRODUCT_IDS_FILE, ids)
    return redirect(url_for("index"))


@app.route("/save_product_sets", methods=["POST"])
def save_product_sets():
    raw = request.form.get("product_sets", "")
    sets = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if "," in line:
            parts = [p.strip() for p in line.split(",") if p.strip()]
        else:
            parts = [p.strip() for p in line.split() if p.strip()]
        if parts:
            sets.append(parts)

    write_product_sets(PRODUCT_SETS_FILE, sets)
    return redirect(url_for("index"))


@app.route("/enrich_products", methods=["POST"])
def run_enrichment():
    # По кнопке полностью перегенерируем product_info.csv
    try:
        enrich_products()
        with state_lock:
            state["status"] = "Информация о товарах обновлена"
            state["last_error"] = ""
    except Exception as e:
        with state_lock:
            state["last_error"] = f"Ошибка обогащения товаров: {e}"
    return redirect(url_for("index"))

@app.route("/run", methods=["POST"])
def start_run():
    adapter = request.form.get("adapter", "banana")
    product_photo_key = request.form.get("product_photo") or "w2000_1"
    body_part = request.form.get("body_part") or "upper"
    person_choice = request.form.get("person_choice") or ""

    person_upload = request.files.get("person_file")
    if person_upload and person_upload.filename:
        os.makedirs(PHOTOS_DIR, exist_ok=True)
        filename = secure_filename(person_upload.filename)
        save_path = os.path.join(PHOTOS_DIR, filename)
        person_upload.save(save_path)
        person_choice = filename

    if person_choice:
        person_path = os.path.join(PHOTOS_DIR, person_choice)
    else:
        person_path = None  # auto-find inside processor

    with state_lock:
        if state["running"]:
            return redirect(url_for("index"))
        state["status"] = "Запуск..."
        state["last_error"] = ""

    thread = threading.Thread(
        target=run_tryon,
        args=(person_path, adapter, body_part, product_photo_key),
        daemon=True,
    )
    thread.start()
    return redirect(url_for("index"))


@app.route("/run_with_example", methods=["POST"])
def start_run_with_example():
    adapter = request.form.get("adapter", "banana")
    body_part = request.form.get("body_part") or "upper"
    person_choice = request.form.get("person_choice") or ""

    person_upload = request.files.get("person_file")
    if person_upload and person_upload.filename:
        os.makedirs(PHOTOS_DIR, exist_ok=True)
        filename = secure_filename(person_upload.filename)
        save_path = os.path.join(PHOTOS_DIR, filename)
        person_upload.save(save_path)
        person_choice = filename

    if person_choice:
        person_path = os.path.join(PHOTOS_DIR, person_choice)
    else:
        person_path = None  # auto-find inside processor

    with state_lock:
        if state["running"]:
            return redirect(url_for("index"))
        state["status"] = "Запуск (с примером)..."
        state["last_error"] = ""

    thread = threading.Thread(target=run_tryon_with_example, args=(person_path, adapter, body_part), daemon=True)
    thread.start()
    return redirect(url_for("index"))


@app.route("/run_local_photos", methods=["POST"])
def start_run_local_photos():
    adapter = request.form.get("adapter", "banana")
    body_part = request.form.get("body_part") or "upper"
    person_choice = request.form.get("person_choice") or ""

    if person_choice:
        person_path = os.path.join(PHOTOS_DIR, person_choice)
    else:
        person_path = None  # auto-find inside processor

    with state_lock:
        if state["running"]:
            return redirect(url_for("index"))
        state["status"] = "Запуск (локальные фото)..."
        state["last_error"] = ""

    thread = threading.Thread(target=run_tryon_local_photos, args=(person_path, adapter, body_part), daemon=True)
    thread.start()
    return redirect(url_for("index"))


@app.route("/run_with_room", methods=["POST"])
def start_run_with_room():
    adapter = request.form.get("adapter", "banana")
    body_part = request.form.get("body_part") or "upper"
    person_choice = request.form.get("person_choice") or ""

    person_upload = request.files.get("person_file")
    if person_upload and person_upload.filename:
        os.makedirs(PHOTOS_DIR, exist_ok=True)
        filename = secure_filename(person_upload.filename)
        save_path = os.path.join(PHOTOS_DIR, filename)
        person_upload.save(save_path)
        person_choice = filename

    if person_choice:
        person_path = os.path.join(PHOTOS_DIR, person_choice)
    else:
        person_path = None  # auto-find inside processor

    with state_lock:
        if state["running"]:
            return redirect(url_for("index"))
        state["status"] = "Запуск (с примерочной)..."
        state["last_error"] = ""

    thread = threading.Thread(
        target=run_tryon_with_room,
        args=(person_path, adapter, body_part),
        daemon=True,
    )
    thread.start()
    return redirect(url_for("index"))


@app.route("/run_multi_sets", methods=["POST"])
def start_run_multi_sets():
    adapter = request.form.get("adapter", "banana")
    body_part = request.form.get("body_part") or "upper"
    person_choice = request.form.get("person_choice") or ""

    person_upload = request.files.get("person_file")
    if person_upload and person_upload.filename:
        os.makedirs(PHOTOS_DIR, exist_ok=True)
        filename = secure_filename(person_upload.filename)
        save_path = os.path.join(PHOTOS_DIR, filename)
        person_upload.save(save_path)
        person_choice = filename

    if person_choice:
        person_path = os.path.join(PHOTOS_DIR, person_choice)
    else:
        person_path = None  # auto-find inside processor

    with state_lock:
        if state["running"]:
            return redirect(url_for("index"))
        state["status"] = "Запуск (мульти-наборы)..."
        state["last_error"] = ""

    thread = threading.Thread(
        target=run_tryon_multi_sets,
        args=(person_path, adapter, body_part),
        daemon=True,
    )
    thread.start()
    return redirect(url_for("index"))


@app.route("/photoresult/<path:filename>")
def serve_result(filename):
    return send_from_directory(OUTPUT_DIR, filename)


@app.route("/photos/<path:filename>")
def serve_photo(filename):
    return send_from_directory(PHOTOS_DIR, filename)


@app.route("/temp_photos/<path:filename>")
def serve_temp(filename):
    return send_from_directory(TEMP_DIR, filename)


@app.route("/product-photos/<path:filename>")
def serve_product_photo(filename):
    return send_from_directory(PRODUCT_PHOTOS_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)

