@echo off
setlocal

echo Updating repository and submodules...
git pull --recurse-submodules

if not exist venv (
    echo Creating virtual environment...
    python -m venv venv
)

echo Activating virtual environment...
call venv\Scripts\activate

echo Checking/Installing dependencies...
pip install -r requirements.txt

echo Starting the bot...
python main.py

pause
