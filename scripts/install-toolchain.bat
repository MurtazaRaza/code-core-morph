@echo off
rem Native toolchain installer for CodeCoreMorph (SPEC 7.15).
rem POSIX twin: scripts/install-toolchain.sh (same flags, messages and exit codes).
setlocal

set "CCM_LIB=%~dp0lib\common.bat"

call "%CCM_LIB%" :parse_args %*
if errorlevel 1 exit /b %errorlevel%
if "%CCM_HELP%"=="1" goto usage
set "LANGUAGE="
set "LANGUAGE_COUNT=0"
for %%a in (%CCM_POSITIONAL%) do call :count_language %%a
if not "%LANGUAGE_COUNT%"=="1" goto expected_one_language
if "%LANGUAGE%"=="cpp" goto language_ok
if "%LANGUAGE%"=="all" goto language_ok
if "%LANGUAGE%"=="java" goto language_later
if "%LANGUAGE%"=="csharp" goto language_later
if "%LANGUAGE%"=="rust" goto language_later
call "%CCM_LIB%" :die 2 "unknown language '%LANGUAGE%'; available: cpp, all"
exit /b 2

:language_ok
call "%CCM_LIB%" :setup
if errorlevel 1 exit /b %errorlevel%
call :install_cpp
if errorlevel 1 exit /b %errorlevel%
call "%CCM_LIB%" :log "done"
exit /b 0


:count_language
set /a LANGUAGE_COUNT+=1
set "LANGUAGE=%~1"
exit /b 0


rem :install_cpp  -- Windows needs no system prerequisites beyond the built-in download tools.
:install_cpp
call "%CCM_LIB%" :install_tool cpp llvm-mingw
if errorlevel 1 exit /b %errorlevel%
set "LLVM_ID=llvm-mingw %CCM_ROW_VERSION%"
set "LLVM_BIN=%CCM_TOOL_BIN_DIR%"
rem Runtime DLLs (libc++, libunwind) that test executables need.
set "EXTRA_BIN=%CCM_TOOL_BIN_DIR:~0,-4%\x86_64-w64-mingw32\bin"
set "CC_PATH=%LLVM_BIN%\clang.exe"
set "CXX_PATH=%LLVM_BIN%\clang++.exe"
call "%CCM_LIB%" :install_tool cpp cmake
if errorlevel 1 exit /b %errorlevel%
set "CMAKE_VERSION=%CCM_ROW_VERSION%"
set "CMAKE_BIN=%CCM_TOOL_BIN_DIR%"
set "CMAKE=%CCM_TOOL_BIN_DIR%\cmake.exe"
call "%CCM_LIB%" :install_tool cpp ninja
if errorlevel 1 exit /b %errorlevel%
set "NINJA_VERSION=%CCM_ROW_VERSION%"
set "NINJA_BIN=%CCM_TOOL_BIN_DIR%"
set "NINJA=%CCM_TOOL_BIN_DIR%\ninja.exe"
call "%CCM_LIB%" :install_tool cpp googletest
if errorlevel 1 exit /b %errorlevel%
set "GTEST_VERSION=%CCM_ROW_VERSION%"
set "GT_EXTRA="
call :build_googletest "%CCM_TOOL_BIN_DIR%"
if errorlevel 1 exit /b %errorlevel%
set "GT_MODERN=%GTEST_PREFIX%"
call "%CCM_LIB%" :install_tool cpp googletest-cxx98
if errorlevel 1 exit /b %errorlevel%
set "GTEST98_VERSION=%CCM_ROW_VERSION%"
rem 1.8.1 predates the CMake 3.5 policies that CMake 4 requires.
set "GT_EXTRA=-DCMAKE_POLICY_VERSION_MINIMUM=3.5 -DCMAKE_CXX_STANDARD=98 -DCMAKE_CXX_STANDARD_REQUIRED=ON"
call :build_googletest "%CCM_TOOL_BIN_DIR%"
if errorlevel 1 exit /b %errorlevel%
set "GTEST98_PREFIX=%GTEST_PREFIX%"
set "GTEST_PREFIX=%GT_MODERN%"
call :write_env
exit /b %errorlevel%


rem :build_googletest SOURCE_DIR  -- build and install GoogleTest with the downloaded tools,
rem unless the same source was already built with the same compiler. Extra CMake arguments
rem come from GT_EXTRA. Sets GTEST_PREFIX.
:build_googletest
set "GT_SRC=%~1"
set "GT_ROOT=%CCM_HOME%\toolchains\googletest\%CCM_ROW_VERSION%"
set "GTEST_PREFIX=%GT_ROOT%\install"
set "GT_STAMP=%CCM_ROW_SHA256% %LLVM_ID%"
if "%CCM_FORCE%"=="1" goto build_googletest_needed
if not exist "%GTEST_PREFIX%\.built" goto build_googletest_needed
set "GT_HAVE="
for /f "usebackq delims=" %%m in ("%GTEST_PREFIX%\.built") do if not defined GT_HAVE set "GT_HAVE=%%m"
if not "%GT_HAVE%"=="%GT_STAMP%" goto build_googletest_needed
call "%CCM_LIB%" :log "GoogleTest %CCM_ROW_VERSION% is already built in %GTEST_PREFIX%"
exit /b 0
:build_googletest_needed
if not "%CCM_DRY_RUN%"=="1" goto build_googletest_real
call "%CCM_LIB%" :log "would build GoogleTest %CCM_ROW_VERSION% with %LLVM_ID% into %GTEST_PREFIX%"
exit /b 0
:build_googletest_real
set "GT_BUILD=%GT_ROOT%\.build-%RANDOM%"
set "GT_TMP=%GT_ROOT%\.install-%RANDOM%"
set "GT_LOG=%GT_ROOT%\build.log"
if exist "%GT_BUILD%" rmdir /s /q "%GT_BUILD%"
if exist "%GT_TMP%" rmdir /s /q "%GT_TMP%"
if not exist "%GT_ROOT%" mkdir "%GT_ROOT%" 2>nul
type nul >"%GT_LOG%" 2>nul
if errorlevel 1 goto build_googletest_no_log
call "%CCM_LIB%" :log "building GoogleTest %CCM_ROW_VERSION% with %LLVM_ID% (log: %GT_LOG%)"
"%CMAKE%" -S "%GT_SRC:\=/%" -B "%GT_BUILD:\=/%" -G Ninja "-DCMAKE_MAKE_PROGRAM=%NINJA:\=/%" "-DCMAKE_C_COMPILER=%CC_PATH:\=/%" "-DCMAKE_CXX_COMPILER=%CXX_PATH:\=/%" -DCMAKE_BUILD_TYPE=Release "-DCMAKE_INSTALL_PREFIX=%GT_TMP:\=/%" -DBUILD_GMOCK=ON -DINSTALL_GTEST=ON %GT_EXTRA% >"%GT_LOG%" 2>&1
if errorlevel 1 goto build_googletest_failed
"%CMAKE%" --build "%GT_BUILD:\=/%" >>"%GT_LOG%" 2>&1
if errorlevel 1 goto build_googletest_failed
"%CMAKE%" --install "%GT_BUILD:\=/%" >>"%GT_LOG%" 2>&1
if errorlevel 1 goto build_googletest_failed
>"%GT_TMP%\.built" echo(%GT_STAMP%
if exist "%GTEST_PREFIX%" rmdir /s /q "%GTEST_PREFIX%"
rmdir /s /q "%GT_BUILD%"
move "%GT_TMP%" "%GTEST_PREFIX%" >nul
if errorlevel 1 goto build_googletest_move_failed
call "%CCM_LIB%" :log "installed GoogleTest %CCM_ROW_VERSION% into %GTEST_PREFIX%"
exit /b 0
:build_googletest_failed
if exist "%GT_BUILD%" rmdir /s /q "%GT_BUILD%"
if exist "%GT_TMP%" rmdir /s /q "%GT_TMP%"
set "GT_LINES=0"
for /f %%n in ('find /c /v "" ^<"%GT_LOG%"') do set "GT_LINES=%%n"
set /a GT_SKIP=GT_LINES-20
if %GT_SKIP% LSS 0 set "GT_SKIP=0"
more +%GT_SKIP% "%GT_LOG%" 1>&2
call "%CCM_LIB%" :die 1 "building GoogleTest failed; the full log is %GT_LOG%"
exit /b 1
:build_googletest_no_log
call "%CCM_LIB%" :die 1 "could not create %GT_LOG%"
exit /b 1
:build_googletest_move_failed
call "%CCM_LIB%" :die 1 "could not move the GoogleTest build into %GTEST_PREFIX%"
exit /b 1


:write_env
set "ENV_FILE=%CCM_HOME%\toolchains\cpp.env"
set "ACTIVATE=%CCM_HOME%\toolchains\activate-cpp.bat"
if not "%CCM_DRY_RUN%"=="1" goto write_env_real
call "%CCM_LIB%" :log "would write %ENV_FILE% and %ACTIVATE%"
exit /b 0
:write_env_real
set "PREPEND=%LLVM_BIN%;%CMAKE_BIN%;%NINJA_BIN%;%EXTRA_BIN%"
>"%ENV_FILE%.tmp" echo(# CodeCoreMorph C++ toolchain, written by install-toolchain. Do not edit.
if errorlevel 1 goto write_env_failed
>>"%ENV_FILE%.tmp" echo(# %LLVM_ID%, cmake %CMAKE_VERSION%, ninja %NINJA_VERSION%, googletest %GTEST_VERSION% and %GTEST98_VERSION% (C++98/03)
>>"%ENV_FILE%.tmp" echo(PATH_PREPEND=%PREPEND%
>>"%ENV_FILE%.tmp" echo(CC=%CC_PATH%
>>"%ENV_FILE%.tmp" echo(CXX=%CXX_PATH%
>>"%ENV_FILE%.tmp" echo(CMAKE_GENERATOR=Ninja
>>"%ENV_FILE%.tmp" echo(CMAKE_PREFIX_PATH=%GTEST_PREFIX%
rem The C++ adapter uses this prefix instead when a project builds as C++98/03.
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_CXX98_PREFIX_PATH=%GTEST98_PREFIX%
move /y "%ENV_FILE%.tmp" "%ENV_FILE%" >nul
if errorlevel 1 goto write_env_failed
>"%ACTIVATE%.tmp" echo(@echo off
if errorlevel 1 goto write_activate_failed
>>"%ACTIVATE%.tmp" echo(rem Use the CodeCoreMorph C++ toolchain in this cmd.exe window:  call "%ACTIVATE%"
>>"%ACTIVATE%.tmp" echo(set "PATH=%PREPEND%;%%PATH%%"
>>"%ACTIVATE%.tmp" echo(set "CC=%CC_PATH%"
>>"%ACTIVATE%.tmp" echo(set "CXX=%CXX_PATH%"
>>"%ACTIVATE%.tmp" echo(set "CMAKE_GENERATOR=Ninja"
>>"%ACTIVATE%.tmp" echo(set "CMAKE_PREFIX_PATH=%GTEST_PREFIX%"
move /y "%ACTIVATE%.tmp" "%ACTIVATE%" >nul
if errorlevel 1 goto write_activate_failed
rem cmd.exe can't write the LF-only activate-cpp.sh; remove an older one rather than leave it
rem pointing at a version that may be gone. install-toolchain.sh writes both scripts.
set "ACTIVATE_SH=%CCM_HOME%\toolchains\activate-cpp.sh"
if not exist "%ACTIVATE_SH%" goto write_env_done
del /f /q "%ACTIVATE_SH%"
call "%CCM_LIB%" :log "removed %ACTIVATE_SH%, which may be out of date; run install-toolchain.sh cpp to write it again"
:write_env_done
call "%CCM_LIB%" :log "wrote %ENV_FILE%"
echo(codecoremorph: to use the toolchain in your own shell, run: call "%ACTIVATE%"
exit /b 0
:write_env_failed
call "%CCM_LIB%" :die 1 "could not write %ENV_FILE%"
exit /b 1
:write_activate_failed
call "%CCM_LIB%" :die 1 "could not write %ACTIVATE%"
exit /b 1


:language_later
call "%CCM_LIB%" :die 2 "the %LANGUAGE% toolchain is not available yet; available: cpp"
exit /b 2

:expected_one_language
call "%CCM_LIB%" :die 2 "expected one language: cpp or all (see --help)"
exit /b 2

:usage
echo Usage: install-toolchain [--prefix DIR] [--offline DIR] [--force] [--dry-run] [--help] LANGUAGE
echo.
echo Installs the pinned native toolchain for LANGUAGE under CODECOREMORPH_HOME, so
echo CodeCoreMorph can build and test projects without Docker. Every download is
echo checksum-verified. No admin rights are needed, and your PATH is not changed.
echo.
echo Languages:
echo   cpp   LLVM (clang, clang-tidy, clang-format; llvm-mingw on Windows), CMake,
echo         Ninja, and GoogleTest built with that compiler (1.12.1, plus 1.8.1
echo         for projects that build as C++98/03)
echo   all   every available language (currently cpp)
echo.
echo Options:
echo   --prefix DIR   install under DIR (default: CODECOREMORPH_HOME)
echo   --offline DIR  install from the archives in DIR, without the network: files
echo                  named as in the manifest's URLs, or a downloads directory
echo                  copied from a CODECOREMORPH_HOME on the same platform
echo   --force        reinstall even if the pinned version is already installed
echo   --dry-run      print what would be downloaded and built; change nothing
echo   --help         show this help
echo.
echo CodeCoreMorph finds the tools through toolchains/LANGUAGE.env. To use them in
echo your own shell, run toolchains/activate-LANGUAGE (.bat or .sh).
echo.
echo Exit codes: 0 success, 1 download, checksum or build failure, 2 usage error,
echo             3 missing system prerequisite
exit /b 0
