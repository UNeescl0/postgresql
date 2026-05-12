@echo off
set PGPASSWORD= put_your_password_here
set PG_PATH=C:\Program Files\PostgreSQL\18\bin
set BACKUP_DIR=C:\Coffre_PostgreSQL\sauvegardes
set DB_USER=put_your_username
set DB_HOST=localhost
set DB_PORT=5432
set DB_NAME=ma_base_test

for /f "tokens=2 delims==" %%a in ('wmic OS Get localdatetime /value') do set dt=%%a
set DATETIME=%dt:~0,4%-%dt:~4,2%-%dt:~6,2%_%dt:~8,2%-%dt:~10,2%

if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"

echo Sauvegarde en cours de %DB_NAME%...

"%PG_PATH%\pg_dump.exe" -U %DB_USER% -h %DB_HOST% -p %DB_PORT% -F c -b -f "%BACKUP_DIR%\%DB_NAME%_%DATETIME%.backup" %DB_NAME%

if %errorlevel% == 0 (
    echo Sauvegarde reussie !
    echo Fichier sauvegarde dans : %BACKUP_DIR%\%DB_NAME%_%DATETIME%.backup
) else (
    echo Erreur lors de la sauvegarde !
)

pause
