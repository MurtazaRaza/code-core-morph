@echo off
rem Pull the Ollama models a migration manifest's roles use (SPEC 7.15). `codecoremorph doctor`
rem prints this command with the configured tags. POSIX twin: scripts/pull-models.sh (same
rem flags, messages and exit codes).
setlocal

set "CCM_LIB=%~dp0lib\common.bat"

call "%CCM_LIB%" :parse_args %*
if errorlevel 1 exit /b %errorlevel%
if "%CCM_HELP%"=="1" goto usage
if not defined CCM_POSITIONAL goto no_tags
if "%CCM_DRY_RUN%"=="1" goto pull_all
call "%CCM_LIB%" :require_cmd ollama "Install Ollama from https://ollama.com/download, then open a new terminal."
if errorlevel 1 exit /b %errorlevel%
ollama list >nul 2>&1
if errorlevel 1 goto ollama_unreachable

:pull_all
set "PULL_FAILED="
for %%t in (%CCM_POSITIONAL%) do call :pull_one %%t
if defined PULL_FAILED exit /b %PULL_FAILED%
call "%CCM_LIB%" :log "done"
exit /b 0


rem :pull_one TAG  -- sets PULL_FAILED on failure; later calls do nothing, like the .sh twin,
rem which stops at the first failure.
:pull_one
if defined PULL_FAILED exit /b 0
if not "%CCM_DRY_RUN%"=="1" goto pull_one_real
call "%CCM_LIB%" :log "would pull %~1"
exit /b 0
:pull_one_real
call "%CCM_LIB%" :log "pulling %~1"
ollama pull "%~1"
if not errorlevel 1 exit /b 0
call "%CCM_LIB%" :die 1 "could not pull %~1"
set "PULL_FAILED=1"
exit /b 1

:no_tags
call "%CCM_LIB%" :die 2 "name at least one model tag, for example: pull-models qwen3:4b"
exit /b 2

:ollama_unreachable
call "%CCM_LIB%" :die 3 "Ollama is installed but its server isn't reachable; start Ollama (the app, or 'ollama serve') and try again."
exit /b 3

:usage
echo Usage: pull-models [--dry-run] [--help] TAG...
echo.
echo Checks that Ollama is installed and running, then runs `ollama pull` for each
echo model TAG (for example qwen3:4b). Ollama verifies each download itself and keeps
echo models in its own directory (set OLLAMA_MODELS before starting Ollama to move it).
echo.
echo Options:
echo   --dry-run  print what would be pulled; pull nothing
echo   --help     show this help
echo.
echo Exit codes: 0 success, 1 pull failure, 2 usage error, 3 Ollama is missing or stopped
exit /b 0
