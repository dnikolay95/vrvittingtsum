import os
import base64
import requests
import time
import json
from pathlib import Path
from typing import List, Dict, Optional
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import argparse
import shutil
import traceback

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('tryon_processor.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TryOnValidationError(Exception):
    """Raised when the try-on API rejects the photo (e.g. body_validation_failed)."""
    def __init__(self, failures: list, message: str = ""):
        self.failures = failures
        super().__init__(message or f"body_validation_failed: {', '.join(failures)}")


class TsumTryOnProcessor:
    def __init__(self, api_key: str = None, prompts_file: str = "prompts.txt"):
        if api_key is None:
            api_key = os.environ.get("TSUM_API_KEY", "94960fba-035c-4de6-b78a-701898751d27")
        self.api_key = api_key
        self.prompts_file = prompts_file
        self.prompts = self.load_prompts(prompts_file)
        # v3 API headers/endpoint (шаблон, для новых сессий)
        self.tryon_headers = {
            "x-api-key": self.api_key,
            "X-Device-Name": "desktop",
            "X-Device-Id": "device-123",
            "X-App-Platform": "web",
            "X-App-Version": "1.0.0",
            "X-OS-Version": "macos",
            "accept": "application/json",
        }
        self.tryon_session = requests.Session()
        self.tryon_session.headers.update(self.tryon_headers)
        # В v3 актуальный префикс включает /api
        self.tryon_base_url = "https://tryon.tsum.com/api"
        self.tryon_tryon_path = "/v3/tryon/apparel"
        # Для мульти-примерки используется другой путь (без /apparel)
        self.tryon_multi_path = "/v3/tryon"
        self.tryon_jobs_path = "/v3/jobs"
        
        # ImageBan API credentials
        self.imageban_client_id = "Pf91eht8Im9gt3l8M74L"
        self.imageban_secret_key = "NtkaXUjT7pAzOUzo8zXiKkPHwPZKWVkjExl"
        self.imageban_api_url = "https://api.imageban.ru/v1"

    def load_prompts(self, prompts_file: str) -> Dict[str, str]:
        """Читает промты из текстового файла (формат: ключ: строки до следующего ключа)"""
        default_prompts = {
            "banana": (
                "Virtual try-on: use PRODUCT_IMAGE and PERSON_IMAGE. "
                "Try on the product onto the person realistically. "
                "Requirements:\n"
                "1. PRESERVE STRUCTURE: Keep the exact body shape, height, and pose of the person. "
                "The feet and head position must remain identical to the original photo relative to the background.\n"
                "2. FIT: The garment should drape naturally over the person's existing body proportions. "
                "Do not elongate or slim the body to fit the clothes.\n"
                "3. BACKGROUND: Keep the background exactly unchanged.\n"
                "4. LIGHTING: Match the lighting of the garment to the scene.\n"
                "Output a photorealistic image."
            ),
            "banana_room": (
                "Virtual try-on task with fitting room background.\n\n"
                "INPUTS:\n"
                "- PERSON_IMAGE: The person to dress (this is your MAIN SUBJECT)\n"
                "- First PRODUCT_IMAGE: The garment/clothing item to try on\n"
                "- Second PRODUCT_IMAGE: The fitting room background (IGNORE any person in this image, use ONLY the room/environment)\n\n"
                "TASK:\n"
                "1. Take the person from PERSON_IMAGE (keep their exact pose, face, and body structure)\n"
                "2. Replace ONLY their clothing with the garment from the first PRODUCT_IMAGE\n"
                "3. Replace the background with the room environment from the second PRODUCT_IMAGE (ignore any person visible in that room image)\n\n"
                "CRITICAL REQUIREMENTS:\n"
                "1. CLOTHING REPLACEMENT: You MUST replace the clothing on the person from PERSON_IMAGE with the garment from the first PRODUCT_IMAGE. "
                "The person's current clothing must be completely replaced by the new garment.\n"
                "2. PRESERVE POSE: Keep the exact body pose, height, and body structure of the person from PERSON_IMAGE. "
                "Do not change the position of feet, hands, or head. Maintain the same stance and posture.\n"
                "3. PRESERVE FACE: Keep the person's face from PERSON_IMAGE completely unchanged. Do not modify facial features, "
                "expression, or head position.\n"
                "4. BACKGROUND ONLY: Extract ONLY the room environment (walls, floor, furniture, lighting) from the second PRODUCT_IMAGE. "
                "IGNORE and REMOVE any person that may be visible in that room image. Use only the empty room as background.\n"
                "5. LIGHTING: Match the lighting conditions from the second PRODUCT_IMAGE (fitting room). "
                "Apply the same light direction, intensity, shadows, and reflections as in the fitting room photo.\n"
                "6. INTEGRATION: Make sure the person from PERSON_IMAGE (wearing the new garment) looks naturally placed in the room, "
                "with proper shadows cast on the floor and correct perspective alignment.\n\n"
                "DO NOT: Move the person to a different position, change their pose, or use the person from the room image.\n"
                "DO: Replace clothing on PERSON_IMAGE person, place them in the empty room background.\n"
                "Output a photorealistic image."
            ),
            "banana_multi": (
                "Virtual try-on: use all PRODUCT_IMAGE inputs together with PERSON_IMAGE. "
                "Try on every product onto the person realistically and cohesively. "
                "Requirements:\n"
                "1. PRESERVE STRUCTURE: Keep the exact body shape, height, and pose of the person. "
                "The feet and head position must remain identical to the original photo relative to the background.\n"
                "2. FIT: The garment should drape naturally over the person's existing body proportions. "
                "Do not elongate or slim the body to fit the clothes.\n"
                "3. BACKGROUND: Keep the background exactly unchanged.\n"
                "4. LIGHTING: Match the lighting of the garment to the scene.\n"
                "Output a photorealistic image."
            ),
            "flux": (
                "Virtual try-on task.\n"
                "Image 1 is the reference garment (product). Image 2 is the model (person).\n"
                "Task: Generate a photo of the person from Image 2 wearing the garment from Image 1.\n"
                "Requirements:\n"
                "1. PRESERVE STRUCTURE: Keep the exact body shape, height, and pose of the person. "
                "The feet and head position must remain identical to the original photo relative to the background.\n"
                "2. FIT: The garment should drape naturally over the person's existing body proportions. "
                "Do not elongate or slim the body to fit the clothes.\n"
                "3. BACKGROUND: Keep the background exactly unchanged.\n"
                "4. LIGHTING: Match the lighting of the garment to the scene.\n"
                "Output a photorealistic image."
            ),
            "flux_room": (
                "Virtual try-on task with fitting room background.\n\n"
                "INPUTS:\n"
                "- Image 1 (first product): The garment/clothing item to try on\n"
                "- Image 2 (second product): The fitting room background (IGNORE any person in this image, use ONLY the room/environment)\n"
                "- Image 3 (person): The person to dress (this is your MAIN SUBJECT)\n\n"
                "TASK:\n"
                "1. Take the person from Image 3 (keep their exact pose, face, and body structure)\n"
                "2. Replace ONLY their clothing with the garment from Image 1\n"
                "3. Replace the background with the room environment from Image 2 (ignore any person visible in that room image)\n\n"
                "CRITICAL REQUIREMENTS:\n"
                "1. CLOTHING REPLACEMENT: You MUST replace the clothing on the person from Image 3 with the garment from Image 1. "
                "The person's current clothing must be completely replaced by the new garment.\n"
                "2. PRESERVE POSE: Keep the exact body pose, height, and body structure of the person from Image 3. "
                "Do not change the position of feet, hands, or head. Maintain the same stance and posture.\n"
                "3. PRESERVE FACE: Keep the person's face from Image 3 completely unchanged. Do not modify facial features, "
                "expression, or head position.\n"
                "4. BACKGROUND ONLY: Extract ONLY the room environment (walls, floor, furniture, lighting) from Image 2. "
                "IGNORE and REMOVE any person that may be visible in that room image. Use only the empty room as background.\n"
                "5. LIGHTING: Match the lighting conditions from Image 2 (fitting room). "
                "Apply the same light direction, intensity, shadows, and reflections as in the fitting room photo.\n"
                "6. INTEGRATION: Make sure the person from Image 3 (wearing the new garment) looks naturally placed in the room, "
                "with proper shadows cast on the floor and correct perspective alignment.\n\n"
                "DO NOT: Move the person to a different position, change their pose, or use the person from the room image.\n"
                "DO: Replace clothing on Image 3 person, place them in the empty room background.\n"
                "Output a photorealistic image."
            ),
            "flux_multi": (
                "Virtual try-on: use all product images together with the person image. "
                "Inputs are ordered: images 1..N are products, last image is the person. "
                "Try on every product onto the person realistically and cohesively. "
                "Do not add or remove anything, do not change framing or background. "
                "Return exactly one image with all products on the person."
            ),
        }
        if not os.path.exists(prompts_file):
            logger.warning(f"Файл с промтами не найден: {prompts_file}. Использую дефолтные.")
            return default_prompts
        
        prompts: Dict[str, str] = {}
        current_key = None
        buffer: List[str] = []
        
        def flush():
            nonlocal current_key, buffer
            if current_key:
                prompts[current_key] = "\n".join(buffer).strip()
            current_key = None
            buffer = []
        
        try:
            with open(prompts_file, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.rstrip("\n")
                    if stripped and not stripped.startswith(" "):
                        if ":" in stripped and stripped.endswith(":"):
                            flush()
                            current_key = stripped[:-1].strip()
                            continue
                    if current_key:
                        buffer.append(stripped)
            flush()
        except Exception as e:
            logger.error(f"Ошибка чтения файла промтов: {e}")
            return default_prompts
        
        for k, v in default_prompts.items():
            prompts.setdefault(k, v)
        return prompts
    
    def find_person_photo(self, photos_dir: str = "photos") -> Optional[str]:
        """Находит фото человека в директории photos"""
        extensions = ['.jpg', '.jpeg', '.png', '.webp']
        
        for ext in extensions:
            common_names = ['person', 'human', 'photo', 'me', 'user', 'model']
            
            for name in common_names:
                pattern = f"{name}*{ext}"
                files = list(Path(photos_dir).glob(pattern))
                if files:
                    return str(files[0])
        
        for ext in extensions:
            files = list(Path(photos_dir).glob(f"*{ext}"))
            if files:
                return str(files[0])
        
        return None

    def find_room_photo(self, room_dir: str = "room") -> Optional[str]:
        """Находит фото примерочной в директории room"""
        extensions = ['.jpg', '.jpeg', '.png', '.webp']

        for ext in extensions:
            files = list(Path(room_dir).glob(f"*{ext}"))
            if files:
                return str(files[0])

        return None
    
    def read_product_urls(self, urls_file: str = "producturl.txt") -> List[str]:
        """Читает URL товаров из текстового файла"""
        urls = []
        try:
            with open(urls_file, 'r', encoding='utf-8') as f:
                for line in f:
                    url = line.strip()
                    if url and url.startswith('http'):
                        urls.append(url)
            logger.info(f"Прочитано {len(urls)} URL из файла {urls_file}")
            return urls
        except Exception as e:
            logger.error(f"Ошибка при чтении файла {urls_file}: {e}")
            return []
    
    def download_product_image(self, url: str, index: int, temp_dir: str = "temp_photos") -> Optional[str]:
        """Скачивает фото товара по URL"""
        os.makedirs(temp_dir, exist_ok=True)
        
        filename = f"product_{index:04d}.jpg"
        filepath = os.path.join(temp_dir, filename)
        
        try:
            logger.info(f"Скачиваю фото товара #{index}: {url[:60]}...")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            
            with open(filepath, 'wb') as f:
                f.write(response.content)
            
            logger.info(f"Фото сохранено: {filename}")
            return filepath
            
        except Exception as e:
            logger.error(f"Ошибка при скачивании фото #{index}: {e}")
            return None
    
    def image_to_base64(self, image_path: str) -> Optional[str]:
        """Конвертирует изображение в base64"""
        try:
            with open(image_path, "rb") as image_file:
                encoded = base64.b64encode(image_file.read()).decode('utf-8')
                return encoded
        except Exception as e:
            logger.error(f"Ошибка при чтении изображения {image_path}: {e}")
            return None
    
    def substitute_prompt_placeholders(self, prompt: str, product_info: Optional[Dict[str, str]] = None) -> str:
        """Подставляет информацию о товаре в промпт через плейсхолдеры"""
        if not product_info:
            return prompt
        
        replacements = {
            "product-title": product_info.get("title", ""),
            "product-category": product_info.get("category_title", ""),
            "product-color": product_info.get("color_title", ""),
            "product-material": product_info.get("composition", ""),
            "size-info1": product_info.get("size_info1", ""),
            "size-info2": product_info.get("size_info2", ""),
        }
        
        result = prompt
        for placeholder, value in replacements.items():
            result = result.replace(placeholder, value)
        
        return result
    
    def process_tryon_with_example(self, 
                                   person_image_path: str, 
                                   product_image_path: str,
                                   product_image2_path: Optional[str] = None,
                                   product_id: int = 13728452, 
                                   body_part: Optional[str] = "upper",
                                   adapter: str = "banana",
                                   product_info: Optional[Dict[str, str]] = None) -> Optional[str]:
        """Выполняет примерку с примером (3 файла: person, product, product2) и возвращает путь к результату"""
        
        # Используем обычный промпт (не _multi), но с упоминанием PRODUCT_IMAGE2
        prompt_key = adapter if adapter in ["banana", "flux"] else "banana"
        prompt = self.prompts.get(prompt_key, "")
        prompt = self.substitute_prompt_placeholders(prompt, product_info)
        
        try:
            logger.info("Отправляю запрос на примерку с примером (v3, multipart, 3 файла)...")
            tryon_url = f"{self.tryon_base_url}{self.tryon_tryon_path}"
            params = {"body_part": body_part} if body_part else {}
            
            data = {
                "productId": str(product_id),
                "adapter": adapter,
                "prompt": prompt,
            }
            
            # Создаем отдельную сессию на запрос (requests.Session не потокобезопасен)
            session = requests.Session()
            session.headers.update(self.tryon_headers)
            
            # Открываем все файлы
            person_file = open(person_image_path, "rb")
            product_file = open(product_image_path, "rb")
            
            files = {
                "person": (os.path.basename(person_image_path), person_file, "image/jpeg"),
                "product": (os.path.basename(product_image_path), product_file, "image/jpeg"),
            }
            
            # Если есть второе фото товара - добавляем его
            if product_image2_path and os.path.exists(product_image2_path):
                product2_file = open(product_image2_path, "rb")
                files["product2"] = (os.path.basename(product_image2_path), product2_file, "image/jpeg")
                logger.info(f"Добавлено второе фото товара: {os.path.basename(product_image2_path)}")
            
            try:
                response = session.post(
                    tryon_url,
                    headers=session.headers,
                    data=data,
                    files=files,
                    params=params,
                    timeout=60
                )
            finally:
                person_file.close()
                product_file.close()
                if "product2" in files:
                    files["product2"][1].close()
            
            response.raise_for_status()
            
            data = response.json()
            job_id = data.get('job_id')
            
            if not job_id:
                logger.error(f"Не получен job_id. Ответ: {data}")
                return None
                
            logger.info(f"Получен job_id: {job_id}")
            
            # Поллинг результата: до 12 попыток, каждые 5 секунд (до ~60 секунд)
            max_attempts = 12
            poll_interval = 5
            result_url = f"{self.tryon_base_url}{self.tryon_jobs_path}/{job_id}/result"
            
            for attempt in range(1, max_attempts + 1):
                logger.info(f"Проверяю результат (попытка {attempt}/{max_attempts})...")
                try:
                    result_response = session.get(
                        result_url,
                        headers={'accept': 'image/png', 'x-api-key': self.api_key},
                        timeout=60
                    )
                except requests.exceptions.RequestException as e:
                    logger.warning(f"Ошибка при запросе результата: {e}")
                    if attempt == max_attempts:
                        return None
                    time.sleep(poll_interval)
                    continue
                
                if result_response.status_code == 200:
                    temp_result = f"temp_{job_id}.png"
                    with open(temp_result, 'wb') as f:
                        f.write(result_response.content)
                    
                    logger.info(f"Результат получен и сохранен: {temp_result}")
                    return temp_result
                
                logger.info(f"Результат не готов, статус: {result_response.status_code}")
                
                if attempt < max_attempts:
                    time.sleep(poll_interval)
            
            logger.error("Не удалось получить результат за отведенное время")
            return None
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при запросе к Tsum API: {e}")
            return None
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            return None

    def process_tryon_multi(
        self,
        person_image_path: str,
        product_image_paths: List[str],
        product_ids: List[str],
        body_part: Optional[str] = "upper",
        adapter: str = "banana",
        product_info: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """
        Мульти-примерка: одно фото человека + несколько фото товаров.
        Использует промт banana_multi / flux_multi и отправляет несколько файлов "product".
        """

        # Выбираем мульти-промт для адаптера
        prompt_key = f"{adapter}_multi" if f"{adapter}_multi" in self.prompts else adapter
        prompt = self.prompts.get(prompt_key, "")
        prompt = self.substitute_prompt_placeholders(prompt, product_info)

        try:
            logger.info(
                f"Отправляю запрос на мульти-примерку (v3, multipart)... "
                f"adapter={adapter}, products={len(product_image_paths)}"
            )
            # Для мульти-примерки используем отдельный endpoint: /api/v3/tryon (без /apparel)
            tryon_url = f"{self.tryon_base_url}{self.tryon_multi_path}"
            # Для мульти-ручки body_part не передаём вообще
            params = {}

            # Формируем form-data как список пар, чтобы можно было передать несколько productIds
            form_data: List[tuple] = []
            # adapter
            form_data.append(("adapter", adapter))
            # мульти-промт
            form_data.append(("promptMulti", prompt))
            # список productIds (по одному полю на каждый ID товара в наборе)
            for pid in product_ids:
                pid_str = str(pid).strip()
                if pid_str:
                    form_data.append(("productIds", pid_str))

            # Отдельная сессия для потока
            session = requests.Session()
            session.headers.update(self.tryon_headers)

            # Готовим файлы: несколько products + один person
            files: List[tuple] = []
            try:
                # person
                person_file = open(person_image_path, "rb")
                files.append(
                    (
                        "person",
                        (os.path.basename(person_image_path), person_file, "image/jpeg"),
                    )
                )

                # все товары под одним ключом "product"
                product_file_handles = []
                for p in product_image_paths:
                    f = open(p, "rb")
                    product_file_handles.append(f)
                    files.append(
                        ("products", (os.path.basename(p), f, "image/jpeg"))
                    )

                response = session.post(
                    tryon_url,
                    headers=session.headers,
                    data=form_data,
                    files=files,
                    params=params,
                    timeout=60,
                )
            finally:
                # Закрываем все файлы
                try:
                    person_file.close()
                except Exception:
                    pass
                for f in locals().get("product_file_handles", []):
                    try:
                        f.close()
                    except Exception:
                        pass

            # Если сервер вернул ошибку, сразу логируем тело
            if response.status_code >= 400:
                try:
                    logger.error(f"[multi] HTTP {response.status_code} от Tsum API: {response.text}")
                except Exception:
                    logger.error(f"[multi] HTTP {response.status_code} от Tsum API (не удалось прочитать text)")
                if response.status_code == 422:
                    try:
                        body = response.json()
                        detail = body.get("detail", {})
                        if detail.get("error") == "body_validation_failed":
                            failures = detail.get("failures", [])
                            raise TryOnValidationError(failures)
                    except (ValueError, AttributeError):
                        pass
                response.raise_for_status()

            data = response.json()
            job_id = data.get("job_id")

            if not job_id:
                logger.error(f"Не получен job_id для мульти-примерки. Ответ: {data}")
                return None

            logger.info(f"Получен job_id для мульти-примерки: {job_id}")

            # Поллинг результата
            max_attempts = 12
            poll_interval = 5
            result_url = f"{self.tryon_base_url}{self.tryon_jobs_path}/{job_id}/result"

            for attempt in range(1, max_attempts + 1):
                logger.info(f"[multi] Проверяю результат (попытка {attempt}/{max_attempts})...")
                try:
                    result_response = session.get(
                        result_url,
                        headers={"accept": "image/png", "x-api-key": self.api_key},
                        timeout=60,
                    )
                except requests.exceptions.RequestException as e:
                    logger.warning(f"[multi] Ошибка при запросе результата: {e}")
                    if attempt == max_attempts:
                        return None
                    time.sleep(poll_interval)
                    continue

                if result_response.status_code == 200:
                    temp_result = f"temp_multi_{job_id}.png"
                    with open(temp_result, "wb") as f:
                        f.write(result_response.content)

                    logger.info(f"[multi] Результат получен и сохранен: {temp_result}")
                    return temp_result

                logger.info(f"[multi] Результат не готов, статус: {result_response.status_code}")

                if attempt < max_attempts:
                    time.sleep(poll_interval)

            logger.error("[multi] Не удалось получить результат за отведенное время")
            return None

        except TryOnValidationError:
            raise
        except requests.exceptions.RequestException as e:
            resp = getattr(e, "response", None)
            if resp is not None:
                try:
                    logger.error(
                        f"[multi] Ошибка при запросе к Tsum API: HTTP {resp.status_code}, тело: {resp.text}"
                    )
                except Exception:
                    logger.error(f"[multi] Ошибка при запросе к Tsum API: HTTP {resp.status_code}")
            else:
                logger.error(f"[multi] Ошибка при запросе к Tsum API: {e}")
            return None
        except Exception as e:
            logger.error(f"[multi] Неожиданная ошибка: {e}")
            return None

    def process_tryon(self, 
                      person_image_path: str, 
                      product_image_path: str, 
                      product_id: int = 13728452, 
                      body_part: Optional[str] = "upper",
                      adapter: str = "banana",
                      product_info: Optional[Dict[str, str]] = None) -> Optional[str]:
        """Выполняет примерку и возвращает путь к результату (v3, multipart)"""
        
        prompt_key = adapter if adapter in ["banana", "flux"] else "banana"
        prompt = self.prompts.get(prompt_key, "")
        prompt = self.substitute_prompt_placeholders(prompt, product_info)
        
        try:
            logger.info("Отправляю запрос на примерку (v3, multipart)...")
            tryon_url = f"{self.tryon_base_url}{self.tryon_tryon_path}"
            params = {"body_part": body_part} if body_part else {}
            
            data = {
                "productId": str(product_id),
                "adapter": adapter,
                "prompt": prompt,
            }
            
            # Создаем отдельную сессию на запрос (requests.Session не потокобезопасен)
            session = requests.Session()
            session.headers.update(self.tryon_headers)
            
            with open(person_image_path, "rb") as person_file, open(product_image_path, "rb") as product_file:
                files = {
                    "person": (os.path.basename(person_image_path), person_file, "image/jpeg"),
                    "product": (os.path.basename(product_image_path), product_file, "image/jpeg"),
                }
                response = session.post(
                    tryon_url,
                    headers=session.headers,
                    data=data,
                    files=files,
                    params=params,
                    timeout=60
                )
            
            if response.status_code == 422:
                try:
                    body = response.json()
                    detail = body.get("detail", {})
                    if detail.get("error") == "body_validation_failed":
                        failures = detail.get("failures", [])
                        logger.error(f"[single] body_validation_failed: {failures}")
                        raise TryOnValidationError(failures)
                except (ValueError, AttributeError):
                    pass
            response.raise_for_status()
            
            data = response.json()
            job_id = data.get('job_id')
            
            if not job_id:
                logger.error(f"Не получен job_id. Ответ: {data}")
                return None
                
            logger.info(f"Получен job_id: {job_id}")
            
            # Поллинг результата: до 12 попыток, каждые 5 секунд (до ~60 секунд)
            max_attempts = 12
            poll_interval = 5
            result_url = f"{self.tryon_base_url}{self.tryon_jobs_path}/{job_id}/result"
            
            for attempt in range(1, max_attempts + 1):
                logger.info(f"Проверяю результат (попытка {attempt}/{max_attempts})...")
                try:
                    result_response = session.get(
                        result_url,
                        headers={'accept': 'image/png', 'x-api-key': self.api_key},
                        timeout=60
                    )
                except requests.exceptions.RequestException as e:
                    logger.warning(f"Ошибка при запросе результата: {e}")
                    if attempt == max_attempts:
                        return None
                    time.sleep(poll_interval)
                    continue
                
                if result_response.status_code == 200:
                    temp_result = f"temp_{job_id}.png"
                    with open(temp_result, 'wb') as f:
                        f.write(result_response.content)
                    
                    logger.info(f"Результат получен и сохранен: {temp_result}")
                    return temp_result
                
                logger.info(f"Результат не готов, статус: {result_response.status_code}")
                
                if attempt < max_attempts:
                    time.sleep(poll_interval)
            
            logger.error("Не удалось получить результат за отведенное время")
            return None
            
        except TryOnValidationError:
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка при запросе к Tsum API: {e}")
            return None
        except Exception as e:
            logger.error(f"Неожиданная ошибка: {e}")
            return None

    def process_tryon_with_room(
        self,
        person_image_path: str,
        product_image_path: str,
        room_image_path: str,
        product_id: int = 13728452,
        body_part: Optional[str] = "upper",
        adapter: str = "banana",
        product_info: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """Примерка с дополнительной фотографией примерочной (ROOM_IMAGE).
        
        Примерочная передается как второй product с productId=9999.
        В промте можно ссылаться на PRODUCT_IMAGE (будет два: одежда и примерочная).
        """

        # Используем отдельные промты banana_room / flux_room при наличии
        room_key = f"{adapter}_room"
        if room_key in self.prompts:
            prompt_key = room_key
        else:
            prompt_key = adapter if adapter in ["banana", "flux"] else "banana"

        prompt = self.prompts.get(prompt_key, "")
        prompt = self.substitute_prompt_placeholders(prompt, product_info)
        
        # Логируем промт для проверки (полностью)
        logger.info(f"[room] Используемый промт ({prompt_key}):")
        logger.info(f"[room] {prompt}")
        logger.info(f"[room] Длина промта: {len(prompt)} символов")

        try:
            logger.info("Отправляю запрос на примерку с примерочной (v3, multipart)...")
            logger.info(f"[room] Передаю room как второй product с productId=9999")
            tryon_url = f"{self.tryon_base_url}{self.tryon_tryon_path}"
            params = {"body_part": body_part} if body_part else {}

            # Передаем оба productId: основной товар и примерочная (9999)
            form_data = [
                ("productId", str(product_id)),
                ("productId", "9999"),  # ID для примерочной
                ("adapter", adapter),
                ("prompt", prompt),  # Промт передается в data/prompt
            ]

            session = requests.Session()
            session.headers.update(self.tryon_headers)

            # Открываем все файлы
            person_file = open(person_image_path, "rb")
            product_file = open(product_image_path, "rb")
            room_file = open(room_image_path, "rb")
            
            try:
                # Передаем room как второй product (а не как отдельное поле room)
                # Порядок: person, product (одежда), product (комната)
                files = [
                    ("person", (os.path.basename(person_image_path), person_file, "image/jpeg")),
                    ("product", (os.path.basename(product_image_path), product_file, "image/jpeg")),  # первый product = одежда
                    ("product", (os.path.basename(room_image_path), room_file, "image/jpeg")),  # второй product = примерочная
                ]
                
                logger.info(f"[room] Отправляю запрос:")
                logger.info(f"[room]   - person: {os.path.basename(person_image_path)}")
                logger.info(f"[room]   - product[1] (одежда): {os.path.basename(product_image_path)}")
                logger.info(f"[room]   - product[2] (комната): {os.path.basename(room_image_path)}")
                logger.info(f"[room]   - productId: {product_id} и 9999")
                logger.info(f"[room]   - adapter: {adapter}")
                logger.info(f"[room]   - prompt length: {len(prompt)} символов")
                
                response = session.post(
                    tryon_url,
                    headers=session.headers,
                    data=form_data,
                    files=files,
                    params=params,
                    timeout=60,
                )
            finally:
                person_file.close()
                product_file.close()
                room_file.close()

            if response.status_code == 422:
                try:
                    body = response.json()
                    detail = body.get("detail", {})
                    if detail.get("error") == "body_validation_failed":
                        failures = detail.get("failures", [])
                        logger.error(f"[room] body_validation_failed: {failures}")
                        raise TryOnValidationError(failures)
                except (ValueError, AttributeError):
                    pass
            response.raise_for_status()

            data = response.json()
            job_id = data.get("job_id")

            if not job_id:
                logger.error(f"Не получен job_id. Ответ: {data}")
                return None

            logger.info(f"Получен job_id (room): {job_id}")

            max_attempts = 12
            poll_interval = 5
            result_url = f"{self.tryon_base_url}{self.tryon_jobs_path}/{job_id}/result"

            for attempt in range(1, max_attempts + 1):
                logger.info(f"[room] Проверяю результат (попытка {attempt}/{max_attempts})...")
                try:
                    result_response = session.get(
                        result_url,
                        headers={"accept": "image/png", "x-api-key": self.api_key},
                        timeout=60,
                    )
                except requests.exceptions.RequestException as e:
                    logger.warning(f"[room] Ошибка при запросе результата: {e}")
                    if attempt == max_attempts:
                        return None
                    time.sleep(poll_interval)
                    continue

                if result_response.status_code == 200:
                    temp_result = f"temp_room_{job_id}.png"
                    with open(temp_result, "wb") as f:
                        f.write(result_response.content)

                    logger.info(f"[room] Результат получен и сохранен: {temp_result}")
                    return temp_result

                logger.info(f"[room] Результат не готов, статус: {result_response.status_code}")

                if attempt < max_attempts:
                    time.sleep(poll_interval)

            logger.error("[room] Не удалось получить результат за отведенное время")
            return None

        except TryOnValidationError:
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"[room] Ошибка при запросе к Tsum API: {e}")
            return None
        except Exception as e:
            logger.error(f"[room] Неожиданная ошибка: {e}")
            return None
    
    def upload_to_imageban(self, image_path: str, filename: Optional[str] = None) -> Optional[str]:
        """Загружает изображение на ImageBan и возвращает ссылку"""
        try:
            logger.info(f"Загружаю изображение на ImageBan: {os.path.basename(image_path)}")
            
            # Проверяем размер файла
            file_size = os.path.getsize(image_path)
            logger.info(f"Размер файла: {file_size} байт ({file_size/1024/1024:.2f} MB)")
            
            if file_size > 10 * 1024 * 1024:  # 10 MB
                logger.error("Файл слишком большой (>10 MB)")
                return None
            
            # Простой метод: как в curl примере (бинарный файл с TOKEN)
            with open(image_path, 'rb') as f:
                files = {'image': f}
                
                if filename:
                    files['name'] = (None, filename)
                
                headers = {'Authorization': f'TOKEN {self.imageban_client_id}'}
                
                response = requests.post(
                    self.imageban_api_url,
                    headers=headers,
                    files=files,
                    timeout=60
                )
            
            logger.info(f"Статус ответа: {response.status_code}")
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"Ответ сервера: {result}")
                
                if result.get('success', False):
                    # data может быть объектом или списком
                    data = result.get('data', {})
                    
                    if isinstance(data, dict):
                        link = data.get('link')
                    elif isinstance(data, list) and len(data) > 0:
                        link = data[0].get('link')
                    else:
                        logger.error(f"Неизвестный формат данных: {type(data)}")
                        return None
                    
                    if link:
                        logger.info(f"✓ Успешно загружено на ImageBan!")
                        logger.info(f"  Ссылка: {link}")
                        
                        # Логируем дополнительную информацию
                        if isinstance(data, dict):
                            logger.info(f"  ID: {data.get('id', 'N/A')}")
                            logger.info(f"  Размер: {data.get('size', 'N/A')} байт")
                            logger.info(f"  Разрешение: {data.get('resolution', 'N/A')}")
                            logger.info(f"  Короткая ссылка: {data.get('short_link', 'N/A')}")
                        
                        return link
                    else:
                        logger.error("В ответе нет ссылки")
                else:
                    error_info = result.get('error', {})
                    error_code = error_info.get('code', 'Unknown')
                    error_msg = error_info.get('message', 'Unknown error')
                    logger.error(f"API вернуло ошибку: Код {error_code}, Сообщение: {error_msg}")
            else:
                logger.error(f"Ошибка HTTP {response.status_code}: {response.text}")
            
            return None
            
        except Exception as e:
            logger.error(f"Ошибка при загрузке на ImageBan: {str(e)}")
            traceback.print_exc()
            return None
    
    def save_results_to_table(self, results: List[Dict], output_file: str = "tryon_results.csv"):
        """Сохраняет результаты в CSV таблицу"""
        try:
            import csv
            fieldnames = ['index', 'product_url', 'tryon_result_link', 'status', 'timestamp']
            
            with open(output_file, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                
                for result in results:
                    writer.writerow({
                        'index': result.get('index', ''),
                        'product_url': result.get('product_url', '')[:100],
                        'tryon_result_link': result.get('link', ''),
                        'status': result.get('status', ''),
                        'timestamp': result.get('timestamp', '')
                    })
            
            logger.info(f"Результаты сохранены в таблицу: {output_file}")
            
            # Также сохраняем в JSON для удобства
            json_file = output_file.replace('.csv', '.json')
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(results, f, ensure_ascii=False, indent=2)
            
            logger.info(f"Полные результаты сохранены в: {json_file}")
            
        except Exception as e:
            logger.error(f"Ошибка при сохранении результатов: {e}")
    
    def load_product_info_csv(self, csv_path: str = "product_info.csv") -> Dict[str, Dict[str, str]]:
        """Загружает информацию о товарах из CSV и возвращает словарь {product_id: {info}}"""
        import csv
        result = {}
        if not os.path.exists(csv_path):
            logger.warning(f"Файл с информацией о товарах не найден: {csv_path}")
            return result
        
        try:
            with open(csv_path, "r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    product_id = row.get("product_id", "").strip()
                    if product_id:
                        result[product_id] = {
                            "title": row.get("title", ""),
                            "category_title": row.get("category_title", ""),
                            "color_title": row.get("color_title", ""),
                            "composition": row.get("composition", ""),
                            "w2000_1": row.get("w2000_1", "").strip(),
                            "w2000_2": row.get("w2000_2", "").strip(),
                            "size_info1": row.get("size_info1", ""),
                            "size_info2": row.get("size_info2", ""),
                        }
            logger.info(f"Загружена информация о {len(result)} товарах из {csv_path}")
        except Exception as e:
            logger.error(f"Ошибка при чтении {csv_path}: {e}")
        
        return result

    def process_multi_sets(
        self,
        product_id_sets: List[List[str]],
        person_photo_path: Optional[str] = None,
        output_dir: str = "photoresult",
        temp_dir: str = "temp_photos",
        adapter: str = "banana",
        body_part: Optional[str] = "upper",
        product_info_csv: Optional[str] = "product_info.csv",
    ) -> List[Dict]:
        """
        Обработка мульти-наборов товаров:
        один человек + несколько товаров в каждой строке (наборе).

        ВАЖНО: для мульти-режима мы больше НЕ используем product_info.csv.
        Для каждого product_id запрашиваем данные напрямую из TSUM API.
        """

        # Отдельный импорт здесь, чтобы не ломать обычный сценарий, если enrich_products не нужен
        from enrich_products import fetch_product, extract_product_info

        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(temp_dir, exist_ok=True)

        if not person_photo_path:
            person_photo_path = self.find_person_photo()

        if not person_photo_path or not os.path.exists(person_photo_path):
            logger.error(f"[multi] Фото человека не найдено: {person_photo_path}")
            logger.info("[multi] Поместите фото в папку 'photos' или укажите путь")
            return []

        logger.info(f"[multi] Начинаю обработку {len(product_id_sets)} наборов товаров...")
        logger.info(f"[multi] Фото человека: {person_photo_path}")
        logger.info(f"[multi] Выходная директория: {output_dir}")
        logger.info(f"[multi] Временная директория: {temp_dir}")
        logger.info("[multi] В мульти-режиме product_info.csv игнорируется, данные берём прямо из TSUM API.")

        results: List[Dict] = []

        for set_index, id_set in enumerate(product_id_sets, 1):
            # Чистим набор от пустых значений
            ids = [pid.strip() for pid in id_set if pid.strip()]
            if not ids:
                logger.warning(f"[multi] Набор #{set_index} пустой, пропускаю")
                continue

            logger.info(f"\n{'='*60}")
            logger.info(f"[multi] ОБРАБОТКА НАБОРА #{set_index}: {', '.join(ids)}")
            logger.info(f"{'='*60}")

            result_entry = {
                "index": set_index,
                "product_ids": ids,
                "status": "failed",
                "timestamp": datetime.now().isoformat(),
                "person_path": person_photo_path,
                "product_path": None,  # первое фото товара
                "local_path": None,
                "banana_local_path": None,
                "banana_link": None,
                "banana_status": "pending",
                "flux_local_path": None,
                "flux_link": None,
                "flux_status": "pending",
            }

            try:
                # Собираем информацию и фото по всем товарам в наборе
                product_infos: List[Dict[str, str]] = []
                product_image_paths: List[str] = []

                for i, pid in enumerate(ids, 1):
                    # 1. Запрос в TSUM API
                    raw = fetch_product(pid)
                    if not raw:
                        logger.warning(f"[multi] Не удалось получить данные по товару {pid} из TSUM API, пропускаю его")
                        continue

                    # 2. Извлекаем нужные поля (в т.ч. w2000_1)
                    info = extract_product_info(raw)
                    url = info.get("w2000_1")
                    if not url:
                        logger.warning(f"[multi] Нет w2000_1 для товара {pid} в ответе TSUM API, пропускаю его")
                        continue

                    # 3. Скачиваем фото
                    img_path = self.download_product_image(url, index=1000 * set_index + i, temp_dir=temp_dir)
                    if not img_path:
                        logger.warning(f"[multi] Не удалось скачать фото товара {pid}, пропускаю его")
                        continue

                    product_infos.append(info)
                    product_image_paths.append(img_path)

                if not product_image_paths:
                    logger.error(f"[multi] В наборе #{set_index} не осталось ни одного валидного товара")
                    results.append(result_entry)
                    continue

                # Считаем первым товаром тот, что первый в списке
                result_entry["product_path"] = product_image_paths[0]

                # Для плейсхолдеров используем информацию первого товара набора
                main_product_info = product_infos[0]

                adapters = ["banana", "flux"] if adapter == "both" else [adapter]
                any_success = False

                for ad in adapters:
                    logger.info(f"[multi] Запуск адаптера {ad} для набора #{set_index}")
                    tryon_result_path = self.process_tryon_multi(
                        person_image_path=person_photo_path,
                        product_image_paths=product_image_paths,
                        product_ids=ids,
                        body_part=body_part,
                        adapter=ad,
                        product_info=main_product_info,
                    )

                    key_prefix = ad

                    if not tryon_result_path:
                        result_entry[f"{key_prefix}_status"] = "failed"
                        logger.error(f"[multi] Не удалось выполнить мульти-примерку ({ad}) для набора #{set_index}")
                        continue

                    result_entry[f"{key_prefix}_status"] = "success"

                    # Загружаем результат на ImageBan
                    result_filename = f"tryon_multi_{set_index:04d}_{ad}.png"
                    imageban_link = self.upload_to_imageban(tryon_result_path, result_filename)

                    if imageban_link:
                        result_entry[f"{key_prefix}_link"] = imageban_link
                        logger.info(
                            f"[multi] ✓ Набор #{set_index} ({ad}) успешно загружен: {imageban_link}"
                        )
                    else:
                        logger.error(
                            f"[multi] Не удалось загрузить мульти-результат ({ad}) на ImageBan для набора #{set_index}"
                        )

                    # Сохраняем результат локально
                    final_filename = f"tryon_multi_{set_index:04d}_{ad}.png"
                    final_path = os.path.join(output_dir, final_filename)
                    shutil.copy2(tryon_result_path, final_path)
                    result_entry[f"{key_prefix}_local_path"] = final_path

                    if not result_entry["local_path"]:
                        result_entry["local_path"] = final_path

                    any_success = True

                result_entry["status"] = "success" if any_success else "failed"

            except Exception as e:
                logger.error(f"[multi] Критическая ошибка при обработке набора #{set_index}: {e}")
                traceback.print_exc()

            results.append(result_entry)

        # Сортируем результаты по индексу
        results.sort(key=lambda r: r.get("index", 0))
        return results

    def process_local_photos(self,
                           person_photo_path: Optional[str] = None,
                           product_photos_dir: str = "product-photos",
                           output_dir: str = "photoresult",
                           adapter: str = "banana",
                           body_part: Optional[str] = "upper") -> List[Dict]:
        """Обработка локальных фото товаров из папки product-photos.

        Использует отдельный промпт local_photos и только локальные файлы,
        без обращения к product_info.csv и productids.txt.
        """

        os.makedirs(output_dir, exist_ok=True)

        if not person_photo_path:
            person_photo_path = self.find_person_photo()

        if not person_photo_path or not os.path.exists(person_photo_path):
            logger.error(f"Фото человека не найдено: {person_photo_path}")
            logger.info("Поместите фото в папку 'photos' или укажите путь")
            return []

        if not os.path.exists(product_photos_dir):
            logger.error(f"Папка с фото товаров не найдена: {product_photos_dir}")
            return []

        exts = (".jpg", ".jpeg", ".png", ".webp")
        product_files = sorted(
            [
                os.path.join(product_photos_dir, f)
                for f in os.listdir(product_photos_dir)
                if f.lower().endswith(exts)
            ]
        )

        if not product_files:
            logger.error(f"В папке {product_photos_dir} нет файлов с фото товаров")
            return []

        logger.info(f"Начинаю обработку {len(product_files)} локальных фото товаров...")
        logger.info(f"Фото человека: {person_photo_path}")
        logger.info(f"Папка с фото товаров: {product_photos_dir}")

        results: List[Dict] = []

        def process_one(i: int, product_path: str):
            logger.info(f"\n{'='*60}")
            logger.info(f"ОБРАБОТКА ЛОКАЛЬНОГО ФОТО ТОВАРА #{i}/{len(product_files)}: {product_path}")
            logger.info(f"{'='*60}")

            result_entry = {
                "index": i,
                "product_local_path": product_path,
                "status": "failed",
                "timestamp": datetime.now().isoformat(),
                "person_path": person_photo_path,
                "product_path": product_path,
                "local_path": None,
                "banana_local_path": None,
                "banana_link": None,
                "banana_status": "pending",
                "flux_local_path": None,
                "flux_link": None,
                "flux_status": "pending",
            }

            try:
                adapters = ["banana", "flux"] if adapter == "both" else [adapter]
                any_success = False

                def run_adapter(ad: str):
                    logger.info(f"Запуск адаптера {ad} для локального фото товара #{i}")
                    # Для локальных фото используем специальный промпт local_photos
                    # через process_tryon_with_example без второго фото
                    return ad, self.process_tryon_with_example(
                        person_image_path=person_photo_path,
                        product_image_path=product_path,
                        product_image2_path=None,
                        product_id=0,
                        adapter=ad,
                        body_part=body_part,
                        product_info=None,
                    )

                from concurrent.futures import ThreadPoolExecutor, as_completed

                with ThreadPoolExecutor(max_workers=len(adapters)) as executor:
                    futures = [executor.submit(run_adapter, ad) for ad in adapters]
                    for future in as_completed(futures):
                        try:
                            ad, tryon_result_path = future.result()
                        except Exception as e:
                            logger.error(f"Ошибка в потоке адаптера (локальные фото): {e}")
                            continue

                        key_prefix = ad

                        if not tryon_result_path:
                            result_entry[f"{key_prefix}_status"] = "failed"
                            logger.error(f"Не удалось выполнить примерку ({ad}) для локального товара #{i}")
                            continue

                        result_entry[f"{key_prefix}_status"] = "success"

                        final_filename = f"tryon_local_{i:04d}_{ad}.png"
                        final_path = os.path.join(output_dir, final_filename)
                        shutil.copy2(tryon_result_path, final_path)
                        result_entry[f"{key_prefix}_local_path"] = final_path

                        if not result_entry["local_path"]:
                            result_entry["local_path"] = final_path

                        any_success = True

                result_entry["status"] = "success" if any_success else "failed"

            except Exception as e:
                logger.error(f"Критическая ошибка при обработке локального товара #{i}: {e}")
                traceback.print_exc()

            return result_entry

        max_workers = min(10, len(product_files))
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(process_one, i, path) for i, path in enumerate(product_files, 1)]
            for fut in as_completed(futures):
                results.append(fut.result())

        results.sort(key=lambda r: r.get("index", 0))
        return results

    def process_all(self, 
                   person_photo_path: Optional[str] = None,
                   output_dir: str = "photoresult",
                   temp_dir: str = "temp_photos",
                   adapter: str = "banana",
                   body_part: Optional[str] = "upper",
                   product_info_csv: Optional[str] = "product_info.csv",
                   product_ids_file: Optional[str] = "productids.txt",
                   product_ids_override: Optional[List[str]] = None,
                   product_photo_key: str = "w2000_1",
                   room_dir: Optional[str] = None) -> List[Dict]:
        """Основная функция обработки всех товаров (без ограничений по количеству)"""
        
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(temp_dir, exist_ok=True)

        use_room = room_dir is not None
        room_image_path: Optional[str] = None
        if use_room:
            room_image_path = self.find_room_photo(room_dir or "room")
            if not room_image_path or not os.path.exists(room_image_path):
                logger.error(f"[room] Фото примерочной не найдено в папке '{room_dir or 'room'}'")
                logger.info("[room] Отключаю режим примерочной и продолжаю обычную примерку.")
                use_room = False
        
        if not person_photo_path:
            person_photo_path = self.find_person_photo()
        
        if not person_photo_path or not os.path.exists(person_photo_path):
            logger.error(f"Фото человека не найдено: {person_photo_path}")
            logger.info("Поместите фото в папку 'photos' или укажите путь")
            return []
        
        # Загружаем список ID товаров
        product_ids_list: List[str] = []
        if product_ids_override:
            product_ids_list = [pid for pid in product_ids_override if pid]
        else:
            if product_ids_file and os.path.exists(product_ids_file):
                try:
                    with open(product_ids_file, "r", encoding="utf-8") as f:
                        product_ids_list = [line.strip() for line in f if line.strip()]
                except Exception as e:
                    logger.error(f"Не удалось прочитать ID товаров из {product_ids_file}: {e}")
                    return []
            else:
                logger.error(f"Файл с ID товаров не найден: {product_ids_file}")
                return []
        
        if not product_ids_list:
            logger.error(f"Нет ID товаров в файле {product_ids_file}")
            return []
        
        # Загружаем информацию о товарах из CSV
        product_info_map = self.load_product_info_csv(product_info_csv) if product_info_csv else {}
        if not product_info_map:
            logger.error(f"Не удалось загрузить информацию о товарах из {product_info_csv}")
            return []
        
        logger.info(f"Начинаю обработку {len(product_ids_list)} товаров...")
        logger.info(f"Фото человека: {person_photo_path}")
        logger.info(f"Выходная директория: {output_dir}")
        logger.info(f"Временная директория: {temp_dir}")
        logger.info(f"Загружена информация о {len(product_info_map)} товарах из CSV")
        
        results = []

        def process_one(i: int, product_id: str):
            logger.info(f"\n{'='*60}")
            logger.info(f"ОБРАБОТКА ТОВАРА #{i}/{len(product_ids_list)} (ID: {product_id})")
            logger.info(f"{'='*60}")

            result_entry = {
                'index': i,
                'product_id': product_id,
                'status': 'failed',
                'timestamp': datetime.now().isoformat(),
                'person_path': person_photo_path,
                'product_path': None,
                'local_path': None,  # общее поле (первый удачный результат)
                'banana_local_path': None,
                'banana_link': None,
                'banana_status': 'pending',
                'flux_local_path': None,
                'flux_link': None,
                'flux_status': 'pending',
                'gemini_local_path': None,
                'gemini_link': None,
                'gemini_status': 'pending',
            }

            try:
                # 1. Получаем информацию о товаре из CSV
                product_info = product_info_map.get(product_id)
                if not product_info:
                    logger.error(f"Информация о товаре #{i} (ID: {product_id}) не найдена в CSV")
                    return result_entry
                
                # 2. Проверяем наличие фото товара (по выбору 1/2)
                photo_key = product_photo_key or "w2000_1"
                product_image_url = (
                    product_info.get(photo_key)
                    or product_info.get("w2000_1")
                    or product_info.get("w2000_2")
                )
                if not product_image_url:
                    logger.error(f"В CSV нет {photo_key} для товара #{i} (ID: {product_id})")
                    return result_entry
                
                logger.info(f"Использую фото товара из CSV: {product_image_url[:60]}...")
                
                # 3. Скачиваем фото товара
                product_image_path = self.download_product_image(product_image_url, i, temp_dir)
                if not product_image_path:
                    logger.error(f"Не удалось скачать фото товара #{i}")
                    return result_entry
                result_entry['product_path'] = product_image_path
                
                # 4. Выбираем адаптеры (модели) для сравнения
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
                any_success = False

                def run_adapter(ad: str):
                    logger.info(f"Запуск адаптера {ad} для товара #{i}")
                    if use_room and room_image_path:
                        tryon_result_path = self.process_tryon_with_room(
                            person_image_path=person_photo_path,
                            product_image_path=product_image_path,
                            room_image_path=room_image_path,
                            product_id=int(product_id) if str(product_id).isdigit() else 0,
                            adapter=ad,
                            body_part=body_part,
                            product_info=product_info,
                        )
                    else:
                        tryon_result_path = self.process_tryon(
                            person_image_path=person_photo_path,
                            product_image_path=product_image_path,
                            adapter=ad,
                            body_part=body_part,
                            product_info=product_info
                        )
                    return ad, tryon_result_path

                with ThreadPoolExecutor(max_workers=len(adapters)) as executor:
                    futures = [executor.submit(run_adapter, ad) for ad in adapters]
                    for future in as_completed(futures):
                        try:
                            ad, tryon_result_path = future.result()
                        except Exception as e:
                            logger.error(f"Ошибка в потоке адаптера: {e}")
                            continue

                        key_prefix = "gemini" if "gemini" in ad else ad

                        if not tryon_result_path:
                            result_entry[f"{key_prefix}_status"] = 'failed'
                            logger.error(f"Не удалось выполнить примерку ({ad}) для товара #{i}")
                            continue

                        result_entry[f"{key_prefix}_status"] = 'success'

                        # 5. Загружаем результат на ImageBan
                        result_filename = f"tryon_result_{i:04d}_{ad}.png"
                        imageban_link = self.upload_to_imageban(tryon_result_path, result_filename)

                        if imageban_link:
                            result_entry[f"{key_prefix}_link"] = imageban_link
                            logger.info(f"✓ Товар #{i} ({ad}) успешно загружен: {imageban_link}")
                        else:
                            logger.error(f"Не удалось загрузить результат ({ad}) на ImageBan для товара #{i}")

                        # 6. Сохраняем результат локально
                        final_filename = f"tryon_{i:04d}_{ad}.png"
                        final_path = os.path.join(output_dir, final_filename)
                        shutil.copy2(tryon_result_path, final_path)
                        result_entry[f"{key_prefix}_local_path"] = final_path

                        if not result_entry['local_path']:
                            result_entry['local_path'] = final_path

                        any_success = True

                result_entry['status'] = 'success' if any_success else 'failed'

            except Exception as e:
                logger.error(f"Критическая ошибка при обработке товара #{i}: {e}")
                traceback.print_exc()

            return result_entry

        # Параллельная обработка товаров
        max_workers = min(10, len(product_ids_list))  # увеличили параллелизм до 10
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(process_one, i, product_id) for i, product_id in enumerate(product_ids_list, 1)]
            for fut in as_completed(futures):
                results.append(fut.result())

        # Сортируем результаты по индексу для стабильного вывода
        results.sort(key=lambda r: r.get('index', 0))
        
        # Сохраняем результаты в таблицу
        self.save_results_to_table(results, os.path.join(output_dir, "tryon_results.csv"))
        
        # Выводим статистику
        total = len(results)
        success = sum(1 for r in results if r.get('status') == 'success')
        failed = total - success
        
        logger.info(f"\n{'='*60}")
        logger.info("СТАТИСТИКА ОБРАБОТКИ")
        logger.info(f"{'='*60}")
        logger.info(f"Всего товаров: {total}")
        logger.info(f"Успешно: {success} ({success/total*100:.1f}%)")
        logger.info(f"Не удалось: {failed} ({failed/total*100:.1f}%)")
        
        if success > 0:
            logger.info(f"\nСсылки на результаты (CDN):")
            for result in results:
                if result.get('status') == 'success' and result.get('link'):
                    logger.info(f"  Товар #{result['index']}: {result['link']}")
        
        return results


def main():
    """Точка входа в программу"""
    print("╔══════════════════════════════════════════════════════╗")
    print("║        TSUM TRY-ON + IMAGEBAN АВТОМАТ                ║")
    print("║  Примерка одежды + загрузка на CDN                   ║")
    print("╚══════════════════════════════════════════════════════╝")
    print()
    
    parser = argparse.ArgumentParser(description='Автоматическая примерка одежды Tsum и загрузка на CDN')
    parser.add_argument('--person', type=str,
                       help='Путь к фото человека')
    parser.add_argument('--output-dir', type=str, default='photoresult',
                       help='Директория для результатов (по умолчанию: photoresult)')
    parser.add_argument('--temp-dir', type=str, default='temp_photos',
                       help='Директория для временных файлов (по умолчанию: temp_photos)')
    parser.add_argument('--adapter', type=str, default='banana', choices=['banana', 'flux', 'both'],
                       help='Модель/адаптер: banana, flux или both (по умолчанию banana)')
    parser.add_argument('--prompts-file', type=str, default='prompts.txt',
                       help='Файл с промтами (по умолчанию: prompts.txt)')
    parser.add_argument('--body-part', type=str, default='upper',
                       help='Часть тела для примерки (upper|lower), по умолчанию upper')
    parser.add_argument('--debug', action='store_true',
                       help='Включить подробное логирование')
    
    args = parser.parse_args()
    
    if args.debug:
        logger.setLevel(logging.DEBUG)
        logger.debug("Включен режим отладки")
    
    processor = TsumTryOnProcessor(
        prompts_file=args.prompts_file
    )
    
    if not os.path.exists("photos"):
        os.makedirs("photos")
        logger.info("Создана папка 'photos'. Поместите туда фото человека.")
    
    logger.info("Начинаю обработку...")
    
    try:
        results = processor.process_all(
            person_photo_path=args.person,
            output_dir=args.output_dir,
            temp_dir=args.temp_dir,
            adapter=args.adapter,
            body_part=args.body_part,
        )
        
        logger.info("\n" + "="*60)
        logger.info("РАБОТА ЗАВЕРШЕНА!")
        logger.info("="*60)
        
        if results:
            csv_path = os.path.join(args.output_dir, "tryon_results.csv")
            logger.info(f"\n📁 РЕЗУЛЬТАТЫ СОХРАНЕНЫ:")
            logger.info(f"  1. {csv_path} - таблица со ссылками на CDN")
            logger.info(f"  2. {args.output_dir}/ - папка с локальными изображениями")
            logger.info(f"  3. {args.temp_dir}/ - папка с временными файлами")
            logger.info(f"  4. tryon_processor.log - лог-файл с деталями работы")
            
            open_folder = input("\nОткрыть папку с результатами? (да/нет): ").strip().lower()
            if open_folder in ['да', 'д', 'yes', 'y']:
                os.startfile(args.output_dir)
    
    except KeyboardInterrupt:
        logger.info("\n⚠ Обработка прервана пользователем")
    except Exception as e:
        logger.error(f"⚠ Критическая ошибка: {e}")
        traceback.print_exc()


if __name__ == "__main__":
    main()