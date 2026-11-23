@echo off
REM ==========================================================================
REM  AAP Module - fine-tuning jobs in the training container (aap-train:dev).
REM  Separate from start_container.bat: its own image (torch / CUDA 12.8 / peft /
REM  bitsandbytes), compose service "train" (profile "train"). Commands that use
REM  the GPU stop vLLM first and start it again afterwards (only if it was
REM  running). Paths are relative to training\ (configs\..., runs\...).
REM  Docs: training\README.md, docs\PLAN-TRAINING-PIPELINE.md.
REM
REM  Usage:
REM    train.bat build                          build the image
REM    train.bat check                          GPU / bf16 / 4-bit / memory / disk check
REM    train.bat test                           the training unit tests
REM    train.bat smoke                          tiny model end to end (seconds; vLLM keeps running)
REM    train.bat dry  CONFIG [--set k=v ...]  config + dataset check, token counts (no weights)
REM    train.bat run  CONFIG [--set k=v ...]  a training run                       [GPU]
REM    train.bat resume RUN_FOLDER             continue an interrupted run          [GPU]
REM    train.bat sweep SWEEP.yaml [--dry-run] [--max-trials N]   HPO              [GPU]
REM    train.bat resume-sweep SWEEP_FOLDER [--max-trials N]                       [GPU]
REM    train.bat report SWEEP_FOLDER          a sweep's leaderboard
REM    train.bat eval RUN_FOLDER [--checkpoint best or base] [--mode loss or generate]          [GPU]
REM    train.bat package RUN_FOLDER --slot read or decide                                    [GPU]
REM    train.bat shell                          a shell in the container
REM ==========================================================================
setlocal EnableExtensions
cd /d "%~dp0"

set "CMD=%~1"
if "%CMD%"=="" goto :usage
set "ARGS=%*"
call set "REST=%%ARGS:*%1=%%"
if defined REST set "REST=%REST:\=/%"
set "RUN=docker compose --profile train run --rm train"
set "VLLM_WAS_RUNNING=0"
set "RC=0"

if /i "%CMD%"=="build"        goto :build
if /i "%CMD%"=="check"        (%RUN% python -m aap_train.envcheck & exit /b)
if /i "%CMD%"=="test"         (%RUN% python -m pytest -q tests & exit /b)
if /i "%CMD%"=="smoke"        goto :smoke
if /i "%CMD%"=="dry"          (%RUN% python -m aap_train.train --dry-run --config %REST% & exit /b)
if /i "%CMD%"=="report"       (%RUN% python -m aap_train.sweep --report %REST% & exit /b)
if /i "%CMD%"=="shell"        (%RUN% bash & exit /b)
if /i "%CMD%"=="run"          (set "JOB=python -m aap_train.train --config %REST%" & goto :gpu_job)
if /i "%CMD%"=="resume"       (set "JOB=python -m aap_train.train --config dummy --resume %REST%" & goto :gpu_job)
if /i "%CMD%"=="sweep"        goto :sweep
if /i "%CMD%"=="resume-sweep" (set "JOB=python -m aap_train.sweep --resume %REST%" & goto :gpu_job)
if /i "%CMD%"=="eval"         (set "JOB=python -m aap_train.evaluate --run %REST%" & goto :gpu_job)
if /i "%CMD%"=="package"      (set "JOB=python -m aap_train.package --run %REST%" & goto :gpu_job)
echo [ERROR] Unknown command: %CMD%
goto :usage

:build
docker build -f training\Dockerfile -t aap-train:dev training
exit /b %ERRORLEVEL%

:smoke
REM the tiny random model comes from the Hugging Face hub once (then the hf-cache volume holds it)
docker compose --profile train run --rm -e HF_HUB_OFFLINE=0 train python -m aap_train.train --config configs/smoke-tiny.yaml --skip-envcheck
exit /b %ERRORLEVEL%

:sweep
echo %REST% | findstr /c:"--dry-run" >nul
if not errorlevel 1 (%RUN% python -m aap_train.sweep --sweep %REST% & exit /b)
set "JOB=python -m aap_train.sweep --sweep %REST%"
goto :gpu_job

:gpu_job
set "VLLM_ID="
for /f %%i in ('docker ps -q --filter "name=^aap-vllm-1$" --filter "status=running"') do set "VLLM_ID=%%i"
if defined VLLM_ID (
    set "VLLM_WAS_RUNNING=1"
    echo [INFO] Stopping vLLM for the GPU ^(started again when the job ends^)...
    docker compose --profile gpu stop vllm
)
echo [INFO] %JOB%
%RUN% %JOB%
set "RC=%ERRORLEVEL%"
if "%VLLM_WAS_RUNNING%"=="1" (
    echo [INFO] Starting vLLM again...
    docker compose --profile gpu start vllm
)
if not "%RC%"=="0" echo [ERROR] The job ended with code %RC%.
exit /b %RC%

:usage
findstr /b /c:"REM    train.bat" "%~f0"
exit /b 1
