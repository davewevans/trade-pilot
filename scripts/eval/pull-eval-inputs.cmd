@echo off
REM Wrapper so Windows Task Scheduler can run the Friday pull without nested-quote
REM headaches. Point the scheduled task's /TR at THIS file.
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\davew\repos\trade-pilot\scripts\eval\pull-eval-inputs.ps1"
