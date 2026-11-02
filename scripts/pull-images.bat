@echo off
rem Pre-pull the pinned CodeCoreMorph toolchain images (SPEC 7.15). Optional: runs pull images
rem on demand. POSIX twin: scripts/pull-images.sh (same flags, messages and exit codes).
setlocal

set "CCM_LIB=%~dp0lib\common.bat"
for %%I in ("%~dp0..\images") do set "CCM_IMAGES=%%~fI"
if exist "%~dp0..\_images" for %%I in ("%~dp0..\_images") do set "CCM_IMAGES=%%~fI"

call "%CCM_LIB%" :parse_args %*
if errorlevel 1 exit /b %errorlevel%
if "%CCM_HELP%"=="1" goto usage
if "%CCM_DRY_RUN%"=="1" goto languages
call "%CCM_LIB%" :require_cmd docker "Install Docker (Docker Desktop on Windows and macOS), then open a new terminal."
if errorlevel 1 exit /b %errorlevel%
docker info >nul 2>&1
if errorlevel 1 goto docker_unreachable

:languages
set "LANGUAGES=%CCM_POSITIONAL%"
if defined LANGUAGES goto pull_all
for /d %%d in ("%CCM_IMAGES%\*") do if exist "%%d\image.lock" call :add_language "%%~nxd"
:pull_all
set "PULL_FAILED="
for %%l in (%LANGUAGES%) do call :pull_one %%l
if defined PULL_FAILED exit /b %PULL_FAILED%
call "%CCM_LIB%" :log "done"
exit /b 0


:add_language
set "LANGUAGES=%LANGUAGES% %~1"
exit /b 0


rem :pull_one LANGUAGE  -- sets PULL_FAILED to the exit code on failure; later calls do nothing,
rem like the .sh twin, which stops at the first failure.
:pull_one
if defined PULL_FAILED exit /b 0
set "LOCK=%CCM_IMAGES%\%~1\image.lock"
if not exist "%LOCK%" goto no_image
set "IMAGE="
set "DIGEST="
for /f "usebackq eol=# tokens=1,* delims==" %%a in ("%LOCK%") do (
    if "%%a"=="image" set "IMAGE=%%b"
    if "%%a"=="digest" set "DIGEST=%%b"
)
if defined DIGEST goto pull_one_pinned
call "%CCM_LIB%" :warn "%~1: %IMAGE% isn't published yet; build it locally with: build-image %~1"
exit /b 0
:pull_one_pinned
if not "%CCM_DRY_RUN%"=="1" goto pull_one_real
call "%CCM_LIB%" :log "would pull %IMAGE%@%DIGEST%"
exit /b 0
:pull_one_real
call "%CCM_LIB%" :log "pulling %IMAGE%@%DIGEST%"
docker pull "%IMAGE%@%DIGEST%"
if not errorlevel 1 exit /b 0
call "%CCM_LIB%" :die 1 "could not pull %IMAGE%@%DIGEST%"
set "PULL_FAILED=1"
exit /b 1

:no_image
call "%CCM_LIB%" :die 2 "there is no image for '%~1'"
set "PULL_FAILED=2"
exit /b 2

:docker_unreachable
call "%CCM_LIB%" :die 3 "Docker is installed but its daemon isn't reachable; start Docker and try again."
exit /b 3

:usage
echo Usage: pull-images [--dry-run] [--help] [LANGUAGE...]
echo.
echo Pulls the toolchain image of each LANGUAGE (default: every language with an
echo images/LANGUAGE/image.lock), pinned by digest. An image that isn't published yet
echo is skipped with a note on how to build it locally.
echo.
echo Options:
echo   --dry-run  print what would be pulled; pull nothing
echo   --help     show this help
echo.
echo Exit codes: 0 success, 1 pull failure, 2 usage error, 3 Docker is missing or stopped
exit /b 0
