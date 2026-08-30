# main.py
from dashboard import init_dashboard
from bot import _run_with_backoff as run_bot

if __name__ == "__main__":
    print("Iniciando el panel web...")
    init_dashboard()  # 1. Arranca Flask en segundo plano
    
    print("Iniciando el bot de Discord...")
    run_bot()         # 2. Arranca el bot de Discord
