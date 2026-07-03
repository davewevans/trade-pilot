@echo off
REM Local relay wrapper for Task Scheduler (cd into repo so notify.py finds .env).
cd /d "C:\Users\davew\repos\trade-pilot"
powershell -NoProfile -ExecutionPolicy Bypass -File "C:\Users\davew\repos\trade-pilot\scripts\eval\send-eval-notification.ps1"
