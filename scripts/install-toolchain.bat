@echo off
rem Native toolchain installer for CodeCoreMorph (SPEC 7.15).
rem POSIX twin: scripts/install-toolchain.sh (same flags, messages and exit codes).
setlocal

set "CCM_LIB=%~dp0lib\common.bat"
rem The Rust release rustup installs, and the cbindgen (D-56) and bindgen (D-68) releases cargo
rem builds. rustup and cargo verify what they download themselves (SPEC 7.15 rule 1).
set "RUST_VERSION=1.99.1"
set "CBINDGEN_VERSION=0.29.4"
set "BINDGEN_VERSION=0.73.2"
rem The Maven artifacts the Java adapter runs offline (D-63): OpenRewrite's plugin and recipes,
rem and the dependency plugin of its dependency step. Maven downloads them, checking
rem each against the repository's checksums (-C), into toolchains\java\m2, which builds chain
rem behind their own repository (maven.repo.local.tail). java.env names them for the adapter.
set "REWRITE_PLUGIN=org.openrewrite.maven:rewrite-maven-plugin:6.46.1"
set "REWRITE_RECIPES=org.openrewrite.recipe:rewrite-migrate-java:3.42.1,org.openrewrite.recipe:rewrite-static-analysis:2.41.1"
set "DEPENDENCY_PLUGIN=org.apache.maven.plugins:maven-dependency-plugin:3.11.0"
set "JAVA_STANDARDS=8 11 17 21 25"
rem The .NET runtimes installed beside the SDK's own (D-65): a test project runs on its
rem framework's runtime, and one without rolls forward to the next.
set "CSHARP_RUNTIMES=8 6"

call "%CCM_LIB%" :parse_args %*
if errorlevel 1 exit /b %errorlevel%
if "%CCM_HELP%"=="1" goto usage
set "LANGUAGE="
set "LANGUAGE_COUNT=0"
for %%a in (%CCM_POSITIONAL%) do call :count_language %%a
if not "%LANGUAGE_COUNT%"=="1" goto expected_one_language
if "%LANGUAGE%"=="cpp" goto language_ok
if "%LANGUAGE%"=="rust" goto language_ok
if "%LANGUAGE%"=="java" goto language_ok
if "%LANGUAGE%"=="csharp" goto language_ok
if "%LANGUAGE%"=="all" goto language_ok
call "%CCM_LIB%" :die 2 "unknown language '%LANGUAGE%'; available: cpp, rust, java, csharp, all"
exit /b 2

:language_ok
call "%CCM_LIB%" :setup
if errorlevel 1 exit /b %errorlevel%
if "%LANGUAGE%"=="cpp" goto language_cpp
if "%LANGUAGE%"=="java" goto language_java
if "%LANGUAGE%"=="csharp" goto language_csharp
rem rust and all: the Rust toolchain includes the C++ one.
call :install_rust
if errorlevel 1 exit /b %errorlevel%
if not "%LANGUAGE%"=="all" goto language_done
:language_java
call :install_java
if errorlevel 1 exit /b %errorlevel%
if not "%LANGUAGE%"=="all" goto language_done
:language_csharp
call :install_csharp
if errorlevel 1 exit /b %errorlevel%
goto language_done
:language_cpp
call :install_cpp
if errorlevel 1 exit /b %errorlevel%
:language_done
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


rem :install_rust  -- the C++ toolchain, then Rust through rustup, cargo-nextest, cbindgen,
rem bindgen and Corrosion, and rust.env (a superset of cpp.env: every Rust run here is a mixed
rem build).
:install_rust
if defined CCM_OFFLINE goto install_rust_offline
call :install_cpp
if errorlevel 1 exit /b %errorlevel%
set "RUSTUP_HOME_DIR=%CCM_HOME%\toolchains\rust\rustup"
set "CARGO_HOME_DIR=%CCM_HOME%\toolchains\rust\cargo"
call "%CCM_LIB%" :install_tool rust rustup
if errorlevel 1 exit /b %errorlevel%
set "RUSTUP_VERSION=%CCM_ROW_VERSION%"
set "RUST_INIT=%CCM_TOOL_DIR%\rustup-init.exe"
call :rust_toolchain
if errorlevel 1 exit /b %errorlevel%
call "%CCM_LIB%" :install_tool rust cargo-nextest
if errorlevel 1 exit /b %errorlevel%
set "NEXTEST_VERSION=%CCM_ROW_VERSION%"
set "NEXTEST_BIN=%CCM_TOOL_BIN_DIR%"
call :rust_cbindgen
if errorlevel 1 exit /b %errorlevel%
call :rust_bindgen
if errorlevel 1 exit /b %errorlevel%
call :rust_corrosion
if errorlevel 1 exit /b %errorlevel%
call :rust_write_env
exit /b %errorlevel%
:install_rust_offline
call "%CCM_LIB%" :die 2 "--offline doesn't apply to rust: rustup downloads the Rust release and cargo builds cbindgen and bindgen themselves"
exit /b 2


:rust_toolchain
set "RUSTC=%CARGO_HOME_DIR%\bin\rustc.exe"
if "%CCM_FORCE%"=="1" goto rust_toolchain_needed
if not exist "%RUSTC%" goto rust_toolchain_needed
set "RUST_HAVE="
set "RUSTUP_HOME=%RUSTUP_HOME_DIR%"
set "CARGO_HOME=%CARGO_HOME_DIR%"
set "RUSTUP_TOOLCHAIN=%RUST_VERSION%"
for /f "tokens=2" %%v in ('""%RUSTC%" --version" 2^>nul') do set "RUST_HAVE=%%v"
set "RUSTUP_TOOLCHAIN="
if not "%RUST_HAVE%"=="%RUST_VERSION%" goto rust_toolchain_needed
call "%CCM_LIB%" :log "Rust %RUST_VERSION% is already installed in %CCM_HOME%\toolchains\rust"
exit /b 0
:rust_toolchain_needed
if not "%CCM_DRY_RUN%"=="1" goto rust_toolchain_real
call "%CCM_LIB%" :log "would install Rust %RUST_VERSION% with rustup %RUSTUP_VERSION% into %CCM_HOME%\toolchains\rust"
exit /b 0
:rust_toolchain_real
call "%CCM_LIB%" :log "installing Rust %RUST_VERSION% with rustup %RUSTUP_VERSION% (rustup downloads and verifies it)"
set "RUSTUP_HOME=%RUSTUP_HOME_DIR%"
set "CARGO_HOME=%CARGO_HOME_DIR%"
set "RUSTUP_TOOLCHAIN="
rem The gnullvm host links with llvm-mingw, so no Visual Studio is needed (D-32).
"%RUST_INIT%" -y --no-modify-path --profile minimal --default-toolchain %RUST_VERSION% -c clippy -c rustfmt --default-host x86_64-pc-windows-gnullvm
if errorlevel 1 goto rust_toolchain_failed
exit /b 0
:rust_toolchain_failed
call "%CCM_LIB%" :die 1 "rustup-init failed to install Rust %RUST_VERSION%"
exit /b 1


rem :rust_cbindgen  -- build the pinned cbindgen with cargo, unless it is there. Sets CBINDGEN_BIN.
:rust_cbindgen
set "CBINDGEN_ROOT=%CCM_HOME%\toolchains\cbindgen\%CBINDGEN_VERSION%"
set "CBINDGEN_BIN=%CBINDGEN_ROOT%\bin"
if "%CCM_FORCE%"=="1" goto rust_cbindgen_needed
if not exist "%CBINDGEN_BIN%\cbindgen.exe" goto rust_cbindgen_needed
call "%CCM_LIB%" :log "cbindgen %CBINDGEN_VERSION% is already installed in %CBINDGEN_ROOT%"
exit /b 0
:rust_cbindgen_needed
if not "%CCM_DRY_RUN%"=="1" goto rust_cbindgen_real
call "%CCM_LIB%" :log "would build cbindgen %CBINDGEN_VERSION% with cargo install --locked into %CBINDGEN_ROOT%"
exit /b 0
:rust_cbindgen_real
call "%CCM_LIB%" :log "building cbindgen %CBINDGEN_VERSION% (cargo install --locked)"
set "RUSTUP_HOME=%RUSTUP_HOME_DIR%"
set "CARGO_HOME=%CARGO_HOME_DIR%"
set "RUSTUP_TOOLCHAIN=%RUST_VERSION%"
set "PATH=%LLVM_BIN%;%PATH%"
"%CARGO_HOME_DIR%\bin\cargo.exe" install cbindgen --version %CBINDGEN_VERSION% --locked --root "%CBINDGEN_ROOT%"
if errorlevel 1 goto rust_cbindgen_failed
exit /b 0
:rust_cbindgen_failed
call "%CCM_LIB%" :die 1 "building cbindgen %CBINDGEN_VERSION% failed"
exit /b 1


rem :rust_bindgen  -- build the pinned bindgen (the bindgen-cli crate) with cargo, unless it is
rem there. Sets BINDGEN_BIN. It loads libclang when it runs; llvm-mingw has no libclang.dll, so
rem bindgen runs in Docker on Windows for now (D-68).
:rust_bindgen
set "BINDGEN_ROOT=%CCM_HOME%\toolchains\bindgen\%BINDGEN_VERSION%"
set "BINDGEN_BIN=%BINDGEN_ROOT%\bin"
if "%CCM_FORCE%"=="1" goto rust_bindgen_needed
if not exist "%BINDGEN_BIN%\bindgen.exe" goto rust_bindgen_needed
call "%CCM_LIB%" :log "bindgen %BINDGEN_VERSION% is already installed in %BINDGEN_ROOT%"
exit /b 0
:rust_bindgen_needed
if not "%CCM_DRY_RUN%"=="1" goto rust_bindgen_real
call "%CCM_LIB%" :log "would build bindgen %BINDGEN_VERSION% with cargo install --locked into %BINDGEN_ROOT%"
exit /b 0
:rust_bindgen_real
call "%CCM_LIB%" :log "building bindgen %BINDGEN_VERSION% (cargo install --locked)"
set "RUSTUP_HOME=%RUSTUP_HOME_DIR%"
set "CARGO_HOME=%CARGO_HOME_DIR%"
set "RUSTUP_TOOLCHAIN=%RUST_VERSION%"
set "PATH=%LLVM_BIN%;%PATH%"
"%CARGO_HOME_DIR%\bin\cargo.exe" install bindgen-cli --version %BINDGEN_VERSION% --locked --root "%BINDGEN_ROOT%"
if errorlevel 1 goto rust_bindgen_failed
exit /b 0
:rust_bindgen_failed
call "%CCM_LIB%" :die 1 "building bindgen %BINDGEN_VERSION% failed"
exit /b 1


rem :rust_corrosion  -- install Corrosion from its source with the pinned CMake. Sets
rem CORROSION_PREFIX.
:rust_corrosion
call "%CCM_LIB%" :install_tool rust corrosion
if errorlevel 1 exit /b %errorlevel%
set "CORROSION_VERSION=%CCM_ROW_VERSION%"
set "CO_SRC=%CCM_TOOL_BIN_DIR%"
set "CO_ROOT=%CCM_HOME%\toolchains\corrosion\%CORROSION_VERSION%"
set "CORROSION_PREFIX=%CO_ROOT%\install"
if "%CCM_FORCE%"=="1" goto rust_corrosion_needed
if not exist "%CORROSION_PREFIX%\.built" goto rust_corrosion_needed
call "%CCM_LIB%" :log "Corrosion %CORROSION_VERSION% is already installed in %CORROSION_PREFIX%"
exit /b 0
:rust_corrosion_needed
if not "%CCM_DRY_RUN%"=="1" goto rust_corrosion_real
call "%CCM_LIB%" :log "would install Corrosion %CORROSION_VERSION% into %CORROSION_PREFIX%"
exit /b 0
:rust_corrosion_real
set "CO_BUILD=%CO_ROOT%\.build-%RANDOM%"
set "CO_TMP=%CO_ROOT%\.install-%RANDOM%"
set "CO_LOG=%CO_ROOT%\build.log"
if exist "%CO_BUILD%" rmdir /s /q "%CO_BUILD%"
if exist "%CO_TMP%" rmdir /s /q "%CO_TMP%"
call "%CCM_LIB%" :log "installing Corrosion %CORROSION_VERSION% (log: %CO_LOG%)"
rem Corrosion's configure looks for rustc on PATH.
set "RUSTUP_HOME=%RUSTUP_HOME_DIR%"
set "CARGO_HOME=%CARGO_HOME_DIR%"
set "RUSTUP_TOOLCHAIN=%RUST_VERSION%"
set "PATH=%CARGO_HOME_DIR%\bin;%PATH%"
"%CMAKE%" -S "%CO_SRC:\=/%" -B "%CO_BUILD:\=/%" -G Ninja "-DCMAKE_MAKE_PROGRAM=%NINJA:\=/%" "-DCMAKE_C_COMPILER=%CC_PATH:\=/%" "-DCMAKE_CXX_COMPILER=%CXX_PATH:\=/%" -DCORROSION_BUILD_TESTS=OFF "-DCMAKE_INSTALL_PREFIX=%CO_TMP:\=/%" >"%CO_LOG%" 2>&1
if errorlevel 1 goto rust_corrosion_failed
"%CMAKE%" --build "%CO_BUILD:\=/%" >>"%CO_LOG%" 2>&1
if errorlevel 1 goto rust_corrosion_failed
"%CMAKE%" --install "%CO_BUILD:\=/%" >>"%CO_LOG%" 2>&1
if errorlevel 1 goto rust_corrosion_failed
>"%CO_TMP%\.built" echo(%CCM_ROW_SHA256%
if exist "%CORROSION_PREFIX%" rmdir /s /q "%CORROSION_PREFIX%"
rmdir /s /q "%CO_BUILD%"
move "%CO_TMP%" "%CORROSION_PREFIX%" >nul
if errorlevel 1 goto rust_corrosion_move_failed
call "%CCM_LIB%" :log "installed Corrosion %CORROSION_VERSION% into %CORROSION_PREFIX%"
exit /b 0
:rust_corrosion_failed
if exist "%CO_BUILD%" rmdir /s /q "%CO_BUILD%"
if exist "%CO_TMP%" rmdir /s /q "%CO_TMP%"
call "%CCM_LIB%" :die 1 "installing Corrosion failed; the full log is %CO_LOG%"
exit /b 1
:rust_corrosion_move_failed
call "%CCM_LIB%" :die 1 "could not move Corrosion into %CORROSION_PREFIX%"
exit /b 1


:rust_write_env
set "ENV_FILE=%CCM_HOME%\toolchains\rust.env"
set "ACTIVATE=%CCM_HOME%\toolchains\activate-rust.bat"
if not "%CCM_DRY_RUN%"=="1" goto rust_write_env_real
call "%CCM_LIB%" :log "would write %ENV_FILE% and %ACTIVATE%"
exit /b 0
:rust_write_env_real
set "RUST_PREPEND=%CARGO_HOME_DIR%\bin;%NEXTEST_BIN%;%CBINDGEN_BIN%;%BINDGEN_BIN%;%LLVM_BIN%;%CMAKE_BIN%;%NINJA_BIN%;%EXTRA_BIN%"
set "RUST_PREFIXES=%GTEST_PREFIX%;%CORROSION_PREFIX%"
>"%ENV_FILE%.tmp" echo(# CodeCoreMorph Rust toolchain (with the C++ one), written by install-toolchain.
if errorlevel 1 goto rust_write_env_failed
>>"%ENV_FILE%.tmp" echo(# Do not edit. rust %RUST_VERSION% (rustup %RUSTUP_VERSION%), cargo-nextest %NEXTEST_VERSION%, cbindgen %CBINDGEN_VERSION%, bindgen %BINDGEN_VERSION%,
>>"%ENV_FILE%.tmp" echo(# corrosion %CORROSION_VERSION%, %LLVM_ID%, cmake %CMAKE_VERSION%, ninja %NINJA_VERSION%, googletest %GTEST_VERSION% and %GTEST98_VERSION%
>>"%ENV_FILE%.tmp" echo(PATH_PREPEND=%RUST_PREPEND%
>>"%ENV_FILE%.tmp" echo(CC=%CC_PATH%
>>"%ENV_FILE%.tmp" echo(CXX=%CXX_PATH%
>>"%ENV_FILE%.tmp" echo(CMAKE_GENERATOR=Ninja
>>"%ENV_FILE%.tmp" echo(CMAKE_PREFIX_PATH=%RUST_PREFIXES%
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_CXX98_PREFIX_PATH=%GTEST98_PREFIX%
>>"%ENV_FILE%.tmp" echo(RUSTUP_HOME=%RUSTUP_HOME_DIR%
>>"%ENV_FILE%.tmp" echo(CARGO_HOME=%CARGO_HOME_DIR%
>>"%ENV_FILE%.tmp" echo(RUSTUP_TOOLCHAIN=%RUST_VERSION%
rem Builds never reach the network; crates come from the fetch step (SPEC 11.5).
>>"%ENV_FILE%.tmp" echo(CARGO_NET_OFFLINE=true
move /y "%ENV_FILE%.tmp" "%ENV_FILE%" >nul
if errorlevel 1 goto rust_write_env_failed
>"%ACTIVATE%.tmp" echo(@echo off
if errorlevel 1 goto rust_write_activate_failed
>>"%ACTIVATE%.tmp" echo(rem Use the CodeCoreMorph Rust and C++ toolchain in this cmd.exe window:  call "%ACTIVATE%"
>>"%ACTIVATE%.tmp" echo(set "PATH=%RUST_PREPEND%;%%PATH%%"
>>"%ACTIVATE%.tmp" echo(set "CC=%CC_PATH%"
>>"%ACTIVATE%.tmp" echo(set "CXX=%CXX_PATH%"
>>"%ACTIVATE%.tmp" echo(set "CMAKE_GENERATOR=Ninja"
>>"%ACTIVATE%.tmp" echo(set "CMAKE_PREFIX_PATH=%RUST_PREFIXES%"
>>"%ACTIVATE%.tmp" echo(set "RUSTUP_HOME=%RUSTUP_HOME_DIR%"
>>"%ACTIVATE%.tmp" echo(set "CARGO_HOME=%CARGO_HOME_DIR%"
>>"%ACTIVATE%.tmp" echo(set "RUSTUP_TOOLCHAIN=%RUST_VERSION%"
move /y "%ACTIVATE%.tmp" "%ACTIVATE%" >nul
if errorlevel 1 goto rust_write_activate_failed
call "%CCM_LIB%" :log "wrote %ENV_FILE%"
echo(codecoremorph: to use the toolchain in your own shell, run: call "%ACTIVATE%"
exit /b 0
:rust_write_env_failed
call "%CCM_LIB%" :die 1 "could not write %ENV_FILE%"
exit /b 1
:rust_write_activate_failed
call "%CCM_LIB%" :die 1 "could not write %ACTIVATE%"
exit /b 1


:install_java
if defined CCM_OFFLINE goto install_java_offline
set "JDK_IDS="
rem The loop's calls stop at the first failure, so start from errorlevel 0.
ver >nul
for %%n in (%JAVA_STANDARDS%) do call :java_jdk %%n
if errorlevel 1 exit /b %errorlevel%
if not defined JDK_NEWEST exit /b 1
set "JDK_IDS=%JDK_IDS:~0,-1%"
call "%CCM_LIB%" :install_tool java maven
if errorlevel 1 exit /b %errorlevel%
set "MAVEN_VERSION=%CCM_ROW_VERSION%"
set "MAVEN_BIN=%CCM_TOOL_BIN_DIR%"
set "MAVEN_HOME_DIR=%CCM_TOOL_BIN_DIR:~0,-4%"
set "MAVEN_LAUNCHER="
for %%f in ("%MAVEN_HOME_DIR%\boot\plexus-classworlds-*.jar") do set "MAVEN_LAUNCHER=%%~ff"
call "%CCM_LIB%" :install_tool java spoon
if errorlevel 1 exit /b %errorlevel%
set "SPOON_VERSION=%CCM_ROW_VERSION%"
for %%u in ("%CCM_ROW_URL%") do set "SPOON_JAR=%CCM_TOOL_DIR%\%%~nxu"
call :java_fill_repository
if errorlevel 1 exit /b %errorlevel%
call :java_write_env
exit /b %errorlevel%
:install_java_offline
call "%CCM_LIB%" :die 2 "--offline doesn't apply to java: Maven downloads OpenRewrite and the plugins builds run offline itself"
exit /b 2


rem :java_jdk N  -- install the JDK of Java standard N. Sets JDK_HOME_N, JDK_NEWEST and
rem JDK_NEWEST_BIN, and adds it to JDK_IDS. Every JDK has a Windows build.
:java_jdk
if errorlevel 1 exit /b %errorlevel%
call "%CCM_LIB%" :install_tool java jdk%~1
if errorlevel 1 exit /b %errorlevel%
set "JDK_HOME_%~1=%CCM_TOOL_BIN_DIR:~0,-4%"
set "JDK_NEWEST=%CCM_TOOL_BIN_DIR:~0,-4%"
set "JDK_NEWEST_BIN=%CCM_TOOL_BIN_DIR%"
set "JDK_IDS=%JDK_IDS% jdk%~1 %CCM_ROW_VERSION%,"
exit /b 0


rem :java_fill_repository  -- write CodeCoreMorph's Maven settings (builds use them instead of
rem the user's ~/.m2/settings.xml, its mirrors and credentials) and fill the tools repository,
rem unless it already holds the same artifacts. Sets MAVEN_SETTINGS and MAVEN_TOOLS_REPO.
:java_fill_repository
set "JAVA_ROOT=%CCM_HOME%\toolchains\java"
set "MAVEN_SETTINGS=%JAVA_ROOT%\settings.xml"
set "MAVEN_TOOLS_REPO=%JAVA_ROOT%\m2"
set "M2_LOG=%JAVA_ROOT%\m2.log"
set "M2_STAMP=maven %MAVEN_VERSION% %REWRITE_PLUGIN% %REWRITE_RECIPES% %DEPENDENCY_PLUGIN%"
if "%CCM_FORCE%"=="1" goto java_fill_needed
if not exist "%MAVEN_TOOLS_REPO%\.built" goto java_fill_needed
set "M2_HAVE="
for /f "usebackq delims=" %%m in ("%MAVEN_TOOLS_REPO%\.built") do if not defined M2_HAVE set "M2_HAVE=%%m"
if not "%M2_HAVE%"=="%M2_STAMP%" goto java_fill_needed
call "%CCM_LIB%" :log "the Maven tools repository is already filled in %MAVEN_TOOLS_REPO%"
exit /b 0
:java_fill_needed
if not "%CCM_DRY_RUN%"=="1" goto java_fill_real
call "%CCM_LIB%" :log "would download OpenRewrite and the dependency step's plugins with Maven into %MAVEN_TOOLS_REPO%"
exit /b 0
:java_fill_real
if not exist "%JAVA_ROOT%" mkdir "%JAVA_ROOT%"
if not exist "%JAVA_ROOT%" goto java_fill_mkdir_failed
>"%MAVEN_SETTINGS%.tmp" echo(^<settings xmlns="http://maven.apache.org/SETTINGS/1.2.0"^>
if errorlevel 1 goto java_settings_failed
>>"%MAVEN_SETTINGS%.tmp" echo(  ^<!-- CodeCoreMorph builds use these settings instead of ~/.m2/settings.xml.
>>"%MAVEN_SETTINGS%.tmp" echo(       Written by install-toolchain. Do not edit. --^>
>>"%MAVEN_SETTINGS%.tmp" echo(  ^<interactiveMode^>false^</interactiveMode^>
>>"%MAVEN_SETTINGS%.tmp" echo(^</settings^>
move /y "%MAVEN_SETTINGS%.tmp" "%MAVEN_SETTINGS%" >nul
if errorlevel 1 goto java_settings_failed
set "M2_TMP=%JAVA_ROOT%\.m2-%RANDOM%"
if exist "%M2_TMP%" rmdir /s /q "%M2_TMP%"
type nul >"%M2_LOG%"
call "%CCM_LIB%" :log "downloading OpenRewrite and the dependency step's plugins with Maven (log: %M2_LOG%)"
ver >nul
for %%a in (%REWRITE_PLUGIN% %REWRITE_RECIPES% %DEPENDENCY_PLUGIN%) do call :java_get %%a
if errorlevel 1 exit /b %errorlevel%
>"%M2_TMP%\.built" echo(%M2_STAMP%
if exist "%MAVEN_TOOLS_REPO%" rmdir /s /q "%MAVEN_TOOLS_REPO%"
move "%M2_TMP%" "%MAVEN_TOOLS_REPO%" >nul
if errorlevel 1 goto java_fill_move_failed
call "%CCM_LIB%" :log "filled the Maven tools repository in %MAVEN_TOOLS_REPO%"
exit /b 0
:java_fill_mkdir_failed
call "%CCM_LIB%" :die 1 "could not create %JAVA_ROOT%"
exit /b 1
:java_settings_failed
call "%CCM_LIB%" :die 1 "could not write %MAVEN_SETTINGS%"
exit /b 1
:java_fill_move_failed
call "%CCM_LIB%" :die 1 "could not move the Maven repository into %MAVEN_TOOLS_REPO%"
exit /b 1


rem :java_get ARTIFACT  -- download ARTIFACT and its dependencies into M2_TMP, outside any
rem project, so no POM's build extensions load.
:java_get
if errorlevel 1 exit /b %errorlevel%
pushd "%JAVA_ROOT%"
set "JAVA_HOME=%JDK_NEWEST%"
call "%MAVEN_BIN%\mvn.cmd" -B -ntp -C -s "%MAVEN_SETTINGS%" "-Dmaven.repo.local=%M2_TMP%" "%DEPENDENCY_PLUGIN%:get" "-Dartifact=%~1" >>"%M2_LOG%" 2>&1
set "M2_RESULT=%errorlevel%"
popd
if "%M2_RESULT%"=="0" exit /b 0
if exist "%M2_TMP%" rmdir /s /q "%M2_TMP%"
call "%CCM_LIB%" :die 1 "Maven could not download %~1; the full log is %M2_LOG%"
exit /b 1


:java_write_env
set "ENV_FILE=%CCM_HOME%\toolchains\java.env"
set "ACTIVATE=%CCM_HOME%\toolchains\activate-java.bat"
if not "%CCM_DRY_RUN%"=="1" goto java_write_env_real
call "%CCM_LIB%" :log "would write %ENV_FILE% and %ACTIVATE%"
exit /b 0
:java_write_env_real
set "JAVA_PREPEND=%MAVEN_BIN%;%JDK_NEWEST_BIN%"
>"%ENV_FILE%.tmp" echo(# CodeCoreMorph Java toolchain, written by install-toolchain. Do not edit.
if errorlevel 1 goto java_write_env_failed
>>"%ENV_FILE%.tmp" echo(#%JDK_IDS%, maven %MAVEN_VERSION%, spoon %SPOON_VERSION%
>>"%ENV_FILE%.tmp" echo(PATH_PREPEND=%JAVA_PREPEND%
rem The newest JDK; the Java adapter sets JAVA_HOME to the build standard's own.
>>"%ENV_FILE%.tmp" echo(JAVA_HOME=%JDK_NEWEST%
for %%n in (%JAVA_STANDARDS%) do call :java_env_jdk %%n
rem The adapter starts Maven's launcher on the build standard's JDK itself, not mvn.
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_MAVEN_HOME=%MAVEN_HOME_DIR%
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_MAVEN_LAUNCHER=%MAVEN_LAUNCHER%
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_MAVEN_SETTINGS=%MAVEN_SETTINGS%
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_MAVEN_TOOLS_REPO=%MAVEN_TOOLS_REPO%
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_MAVEN_DEPENDENCY_PLUGIN=%DEPENDENCY_PLUGIN%
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_REWRITE_PLUGIN=%REWRITE_PLUGIN%
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_REWRITE_RECIPES=%REWRITE_RECIPES%
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_SPOON_JAR=%SPOON_JAR%
move /y "%ENV_FILE%.tmp" "%ENV_FILE%" >nul
if errorlevel 1 goto java_write_env_failed
>"%ACTIVATE%.tmp" echo(@echo off
if errorlevel 1 goto java_write_activate_failed
>>"%ACTIVATE%.tmp" echo(rem Use the CodeCoreMorph Java toolchain in this cmd.exe window:  call "%ACTIVATE%"
>>"%ACTIVATE%.tmp" echo(set "PATH=%JAVA_PREPEND%;%%PATH%%"
>>"%ACTIVATE%.tmp" echo(set "JAVA_HOME=%JDK_NEWEST%"
move /y "%ACTIVATE%.tmp" "%ACTIVATE%" >nul
if errorlevel 1 goto java_write_activate_failed
call "%CCM_LIB%" :log "wrote %ENV_FILE%"
echo(codecoremorph: to use the toolchain in your own shell, run: call "%ACTIVATE%"
exit /b 0
:java_write_env_failed
call "%CCM_LIB%" :die 1 "could not write %ENV_FILE%"
exit /b 1
:java_env_jdk
call set "JDK_LINE=%%JDK_HOME_%~1%%"
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_JDK_%~1=%JDK_LINE%
exit /b 0
:java_write_activate_failed
call "%CCM_LIB%" :die 1 "could not write %ACTIVATE%"
exit /b 1


:install_csharp
call "%CCM_LIB%" :install_tool csharp dotnet-sdk
if errorlevel 1 exit /b %errorlevel%
set "SDK_VERSION=%CCM_ROW_VERSION%"
set "SDK_DIR=%CCM_TOOL_DIR%"
set "RUNTIME_IDS="
rem The loop's calls stop at the first failure, so start from errorlevel 0.
ver >nul
for %%n in (%CSHARP_RUNTIMES%) do call :csharp_runtime %%n
if errorlevel 1 exit /b %errorlevel%
set "RUNTIME_IDS=%RUNTIME_IDS:~0,-1%"
call :csharp_write_nuget_config
if errorlevel 1 exit /b %errorlevel%
call :csharp_write_env
exit /b %errorlevel%


rem :csharp_runtime N  -- install the .NET N runtime and copy it into the SDK's dotnet root
rem (SDK_DIR), where the dotnet host looks for it, unless it is there already.
:csharp_runtime
if errorlevel 1 exit /b %errorlevel%
call "%CCM_LIB%" :install_tool csharp dotnet-runtime%~1
if errorlevel 1 exit /b %errorlevel%
set "RUNTIME_IDS=%RUNTIME_IDS% dotnet-runtime%~1 %CCM_ROW_VERSION%,"
set "RT_SRC=%CCM_TOOL_DIR%\shared\Microsoft.NETCore.App\%CCM_ROW_VERSION%"
set "RT_DST=%SDK_DIR%\shared\Microsoft.NETCore.App\%CCM_ROW_VERSION%"
if "%CCM_FORCE%"=="1" goto csharp_runtime_needed
if exist "%RT_DST%\.installed" exit /b 0
:csharp_runtime_needed
if not "%CCM_DRY_RUN%"=="1" goto csharp_runtime_real
call "%CCM_LIB%" :log "would add the .NET %CCM_ROW_VERSION% runtime to %SDK_DIR%"
exit /b 0
:csharp_runtime_real
if not exist "%RT_SRC%" goto csharp_runtime_missing
if exist "%RT_DST%.tmp" rmdir /s /q "%RT_DST%.tmp"
xcopy "%RT_SRC%" "%RT_DST%.tmp\" /e /i /q /y >nul
if errorlevel 1 goto csharp_runtime_failed
>"%RT_DST%.tmp\.installed" echo(%CCM_ROW_VERSION%
if errorlevel 1 goto csharp_runtime_failed
if exist "%RT_DST%" rmdir /s /q "%RT_DST%"
move "%RT_DST%.tmp" "%RT_DST%" >nul
if errorlevel 1 goto csharp_runtime_failed
call "%CCM_LIB%" :log "added the .NET %CCM_ROW_VERSION% runtime to %SDK_DIR%"
exit /b 0
:csharp_runtime_missing
call "%CCM_LIB%" :die 1 "the .NET %CCM_ROW_VERSION% runtime archive has no %RT_SRC%"
exit /b 1
:csharp_runtime_failed
if exist "%RT_DST%.tmp" rmdir /s /q "%RT_DST%.tmp"
call "%CCM_LIB%" :die 1 "could not copy the .NET %CCM_ROW_VERSION% runtime into %SDK_DIR%"
exit /b 1


rem :csharp_write_nuget_config  -- NuGet's config for restores (nuget.org alone; the user's
rem NuGet config is never read). Sets NUGET_CONFIG.
:csharp_write_nuget_config
set "CSHARP_ROOT=%CCM_HOME%\toolchains\csharp"
set "NUGET_CONFIG=%CSHARP_ROOT%\nuget.config"
if not "%CCM_DRY_RUN%"=="1" goto csharp_nuget_real
call "%CCM_LIB%" :log "would write %NUGET_CONFIG%"
exit /b 0
:csharp_nuget_real
if not exist "%CSHARP_ROOT%" mkdir "%CSHARP_ROOT%"
if not exist "%CSHARP_ROOT%" goto csharp_nuget_mkdir_failed
>"%NUGET_CONFIG%.tmp" echo(^<?xml version="1.0" encoding="utf-8"?^>
if errorlevel 1 goto csharp_nuget_failed
>>"%NUGET_CONFIG%.tmp" echo(^<!-- CodeCoreMorph restores use this config instead of the user's NuGet config.
>>"%NUGET_CONFIG%.tmp" echo(     Written by install-toolchain. Do not edit. --^>
>>"%NUGET_CONFIG%.tmp" echo(^<configuration^>
>>"%NUGET_CONFIG%.tmp" echo(  ^<packageSources^>
>>"%NUGET_CONFIG%.tmp" echo(    ^<clear /^>
>>"%NUGET_CONFIG%.tmp" echo(    ^<add key="nuget.org" value="https://api.nuget.org/v3/index.json" protocolVersion="3" /^>
>>"%NUGET_CONFIG%.tmp" echo(  ^</packageSources^>
>>"%NUGET_CONFIG%.tmp" echo(^</configuration^>
move /y "%NUGET_CONFIG%.tmp" "%NUGET_CONFIG%" >nul
if errorlevel 1 goto csharp_nuget_failed
exit /b 0
:csharp_nuget_mkdir_failed
call "%CCM_LIB%" :die 1 "could not create %CSHARP_ROOT%"
exit /b 1
:csharp_nuget_failed
call "%CCM_LIB%" :die 1 "could not write %NUGET_CONFIG%"
exit /b 1


:csharp_write_env
set "ENV_FILE=%CCM_HOME%\toolchains\csharp.env"
set "ACTIVATE=%CCM_HOME%\toolchains\activate-csharp.bat"
if not "%CCM_DRY_RUN%"=="1" goto csharp_write_env_real
call "%CCM_LIB%" :log "would write %ENV_FILE% and %ACTIVATE%"
exit /b 0
:csharp_write_env_real
set "REF_PACK="
for /d %%d in ("%SDK_DIR%\packs\Microsoft.NETCore.App.Ref\*") do for /d %%r in ("%%~fd\ref\net*") do set "REF_PACK=%%~fr"
if not defined REF_PACK goto csharp_no_ref_pack
>"%ENV_FILE%.tmp" echo(# CodeCoreMorph C# toolchain, written by install-toolchain. Do not edit.
if errorlevel 1 goto csharp_write_env_failed
>>"%ENV_FILE%.tmp" echo(# dotnet-sdk %SDK_VERSION%,%RUNTIME_IDS%
>>"%ENV_FILE%.tmp" echo(PATH_PREPEND=%SDK_DIR%
>>"%ENV_FILE%.tmp" echo(DOTNET_ROOT=%SDK_DIR%
>>"%ENV_FILE%.tmp" echo(DOTNET_MULTILEVEL_LOOKUP=0
>>"%ENV_FILE%.tmp" echo(DOTNET_CLI_TELEMETRY_OPTOUT=1
>>"%ENV_FILE%.tmp" echo(DOTNET_NOLOGO=1
>>"%ENV_FILE%.tmp" echo(DOTNET_SKIP_FIRST_TIME_EXPERIENCE=1
>>"%ENV_FILE%.tmp" echo(DOTNET_GENERATE_ASPNET_CERTIFICATE=false
>>"%ENV_FILE%.tmp" echo(DOTNET_ADD_GLOBAL_TOOLS_TO_PATH=false
>>"%ENV_FILE%.tmp" echo(DOTNET_CLI_WORKLOAD_UPDATE_NOTIFY_DISABLE=1
rem The adapter compiles its index helper with the SDK's csc and Roslyn, against this pack.
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_DOTNET=%SDK_DIR%\dotnet.exe
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_DOTNET_SDK=%SDK_DIR%\sdk\%SDK_VERSION%
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_DOTNET_REF_PACK=%REF_PACK%
>>"%ENV_FILE%.tmp" echo(CODECOREMORPH_NUGET_CONFIG=%NUGET_CONFIG%
move /y "%ENV_FILE%.tmp" "%ENV_FILE%" >nul
if errorlevel 1 goto csharp_write_env_failed
>"%ACTIVATE%.tmp" echo(@echo off
if errorlevel 1 goto csharp_write_activate_failed
>>"%ACTIVATE%.tmp" echo(rem Use the CodeCoreMorph C# toolchain in this cmd.exe window:  call "%ACTIVATE%"
>>"%ACTIVATE%.tmp" echo(set "PATH=%SDK_DIR%;%%PATH%%"
>>"%ACTIVATE%.tmp" echo(set "DOTNET_ROOT=%SDK_DIR%"
>>"%ACTIVATE%.tmp" echo(set "DOTNET_MULTILEVEL_LOOKUP=0"
>>"%ACTIVATE%.tmp" echo(set "DOTNET_CLI_TELEMETRY_OPTOUT=1"
move /y "%ACTIVATE%.tmp" "%ACTIVATE%" >nul
if errorlevel 1 goto csharp_write_activate_failed
call "%CCM_LIB%" :log "wrote %ENV_FILE%"
echo(codecoremorph: to use the toolchain in your own shell, run: call "%ACTIVATE%"
exit /b 0
:csharp_no_ref_pack
call "%CCM_LIB%" :die 1 "the .NET SDK in %SDK_DIR% has no reference pack"
exit /b 1
:csharp_write_env_failed
call "%CCM_LIB%" :die 1 "could not write %ENV_FILE%"
exit /b 1
:csharp_write_activate_failed
call "%CCM_LIB%" :die 1 "could not write %ACTIVATE%"
exit /b 1


:expected_one_language
call "%CCM_LIB%" :die 2 "expected one language: cpp, rust, java, csharp or all (see --help)"
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
echo   rust  the cpp toolchain, plus Rust (through rustup, with clippy and
echo         rustfmt), cargo-nextest, cbindgen, bindgen and Corrosion, for C++
echo         and C to Rust ports; rustup and cargo download Rust, cbindgen and
echo         bindgen themselves, so --offline doesn't apply
echo   java  a Temurin JDK for each Java standard (8, 11, 17, 21, 25), Maven, the
echo         Spoon jar, and a Maven repository with OpenRewrite and the plugins
echo         builds run offline; Maven downloads those itself, so --offline
echo         doesn't apply
echo   csharp
echo         the .NET SDK (with dotnet format and Roslyn) and the .NET 8 and 6
echo         runtimes beside its own, so tests run on their framework's runtime
echo   all   every available language (cpp, rust, java and csharp)
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
