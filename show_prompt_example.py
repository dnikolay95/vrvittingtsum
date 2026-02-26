from tryon_processor import TsumTryOnProcessor
import csv

# Инициализируем процессор
processor = TsumTryOnProcessor()

# ID 6-го товара из productids.txt
product_id = "13754638"

# Загружаем информацию о товарах
info_map = processor.load_product_info_csv("product_info.csv")
info = info_map.get(product_id, {})

# Берем промпт banana
prompt_template = processor.prompts.get("banana", "")

# Подставляем данные
result = processor.substitute_prompt_placeholders(prompt_template, info)

print("=" * 70)
print("ПРОМПТ ДЛЯ 6-ГО ТОВАРА (ID: 13754638)")
print("=" * 70)
print()
print("ИСХОДНЫЙ ПРОМПТ (из prompts.txt):")
print("-" * 70)
print(prompt_template)
print()
print("ДАННЫЕ ТОВАРА (из product_info.csv):")
print("-" * 70)
if info:
    print(f"  title: {info.get('title', 'НЕТ')}")
    print(f"  color_title: {info.get('color_title', 'НЕТ')}")
    print(f"  category_title: {info.get('category_title', 'НЕТ')}")
    print(f"  composition: {info.get('composition', 'НЕТ')[:100]}...")
else:
    print("  ⚠ Товар не найден в product_info.csv")
    print("  Запустите обогащение товаров, чтобы получить данные")
print()
print("=" * 70)
print("РЕЗУЛЬТАТ ПОДСТАНОВКИ (что отправляется в API):")
print("=" * 70)
print(result)
print()

