@rem Gradle startup for Windows. Requires JDK 17+.
@if "%DEBUG%"=="" @echo off
set DIRNAME=%~dp0
if "%DIRNAME%"=="" set DIRNAME=.
set APP_BASE_NAME=%~n0
set APP_HOME=%DIRNAME%
for %%i in ("%APP_HOME%") do set APP_HOME=%%~fi
set DEFAULT_JVM_OPTS="-Xmx64m" "-Xms64m"
if defined JAVA_HOME (
  set JAVA_EXE=%JAVA_HOME%/bin/java.exe
) else (
  set JAVA_EXE=java.exe
)
"%JAVA_EXE%" -version >NUL 2>&1
if %ERRORLEVEL% neq 0 (
  echo ERROR: JAVA_HOME is not set and no 'java' command could be found in PATH. 1>&2
  exit /b 1
)
set CLASSPATH=%APP_HOME%\gradle\wrapper\gradle-wrapper.jar
"%JAVA_EXE%" %DEFAULT_JVM_OPTS% %JAVA_OPTS% %GRADLE_OPTS% "-Dorg.gradle.appname=%APP_BASE_NAME%" -classpath "%CLASSPATH%" org.gradle.wrapper.GradleWrapperMain %*
