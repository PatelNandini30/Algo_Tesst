@echo off
REM Configure Git to handle line endings automatically on Windows

echo Configuring Git line ending settings...

REM Option 1: Auto-convert to CRLF on checkout, LF on commit (recommended for Windows)
git config --global core.autocrlf true

REM Option 2: If you want to keep LF everywhere (for cross-platform compatibility)
REM git config --global core.autocrlf input

REM Option 3: If you want to disable the warning completely
REM git config --global core.autocrlf false

echo Git line ending configuration updated!
echo Current setting:
git config --global core.autocrlf

pause
