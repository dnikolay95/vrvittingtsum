@echo off
chcp 65001 > nul
cls

echo ========================================
echo     TSUM TRY-ON + IMAGEBAN АВТОМАТ
echo ========================================
echo.

echo Проверяю наличие Python...
python --version
if errorlevel 1 (
    echo ОШИБКА: Python не найден!
    echo Установите Python с сайта python.org
    echo Обязательно поставьте галочку "Add Python to PATH"
    pause
    exit
)

echo.
echo Проверяю необходимые библиотеки...
python -c "import requests" 2>nul
if errorlevel 1 (
    echo Устанавливаю библиотеки...
    pip install requests
)

echo.
echo Создаю необходимые папки...
if not exist "photos" mkdir photos
if not exist "photoresult" mkdir photoresult

echo.
echo ПРОВЕРЬТЕ:
echo 1. В папке "photos" должно быть фото человека
echo 2. В файле "producturl.txt" должны быть ссылки на фото товаров
echo.

pause

echo.
echo ЗАПУСКАЮ ОБРАБОТКУ...
echo ========================================
python tryon_processor.py

echo.
pause