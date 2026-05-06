@echo off
chcp 65001 >nul

:: Проверка существования окружения
if not exist "whispersenv\Scripts\activate.bat" (
    echo [ОШИБКА] Виртуальное окружение whispersenv не найдено!
    echo Сначала запустите setup_whispersenv.bat
    pause
    exit /b 1
)

echo ========================================
echo Запуск проекта в whispersenv
echo ========================================

:: Активация окружения
call whispersenv\Scripts\activate.bat

echo [OK] Виртуальное окружение активировано
echo [OK] Python: 
python --version
echo.

:: Запуск вашего проекта
:: ЗАМЕНИТЕ ЭТУ СТРОКУ НА КОМАНДУ ЗАПУСКА ВАШЕГО ПРОЕКТА
echo Запуск проекта...
python whispers.py --id notAlice --port 5002 --peers Alice:92.242.112.46:5001

:: Альтернативные варианты запуска (раскомментируйте нужное):
:: python app.py
:: python -m your_module
:: streamlit run app.py
:: flask run
:: uvicorn main:app --reload

:: Сохранение кода ошибки
set EXIT_CODE=%errorlevel%

:: Деактивация окружения
call deactivate

if %EXIT_CODE% neq 0 (
    echo.
    echo [ЗАВЕРШЕНО] Проект завершился с ошибкой (код: %EXIT_CODE%)
) else (
    echo.
    echo [ЗАВЕРШЕНО] Проект успешно выполнен
)

pause
exit /b %EXIT_CODE%