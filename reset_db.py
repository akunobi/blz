import sqlite3

def init_db():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    
    # Tabla para mensajes (Sistema de Tickets/Chat)
    # Guardamos el canal para saber a qué ticket pertenece
    c.execute('''
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id INTEGER,
            channel_name TEXT,
            author_name TEXT,
            author_avatar TEXT,
            content TEXT,
            message_id INTEGER UNIQUE,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Tabla simple para Stats (Opcional por ahora, pero la dejamos lista)
    c.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT, -- 'offensive' o 'gk'
            data TEXT -- JSON string con los valores
        )
    ''')
    
    conn.commit()
    conn.close()
    print(">>> [SYSTEM]: DATABASE RESET COMPLETE. EGOIST MEMORY CLEARED.")

if __name__ == '__main__':
    init_db()