@echo off
rem Build a CodeCoreMorph toolchain image locally (SPEC 7.8, D-23).
rem POSIX twin: scripts/build-image.sh (same flags, messages and exit codes).
setlocal

set "CCM_LIB=%~dp0lib\common.bat"
for %%I in ("%~dp0..") do set "CCM_REPO=%%~fI"

call "%CCM_LIB%" :parse_args %*
if errorlevel 1 exit /b %errorlevel%
if "%CCM_HELP%"=="1" goto usage
call "%CCM_LIB%" :no_offline "the build downloads the pinned tools inside Docker"
if errorlevel 1 exit /b %errorlevel%
set "LANGUAGE="
set "LANGUAGE_COUNT=0"
for %%a in (%CCM_POSITIONAL%) do call :count_language %%a
if not "%LANGUAGE_COUNT%"=="1" goto expected_one_language
set "DOCKERFILE=%CCM_REPO%\images\%LANGUAGE%\Dockerfile"
if not exist "%DOCKERFILE%" goto no_definition
set "TAG=codecoremorph-%LANGUAGE%:local"

if not "%CCM_DRY_RUN%"=="1" goto build
call "%CCM_LIB%" :log "would run: docker build -f %DOCKERFILE% -t %TAG% %CCM_REPO%"
exit /b 0

:build
call "%CCM_LIB%" :require_cmd docker "Install Docker (Docker Desktop on Windows and macOS), then open a new terminal."
if errorlevel 1 exit /b %errorlevel%
docker info >nul 2>&1
if errorlevel 1 goto docker_unreachable
call "%CCM_LIB%" :log "building %TAG% (this downloads the pinned toolchain inside Docker)"
docker build -f "%DOCKERFILE%" -t "%TAG%" "%CCM_REPO%"
if errorlevel 1 goto build_failed
call "%CCM_LIB%" :log "built %TAG%"
exit /b 0


:count_language
set /a LANGUAGE_COUNT+=1
set "LANGUAGE=%~1"
exit /b 0

:expected_one_language
call "%CCM_LIB%" :die 2 "expected one language, for example: build-image cpp"
exit /b 2

:no_definition
call "%CCM_LIB%" :die 2 "there is no image definition for '%LANGUAGE%'"
exit /b 2

:docker_unreachable
call "%CCM_LIB%" :die 3 "Docker is installed but its daemon isn't reachable; start Docker and try again."
exit /b 3

:build_failed
call "%CCM_LIB%" :die 1 "docker build failed for %TAG%"
exit /b 1

:usage
echo Usage: build-image [--dry-run] [--help] LANGUAGE
echo.
echo Builds the toolchain image for LANGUAGE from images/LANGUAGE/Dockerfile and tags it
echo codecoremorph-LANGUAGE:local. CodeCoreMorph uses that local build until a published
echo image is pinned in images/LANGUAGE/image.lock. The build downloads the pinned tools
echo inside Docker, with the same script and manifest as native installs.
echo.
echo Options:
echo   --dry-run  print the docker command; build nothing
echo   --help     show this help
echo.
echo Exit codes: 0 success, 1 build failure, 2 usage error, 3 Docker is missing or stopped
exit /b 0
