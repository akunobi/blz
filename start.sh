#!/bin/bash

# 1. Crear las tablas en la base de datos (Ejecuta el script Python)
echo "Running Database Initialization..."
python init_db.py

# 2. Iniciar el bot en segundo plano
echo "Starting Discord Bot..."
python bot.py &

# 3. Iniciar la web (usamos exec para reemplazar el proceso bash por gunicorn)
echo "Starting Gunicorn Web Server..."
exec gunicorn --bind 0.0.0.0:$PORT --workers 4 app:app