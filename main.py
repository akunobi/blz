# main.py
import threading

from dashboard import init_dashboard
from bot import _run_with_backoff as run_bot
from mod_bot import _run_with_backoff as run_mod_bot

if __name__ == "__main__":
    print("Iniciando el panel web...")
    init_dashboard()  # 1. Arranca Flask en segundo plano

    print("Iniciando el bot de moderación...")
    threading.Thread(target=run_mod_bot, daemon=True).start()  # 2. Bot de moderación en su propio hilo

    print("Iniciando el bot de Discord...")
    run_bot()  # 3. Arranca el bot principal (bloquea el hilo principal)
