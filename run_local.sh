#!/bin/bash
cd "$(dirname "$0")"
pkill -f "flylocal/app.py" 2>/dev/null
lsof -ti:8877 | xargs kill -9 2>/dev/null
sleep 0.5
echo "Starting FlyLocal..."
/Volumes/Crucial/Users/mousears1090/projects/WebApp/bakan/.venv/bin/python app.py &
sleep 1
open http://localhost:8877
wait
