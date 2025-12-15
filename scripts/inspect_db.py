import sqlite3, json, os
p = os.path.join(os.path.dirname(__file__), '..', 'database.db')
if not os.path.exists(p):
    p = os.path.join(os.path.dirname(__file__), 'database.db')
conn = sqlite3.connect(p)
conn.row_factory = sqlite3.Row
cur = conn.execute('SELECT id, channel_id, channel_name, author_name, author_id, message_id, content, timestamp FROM messages ORDER BY id DESC LIMIT 100')
rows = [dict(r) for r in cur.fetchall()]
print(json.dumps(rows, indent=2, ensure_ascii=False))
conn.close()
