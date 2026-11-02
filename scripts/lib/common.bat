@echo off
rem Shared helpers for CodeCoreMorph's dependency scripts (SPEC 7.15).
rem
rem Call a function with:   call "%~dp0lib\common.bat" :function [args...]
rem Functions return results in CCM_* variables and report failure through ERRORLEVEL;
rem callers check it with "if errorlevel 1 exit /b %errorlevel%" on the next line.
rem
rem scripts/lib/common.sh implements the same functions, flags, messages and exit codes for
rem POSIX sh. Change both files together.
rem
rem Only tools built into Windows 10 (1803+) and 11 are used: curl.exe, tar.exe, certutil.
rem No PowerShell. Paths must not contain the characters %% ! ^ or &.
rem
rem Exit codes: 0 success, 1 download or checksum failure, 2 usage error,
rem             3 missing system prerequisite (the message says how to install it).

set "CCM_LIB_DIR=%~dp0"
for %%I in ("%~dp0..") do set "CCM_SCRIPTS_DIR=%%~fI"
call %*
exit /b %errorlevel%


:log
echo(codecoremorph: %~1
exit /b 0


:warn
>&2 echo(codecoremorph: warning: %~1
exit /b 0


rem :die CODE MESSAGE  -- print an error; the caller then exits with CODE.
:die
>&2 echo(codecoremorph: error: %~2
exit /b %~1


rem :require_cmd NAME HINT  -- exit 3 with HINT unless NAME is on PATH.
:require_cmd
where "%~1" >nul 2>&1
if not errorlevel 1 exit /b 0
call :die 3 "required tool '%~1' was not found. %~2"
exit /b 3


rem :require_system_tool PATH NAME  -- exit 3 unless a built-in Windows tool exists.
:require_system_tool
if exist "%~1" exit /b 0
call :die 3 "%~2 was not found at %~1. These scripts need Windows 10 version 1803 or later, which include curl.exe, tar.exe and certutil.exe."
exit /b 3


rem :parse_args ARGS...  -- parse the common flags.
rem Sets CCM_PREFIX, CCM_FORCE, CCM_DRY_RUN, CCM_HELP and CCM_POSITIONAL (space-separated).
:parse_args
set "CCM_PREFIX="
set "CCM_FORCE=0"
set "CCM_DRY_RUN=0"
set "CCM_HELP=0"
set "CCM_POSITIONAL="
:parse_args_next
if "%~1"=="" exit /b 0
set "_ccm_arg=%~1"
if /i "%_ccm_arg%"=="--prefix" goto parse_args_prefix
if /i "%_ccm_arg%"=="--force" set "CCM_FORCE=1" & goto parse_args_shift
if /i "%_ccm_arg%"=="--dry-run" set "CCM_DRY_RUN=1" & goto parse_args_shift
if /i "%_ccm_arg%"=="--help" set "CCM_HELP=1" & goto parse_args_shift
if /i "%_ccm_arg%"=="-h" set "CCM_HELP=1" & goto parse_args_shift
if "%_ccm_arg:~0,1%"=="-" goto parse_args_unknown
set "CCM_POSITIONAL=%CCM_POSITIONAL% %_ccm_arg%"
:parse_args_shift
shift
goto parse_args_next
:parse_args_prefix
rem cmd.exe splits "--prefix=DIR" at "=", so it arrives here as "--prefix" "DIR" too.
if "%~2"=="" goto parse_args_prefix_missing
set "CCM_PREFIX=%~2"
shift
shift
goto parse_args_next
:parse_args_prefix_missing
call :die 2 "--prefix needs a directory"
exit /b 2
:parse_args_unknown
call :die 2 "unknown option '%_ccm_arg%' (see --help)"
exit /b 2


rem :detect_platform  -- sets CCM_PLATFORM (windows-x64 or windows-arm64).
:detect_platform
set "_ccm_arch=%PROCESSOR_ARCHITECTURE%"
if defined PROCESSOR_ARCHITEW6432 set "_ccm_arch=%PROCESSOR_ARCHITEW6432%"
if /i "%_ccm_arch%"=="AMD64" set "CCM_PLATFORM=windows-x64" & exit /b 0
if /i "%_ccm_arch%"=="ARM64" set "CCM_PLATFORM=windows-arm64" & exit /b 0
call :die 3 "unsupported processor architecture '%_ccm_arch%'"
exit /b 3


rem :resolve_home  -- sets CCM_HOME: --prefix, else CODECOREMORPH_HOME, else
rem %LOCALAPPDATA%\codecoremorph. Mirrors codecoremorph.core.paths: relative values resolve
rem against the current directory.
:resolve_home
set "_ccm_home="
if defined CCM_PREFIX set "_ccm_home=%CCM_PREFIX%"
if not defined _ccm_home if defined CODECOREMORPH_HOME set "_ccm_home=%CODECOREMORPH_HOME%"
if not defined _ccm_home if defined LOCALAPPDATA set "_ccm_home=%LOCALAPPDATA%\codecoremorph"
if not defined _ccm_home set "_ccm_home=%USERPROFILE%\AppData\Local\codecoremorph"
for %%I in ("%_ccm_home%") do set "CCM_HOME=%%~fI"
if "%CCM_HOME:~-1%"=="\" set "CCM_HOME=%CCM_HOME:~0,-1%"
exit /b 0


rem :setup  -- sets CCM_PLATFORM, CCM_HOME, CCM_MANIFEST and the paths of the built-in tools.
rem Call after :parse_args.
:setup
call :detect_platform
if errorlevel 1 exit /b %errorlevel%
call :resolve_home
set "CCM_CURL=%SystemRoot%\System32\curl.exe"
set "CCM_TAR=%SystemRoot%\System32\tar.exe"
set "CCM_CERTUTIL=%SystemRoot%\System32\certutil.exe"
set "CCM_MANIFEST=%CCM_SCRIPTS_DIR%\deps\manifest.txt"
if defined CODECOREMORPH_MANIFEST set "CCM_MANIFEST=%CODECOREMORPH_MANIFEST%"
if exist "%CCM_MANIFEST%" exit /b 0
call :die 2 "dependency manifest not found: %CCM_MANIFEST%"
exit /b 2


rem :manifest_row GROUP TOOL  -- find the row for this platform (or 'any'). Sets
rem CCM_ROW_VERSION, CCM_ROW_URL, CCM_ROW_SHA256, CCM_ROW_ARCHIVE and CCM_ROW_BIN_DIR.
:manifest_row
set "CCM_ROW_VERSION="
for /f "usebackq eol=# tokens=1-8 delims=|" %%a in ("%CCM_MANIFEST%") do (
    if not defined CCM_ROW_VERSION if "%%a"=="%~1" if "%%b"=="%~2" if "%%d"=="%CCM_PLATFORM%" (
        set "CCM_ROW_URL=%%e"
        set "CCM_ROW_SHA256=%%f"
        set "CCM_ROW_ARCHIVE=%%g"
        set "CCM_ROW_BIN_DIR=%%h"
        set "CCM_ROW_VERSION=%%c"
    )
    if not defined CCM_ROW_VERSION if "%%a"=="%~1" if "%%b"=="%~2" if "%%d"=="any" (
        set "CCM_ROW_URL=%%e"
        set "CCM_ROW_SHA256=%%f"
        set "CCM_ROW_ARCHIVE=%%g"
        set "CCM_ROW_BIN_DIR=%%h"
        set "CCM_ROW_VERSION=%%c"
    )
)
if defined CCM_ROW_VERSION exit /b 0
call :die 3 "the manifest has no pinned download of %~2 for %CCM_PLATFORM%; install %~2 yourself"
exit /b 3


rem :require_download_tools  -- exit 3 unless curl.exe, tar.exe and certutil.exe exist.
:require_download_tools
call :require_system_tool "%CCM_CURL%" curl.exe
if errorlevel 1 exit /b %errorlevel%
call :require_system_tool "%CCM_TAR%" tar.exe
if errorlevel 1 exit /b %errorlevel%
call :require_system_tool "%CCM_CERTUTIL%" certutil.exe
exit /b %errorlevel%


rem :sha256 FILE  -- sets CCM_SHA256 to the file's SHA-256 (empty if certutil failed).
:sha256
set "CCM_SHA256="
for /f "skip=1 delims=" %%h in ('""%CCM_CERTUTIL%" -hashfile "%~1" SHA256"') do if not defined CCM_SHA256 set "CCM_SHA256=%%h"
if not defined CCM_SHA256 exit /b 1
rem Older certutil versions print the hash with spaces between byte pairs.
set "CCM_SHA256=%CCM_SHA256: =%"
exit /b 0


rem :download URL DEST  -- download over HTTPS (or copy a file:// URL), retrying failures.
:download
set "_ccm_url=%~1"
if /i "%_ccm_url:~0,8%"=="https://" goto download_start
if /i "%_ccm_url:~0,7%"=="file://" goto download_start
call :die 1 "refusing to download from a non-HTTPS URL: %~1"
exit /b 1
:download_start
set "_ccm_try=1"
:download_try
"%CCM_CURL%" -fsSL --proto "=https,file" --connect-timeout 30 -o "%~2" "%~1"
if not errorlevel 1 exit /b 0
if exist "%~2" del /f /q "%~2"
if %_ccm_try% GEQ 4 exit /b 1
call :warn "download failed (attempt %_ccm_try% of 4); retrying"
set /a _ccm_try+=1
rem Sleep 2 seconds. timeout.exe refuses to run without a console, so ping instead.
ping -n 3 127.0.0.1 >nul
goto download_try


rem :extract ARCHIVE TYPE DEST_DIR URL [PATTERN...]  -- extract everything, or only the
rem members matching the shell-style PATTERNs.
:extract
if /i "%~2"=="file" goto extract_file
set "_ccm_x_archive=%~1"
set "_ccm_x_dest=%~3"
set "_ccm_x_patterns="
:extract_patterns
if "%~5"=="" goto extract_run
set _ccm_x_patterns=%_ccm_x_patterns% "%~5"
shift /5
goto extract_patterns
:extract_run
rem The built-in tar.exe (bsdtar) reads zip and tar.gz archives.
"%CCM_TAR%" -xf "%_ccm_x_archive%" -C "%_ccm_x_dest%" %_ccm_x_patterns%
exit /b %errorlevel%
:extract_file
set "_ccm_name=%~4"
for %%F in ("%_ccm_name:/=\%") do set "_ccm_name=%%~nxF"
copy /y "%~1" "%~3\%_ccm_name%" >nul
exit /b %errorlevel%


rem :install_tool GROUP TOOL [PATTERN...]  -- install a pinned tool from the manifest, unless
rem the same version and hash is already installed. PATTERNs extract only the matching archive
rem members (shell-style, e.g. */bin/clang); the marker records them, so a different selection
rem reinstalls. Sets CCM_TOOL_DIR and CCM_TOOL_BIN_DIR.
:install_tool
set "_ccm_members=members:"
set "_ccm_member_args="
:install_tool_members
if "%~3"=="" goto install_tool_lookup
set "_ccm_members=%_ccm_members% %~3"
set _ccm_member_args=%_ccm_member_args% "%~3"
shift /3
goto install_tool_members
:install_tool_lookup
call :manifest_row "%~1" "%~2"
if errorlevel 1 exit /b %errorlevel%
set "_ccm_root=%CCM_HOME%\toolchains\%~2"
if "%~1"=="dev" set "_ccm_root=%CCM_HOME%\tools\%~2"
set "CCM_TOOL_DIR=%_ccm_root%\%CCM_ROW_VERSION%"
set "CCM_TOOL_BIN_DIR=%CCM_TOOL_DIR%\%CCM_ROW_BIN_DIR:/=\%"
if "%CCM_ROW_BIN_DIR%"=="." set "CCM_TOOL_BIN_DIR=%CCM_TOOL_DIR%"

if "%CCM_FORCE%"=="1" goto install_tool_fetch
if not exist "%CCM_TOOL_DIR%\.installed" goto install_tool_fetch
rem for /f reads both LF (written by the .sh twin) and CRLF lines; set /p reads only CRLF.
set "_ccm_marker="
for /f "usebackq delims=" %%m in ("%CCM_TOOL_DIR%\.installed") do if not defined _ccm_marker set "_ccm_marker=%%m"
if /i not "%_ccm_marker%"=="%CCM_ROW_SHA256%" goto install_tool_fetch
rem Markers written before member selection existed have one line.
set "_ccm_marker_members=members:"
for /f "usebackq skip=1 delims=" %%m in ("%CCM_TOOL_DIR%\.installed") do set "_ccm_marker_members=%%m"
if not "%_ccm_marker_members%"=="%_ccm_members%" goto install_tool_fetch
call :log "%~2 %CCM_ROW_VERSION% is already installed in %CCM_TOOL_DIR%"
exit /b 0

:install_tool_fetch
set "_ccm_ext=%CCM_ROW_ARCHIVE%"
if "%_ccm_ext%"=="file" set "_ccm_ext=bin"
set "_ccm_archive=%CCM_HOME%\downloads\%CCM_ROW_SHA256%.%_ccm_ext%"
if not "%CCM_DRY_RUN%"=="1" goto install_tool_real
call :log "would download %CCM_ROW_URL%"
call :log "would install %~2 %CCM_ROW_VERSION% into %CCM_TOOL_DIR%"
exit /b 0

:install_tool_real
call :require_download_tools
if errorlevel 1 exit /b %errorlevel%
if not exist "%CCM_HOME%\downloads" mkdir "%CCM_HOME%\downloads"
if not exist "%_ccm_root%" mkdir "%_ccm_root%"
if not exist "%_ccm_root%" goto install_tool_mkdir_failed

if not exist "%_ccm_archive%" goto install_tool_download
call :sha256 "%_ccm_archive%"
if /i not "%CCM_SHA256%"=="%CCM_ROW_SHA256%" goto install_tool_stale_cache
call :log "using cached %_ccm_archive%"
goto install_tool_extract
:install_tool_stale_cache
del /f /q "%_ccm_archive%"

:install_tool_download
set "_ccm_part=%_ccm_archive%.part"
call :log "downloading %~2 %CCM_ROW_VERSION% from %CCM_ROW_URL%"
call :download "%CCM_ROW_URL%" "%_ccm_part%"
if errorlevel 1 goto install_tool_download_failed
call :sha256 "%_ccm_part%"
if /i not "%CCM_SHA256%"=="%CCM_ROW_SHA256%" goto install_tool_checksum_mismatch
move /y "%_ccm_part%" "%_ccm_archive%" >nul

:install_tool_extract
rem Extract next to the destination, then rename: an interrupted install never leaves a
rem half-installed tool behind.
set "_ccm_tmp=%_ccm_root%\.tmp-%CCM_ROW_VERSION%-%RANDOM%"
if exist "%_ccm_tmp%" rmdir /s /q "%_ccm_tmp%"
mkdir "%_ccm_tmp%"
call :extract "%_ccm_archive%" "%CCM_ROW_ARCHIVE%" "%_ccm_tmp%" "%CCM_ROW_URL%" %_ccm_member_args%
if errorlevel 1 goto install_tool_extract_failed
>"%_ccm_tmp%\.installed" echo(%CCM_ROW_SHA256%
>>"%_ccm_tmp%\.installed" echo(%_ccm_members%
if exist "%CCM_TOOL_DIR%" rmdir /s /q "%CCM_TOOL_DIR%"
move "%_ccm_tmp%" "%CCM_TOOL_DIR%" >nul
if errorlevel 1 goto install_tool_move_failed
call :log "installed %~2 %CCM_ROW_VERSION% into %CCM_TOOL_DIR%"
exit /b 0

:install_tool_mkdir_failed
call :die 1 "could not create directories under %CCM_HOME%"
exit /b 1
:install_tool_download_failed
call :die 1 "download failed: %CCM_ROW_URL%"
exit /b 1
:install_tool_checksum_mismatch
if exist "%_ccm_part%" del /f /q "%_ccm_part%"
call :die 1 "checksum mismatch for %CCM_ROW_URL%: expected %CCM_ROW_SHA256%, got %CCM_SHA256%. The file was deleted."
exit /b 1
:install_tool_extract_failed
if exist "%_ccm_tmp%" rmdir /s /q "%_ccm_tmp%"
call :die 1 "could not extract %_ccm_archive%"
exit /b 1
:install_tool_move_failed
if exist "%_ccm_tmp%" rmdir /s /q "%_ccm_tmp%"
call :die 1 "could not move the install into %CCM_TOOL_DIR%"
exit /b 1


rem :version_ge VERSION MINIMUM  -- ERRORLEVEL 0 if VERSION >= MINIMUM, else 1.
:version_ge
call :_version_number "%~1"
set "_ccm_have=%_ccm_num%"
call :_version_number "%~2"
if %_ccm_have% GEQ %_ccm_num% exit /b 0
exit /b 1

:_version_number
set "_ccm_a=0"
set "_ccm_b=0"
set "_ccm_c=0"
for /f "tokens=1-3 delims=.-+ " %%a in ("%~1") do (
    if not "%%a"=="" set "_ccm_a=%%a"
    if not "%%b"=="" set "_ccm_b=%%b"
    if not "%%c"=="" set "_ccm_c=%%c"
)
set /a "_ccm_num=_ccm_a*1000000+_ccm_b*1000+_ccm_c" 2>nul
exit /b 0
