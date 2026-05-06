@echo off
setlocal enabledelayedexpansion
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

:: Проверка аргументов командной строки
if "%~1"=="" (
    echo [ИНФО] Аргументы не переданы. Запрашиваю параметры...
    echo.
    
    :: Запрос ID пользователя
    set /p USER_ID="Введите ваш уникальный ID: "
    
    :: Запрос порта
    set /p USER_PORT="Введите порт (например 5000): "
    
    echo.
    echo Запуск проекта с ID=!USER_ID! и PORT=!USER_PORT!...
    python whispers.py --id !USER_ID! --port !USER_PORT! %2 %3 %4 %5 %6 %7 %8 %9
) else (
    echo Запуск проекта с переданными аргументами...
    python whispers.py %*
)

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
endlocal
exit /b %EXIT_CODE%