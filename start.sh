#!/bin/bash

# 1. Ejecutar el bot de Discord en segundo plano.
# El '&' es crucial para que el bot no bloquee la ejecución del script.
# El bot.py importará la base de datos y comenzará su tarea de polling.
echo "Starting Discord Bot..."
python bot.py &

# 2. Iniciar la aplicación web de Flask (app:app) usando Gunicorn.
# Usamos el número de workers recomendado (2 * número de cores + 1).
# El '0.0.0.0:$PORT' es el estándar para Render.
echo "Starting Gunicorn Web Server..."
exec gunicorn --bind 0.0.0.0:$PORT --workers 4 app:app