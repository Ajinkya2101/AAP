@echo off
REM ==========================================================================
REM  AAP Module - start the Docker Compose stack on Windows (Docker Desktop,
REM  WSL2 back end). See docs\IMPLEMENTATION-PLAN.md section 10.
REM
REM  Usage:
REM    start_container.bat              build + start (GPU mode; falls back to
REM                                     CPU/stub mode if no NVIDIA GPU found)
REM    start_container.bat --cpu        start without vLLM, LLM_BACKEND=stub
REM    start_container.bat --seed       force re-seed of the mock EBS database
REM    start_container.bat --reset      delete all volumes first (asks to confirm)
REM    start_container.bat --yes        do not ask for confirmation
REM    start_container.bat --no-build   do not rebuild images
REM    start_container.bat stop         stop all services
REM    start_container.bat status       show service status
REM    start_container.bat logs [svc]   follow logs (all services or one)
REM ==========================================================================
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "ACTION=up"
set "MODE=gpu"
set "RESET=0"
set "SEED=0"
set "ASSUME_YES=0"
set "BUILD=--build"
set "LOG_SERVICE="
set "API_URL=http://localhost:8080"
set "HEALTH_TRIES=60"

REM ---------------------------------------------------------------- arguments
:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--cpu"      (set "MODE=cpu"      & shift & goto :parse_args)
if /i "%~1"=="--gpu"      (set "MODE=gpu"      & shift & goto :parse_args)
if /i "%~1"=="--seed"     (set "SEED=1"        & shift & goto :parse_args)
if /i "%~1"=="--reset"    (set "RESET=1"       & shift & goto :parse_args)
if /i "%~1"=="--yes"      (set "ASSUME_YES=1"  & shift & goto :parse_args)
if /i "%~1"=="--no-build" (set "BUILD="        & shift & goto :parse_args)
if /i "%~1"=="stop"       (set "ACTION=stop"   & shift & goto :parse_args)
if /i "%~1"=="status"     (set "ACTION=status" & shift & goto :parse_args)
if /i "%~1"=="logs"       (set "ACTION=logs"   & set "LOG_SERVICE=%~2" & goto :args_done)
if /i "%~1"=="help"       goto :usage
if /i "%~1"=="--help"     goto :usage
if /i "%~1"=="-h"         goto :usage
echo [ERROR] Unknown argument: %~1
goto :usage_error

:args_done

REM ------------------------------------------------------------ prerequisites
where docker >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker CLI not found. Install Docker Desktop with the WSL2 back end.
    exit /b 1
)

docker compose version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Compose v2 not found. Update Docker Desktop.
    exit /b 1
)

call :ensure_docker_running
if errorlevel 1 exit /b 1

if not exist "docker-compose.yml" (
    echo [ERROR] docker-compose.yml not found in %CD%.
    echo         It is created in Phase 0 of docs\IMPLEMENTATION-PLAN.md.
    exit /b 1
)

REM All profiles, so stop/status also cover the GPU services.
set "ALL_PROFILES=--profile gpu --profile gpu2"

if /i "%ACTION%"=="stop" (
    echo [INFO] Stopping all services...
    docker compose %ALL_PROFILES% down
    exit /b !errorlevel!
)
if /i "%ACTION%"=="status" (
    docker compose %ALL_PROFILES% ps
    exit /b !errorlevel!
)
if /i "%ACTION%"=="logs" (
    docker compose %ALL_PROFILES% logs -f --tail 200 %LOG_SERVICE%
    exit /b !errorlevel!
)

REM ------------------------------------------------------------------- .env
if not exist ".env" (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo [INFO] Created .env from .env.example. Review it before production use.
    ) else (
        echo [WARN] No .env or .env.example found. Compose defaults will be used.
    )
)

REM ------------------------------------------------------------ GPU detection
if /i "%MODE%"=="gpu" (
    where nvidia-smi >nul 2>&1
    if errorlevel 1 (
        echo [WARN] nvidia-smi not found. Falling back to CPU mode with the stub LLM.
        set "MODE=cpu"
    ) else (
        for /f "usebackq tokens=*" %%g in (`nvidia-smi --query-gpu^=name^,memory.total^,driver_version --format^=csv^,noheader 2^>nul`) do (
            echo [INFO] GPU: %%g
        )
    )
)

if /i "%MODE%"=="gpu" (
    set "PROFILES=--profile gpu"
    set "LLM_BACKEND=vllm"
) else (
    set "PROFILES="
    set "LLM_BACKEND=stub"
)
echo [INFO] Mode: %MODE%  ^|  LLM_BACKEND=!LLM_BACKEND!

REM ------------------------------------------------- Docker VM memory check
REM MemTotal is in bytes. Dropping the last 9 digits gives approximate GB
REM without overflowing the 32-bit arithmetic of set /a.
set "DOCKER_MEM="
for /f "usebackq tokens=*" %%m in (`docker info --format "{{.MemTotal}}" 2^>nul`) do set "DOCKER_MEM=%%m"
if defined DOCKER_MEM (
    set "MEM_GB=!DOCKER_MEM:~0,-9!"
    if "!MEM_GB!"=="" set "MEM_GB=0"
    echo [INFO] Docker VM memory: ~!MEM_GB! GB
    if !MEM_GB! LSS 20 (
        echo [WARN] Docker VM has less than 20 GB RAM. vLLM may fail to load.
        echo        Set memory=24GB under [wsl2] in %%UserProfile%%\.wslconfig, then run: wsl --shutdown
    )
)

REM ------------------------------------------------------------------ reset
if "%RESET%"=="1" (
    if not "%ASSUME_YES%"=="1" (
        echo.
        echo [WARN] --reset deletes ALL volumes: databases, filestore and downloaded models.
        set "CONFIRM="
        set /p "CONFIRM=Type YES to continue: "
        if /i not "!CONFIRM!"=="YES" (
            echo [INFO] Reset cancelled.
            exit /b 1
        )
    )
    echo [INFO] Removing containers and volumes...
    docker compose %ALL_PROFILES% down -v --remove-orphans
    if errorlevel 1 exit /b 1
)

REM --------------------------------------------------------------------- up
echo [INFO] Starting services...
docker compose %PROFILES% up -d %BUILD% --remove-orphans
if errorlevel 1 (
    echo [ERROR] docker compose up failed. Run: start_container.bat logs
    exit /b 1
)

if "%SEED%"=="1" (
    echo [INFO] Re-seeding the mock EBS database...
    docker compose run --rm migrate python -m aap.ebs_mock.seed --force
    if errorlevel 1 (
        echo [ERROR] Seeding failed.
        exit /b 1
    )
    echo [INFO] Refreshing the reference data copy...
    docker compose run --rm migrate python -m aap.refsync --full
    if errorlevel 1 (
        echo [ERROR] Reference sync failed.
        exit /b 1
    )
)

REM ------------------------------------------------------------ health wait
echo [INFO] Waiting for the API at %API_URL%/health ...
set /a TRY=0
:wait_api
set /a TRY+=1
curl -fs "%API_URL%/health" >nul 2>&1
if not errorlevel 1 goto :api_ready
if !TRY! GEQ %HEALTH_TRIES% (
    echo [ERROR] API not healthy after %HEALTH_TRIES% attempts. Run: start_container.bat logs api
    exit /b 1
)
ping -n 6 127.0.0.1 >nul
goto :wait_api

:api_ready
echo [OK] API is healthy.

if /i "%MODE%"=="gpu" (
    docker compose exec -T vllm curl -fs "http://localhost:8000/health" >nul 2>&1
    if errorlevel 1 (
        echo [INFO] vLLM is still loading the model. The first start can take several minutes.
        echo        Follow progress with: start_container.bat logs vllm
    ) else (
        echo [OK] vLLM is ready.
    )
)

echo.
echo ================================================================
echo  AAP stack is running  [mode: %MODE%]
echo ----------------------------------------------------------------
echo   Dashboard       %API_URL%/ui
echo   API docs        %API_URL%/docs
echo   PostgreSQL      localhost:5432   (databases: aap, ebs)
if /i "%MODE%"=="gpu" echo   vLLM            http://localhost:8100/v1  (VLLM_HOST_PORT)
echo ----------------------------------------------------------------
echo   Stop:    start_container.bat stop
echo   Status:  start_container.bat status
echo   Logs:    start_container.bat logs [service]
echo ================================================================
exit /b 0

REM ============================================================ subroutines
:ensure_docker_running
docker info >nul 2>&1
if not errorlevel 1 exit /b 0

echo [INFO] Docker engine is not running. Starting Docker Desktop...
set "DD_EXE=%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
if not exist "%DD_EXE%" (
    echo [ERROR] Docker Desktop not found at "%DD_EXE%". Start it manually and retry.
    exit /b 1
)
start "" "%DD_EXE%"

set /a DTRY=0
:wait_docker
set /a DTRY+=1
docker info >nul 2>&1
if not errorlevel 1 (
    echo [OK] Docker engine is running.
    exit /b 0
)
if !DTRY! GEQ 40 (
    echo [ERROR] Docker engine did not start within about 2 minutes.
    exit /b 1
)
ping -n 4 127.0.0.1 >nul
goto :wait_docker

:usage
echo Usage: start_container.bat [--cpu^|--gpu] [--seed] [--reset] [--yes] [--no-build]
echo        start_container.bat stop ^| status ^| logs [service]
exit /b 0

:usage_error
echo Usage: start_container.bat [--cpu^|--gpu] [--seed] [--reset] [--yes] [--no-build]
echo        start_container.bat stop ^| status ^| logs [service]
exit /b 1
