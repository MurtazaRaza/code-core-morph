@echo off
rem Contributor setup for CodeCoreMorph (SPEC 7.15).
rem POSIX twin: scripts/bootstrap-dev.sh (same flags, messages and exit codes).
setlocal

set "CCM_LIB=%~dp0lib\common.bat"
for %%I in ("%~dp0..") do set "CCM_REPO=%%~fI"
set "UV_MIN_VERSION=0.12.0"
rem dashboard-ui's test tools (jsdom) need it.
set "NODE_MIN_VERSION=24.15.0"
set "GIT_MIN_VERSION=2.30.0"
set "PYTHON_VERSION=3.12"

call "%CCM_LIB%" :parse_args %*
if errorlevel 1 exit /b %errorlevel%
if "%CCM_HELP%"=="1" goto usage
if defined CCM_POSITIONAL goto unexpected_argument
call "%CCM_LIB%" :setup
if errorlevel 1 exit /b %errorlevel%
rem uv installs Python and the dependencies itself: offline, from its cache only.
if defined CCM_OFFLINE set "UV_OFFLINE=1"
if defined CCM_OFFLINE call "%CCM_LIB%" :log "offline: uv uses only its cache (UV_OFFLINE=1)"

rem 1. git is a system prerequisite: detect it, never install it.
call "%CCM_LIB%" :require_cmd git "Install Git for Windows (https://git-scm.com/download/win), then open a new terminal."
if errorlevel 1 exit /b %errorlevel%
set "GIT_VERSION=0"
for /f "tokens=3" %%v in ('git --version') do set "GIT_VERSION=%%v"
call "%CCM_LIB%" :version_ge "%GIT_VERSION%" "%GIT_MIN_VERSION%"
if errorlevel 1 goto git_too_old
call "%CCM_LIB%" :log "git %GIT_VERSION% found"

rem 2. uv: use the one on PATH if it's recent enough, else the pinned download.
set "UV="
where uv >nul 2>&1
if errorlevel 1 goto uv_download
set "UV_VERSION=0"
for /f "tokens=2" %%v in ('uv --version') do set "UV_VERSION=%%v"
call "%CCM_LIB%" :version_ge "%UV_VERSION%" "%UV_MIN_VERSION%"
if errorlevel 1 goto uv_too_old
set "UV=uv"
call "%CCM_LIB%" :log "using uv %UV_VERSION% from PATH"
goto uv_ready
:uv_too_old
call "%CCM_LIB%" :log "uv %UV_VERSION% on PATH is older than %UV_MIN_VERSION%; using the pinned version"
:uv_download
call "%CCM_LIB%" :install_tool dev uv
if errorlevel 1 exit /b %errorlevel%
set "UV=%CCM_TOOL_BIN_DIR%\uv.exe"
:uv_ready

rem 3-6. Python, dependencies, hooks, smoke test. All run from the repository root.
pushd "%CCM_REPO%"
call :run_step "uv python install %PYTHON_VERSION%" python install %PYTHON_VERSION%
if errorlevel 1 goto step_failed
call :run_step "uv sync" sync
if errorlevel 1 goto step_failed
if "%CCM_DRY_RUN%"=="1" goto hooks
git rev-parse --git-dir >nul 2>&1
if not errorlevel 1 goto hooks
call "%CCM_LIB%" :warn "not a git checkout; skipping the pre-commit hooks"
goto smoke_test
:hooks
call :run_step "uv run pre-commit install" run pre-commit install
if errorlevel 1 goto step_failed
:smoke_test
call :run_step "uv run codecoremorph --version" run codecoremorph --version
if errorlevel 1 goto step_failed
popd

rem 7. Node.js builds the dashboard's web UI (dashboard-ui\): the one on PATH if it's recent
rem    enough, else the pinned download.
set "NODE="
where node >nul 2>&1
if errorlevel 1 goto node_download
set "NODE_VERSION=v0"
for /f "delims=" %%v in ('node --version') do set "NODE_VERSION=%%v"
set "NODE_VERSION=%NODE_VERSION:v=%"
call "%CCM_LIB%" :version_ge "%NODE_VERSION%" "%NODE_MIN_VERSION%"
if errorlevel 1 goto node_too_old
set "NODE=node"
call "%CCM_LIB%" :log "using Node.js %NODE_VERSION% from PATH"
goto node_ready
:node_too_old
call "%CCM_LIB%" :log "Node.js %NODE_VERSION% on PATH is older than %NODE_MIN_VERSION%; using the pinned version"
:node_download
call "%CCM_LIB%" :install_tool dev node
if errorlevel 1 exit /b %errorlevel%
set "NODE=%CCM_TOOL_BIN_DIR%\node.exe"
:node_ready

rem 8. Docker is optional: without it, CodeCoreMorph runs toolchains natively.
where docker >nul 2>&1
if errorlevel 1 goto docker_missing
docker info >nul 2>&1
if errorlevel 1 goto docker_unreachable
call "%CCM_LIB%" :log "Docker is reachable"
goto done
:docker_missing
call "%CCM_LIB%" :warn "Docker was not found; CodeCoreMorph will use native mode. Install Docker to run toolchains in the sandbox."
goto done
:docker_unreachable
call "%CCM_LIB%" :warn "Docker is installed but its daemon isn't reachable; start it to use the sandbox."

:done
if "%UV%"=="uv" call "%CCM_LIB%" :log "done. Try: uv run codecoremorph --help"
if not "%UV%"=="uv" call "%CCM_LIB%" :log "done. uv was installed at %UV% and not added to PATH. Add its directory to PATH so the pre-commit hooks can run it. Then try: uv run codecoremorph --help"
if "%NODE%"=="node" exit /b 0
if "%CCM_DRY_RUN%"=="1" exit /b 0
call "%CCM_LIB%" :log "Node.js was installed at %NODE% and not added to PATH. Add its directory to PATH to build the dashboard: cd dashboard-ui; npm ci; npm run build"
exit /b 0


rem :run_step DESCRIPTION UV-ARGS...  -- run "uv UV-ARGS", or describe it in a dry run.
:run_step
if not "%CCM_DRY_RUN%"=="1" goto run_step_real
call "%CCM_LIB%" :log "would run: %~1"
exit /b 0
:run_step_real
set "STEP=%~1"
call "%CCM_LIB%" :log "running: %~1"
"%UV%" %2 %3 %4 %5 %6 %7 %8 %9
exit /b %errorlevel%

:step_failed
set "STEP_EXIT=%errorlevel%"
popd
call "%CCM_LIB%" :die 1 "'%STEP%' failed with exit code %STEP_EXIT%"
exit /b 1

:git_too_old
call "%CCM_LIB%" :die 3 "git %GIT_VERSION% is too old; CodeCoreMorph needs %GIT_MIN_VERSION% or later. Install a newer Git for Windows."
exit /b 3

:unexpected_argument
call "%CCM_LIB%" :die 2 "unexpected argument:%CCM_POSITIONAL%"
exit /b 2

:usage
echo Usage: bootstrap-dev [--prefix DIR] [--offline DIR] [--force] [--dry-run] [--help]
echo.
echo Sets up a CodeCoreMorph development environment: uv (from PATH, or a pinned,
echo checksum-verified download), Python 3.12, the project's dependencies, the
echo pre-commit hooks and Node.js for the dashboard's web UI (from PATH, or a pinned
echo download). Then it checks git and Docker. No admin rights needed.
echo.
echo Options:
echo   --prefix DIR   where downloaded tools go (default: CODECOREMORPH_HOME)
echo   --offline DIR  take uv and Node.js from the archives in DIR, and let uv use
echo                  only its cache (UV_OFFLINE=1); see install-toolchain --help
echo   --force        reinstall downloaded tools
echo   --dry-run      print what would be downloaded and run; change nothing
echo   --help         show this help
echo.
echo Exit codes: 0 success, 1 download or checksum failure, 2 usage error,
echo             3 missing system prerequisite
exit /b 0
