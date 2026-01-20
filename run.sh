#!/bin/bash

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Activating virtual environment..."
source venv/bin/activate

echo "Checking/Installing dependencies..."
pip install -r requirements.txt

echo "Starting the bot..."
python3 main.py
