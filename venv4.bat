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
call whispersenv\Scripts\activate

echo [OK] Виртуальное окружение активировано
echo [OK] Python: 
python --version
echo.

pip install zeroconf

pause
exit /b %EXIT_CODE%