from flask import Flask, render_template, request, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os

app = Flask(__name__)

# --- CONFIGURACIÓN DE BASE DE DATOS (HÍBRIDA) ---
# 1. Intenta obtener la URL de la base de datos de las variables de entorno (Render)
database_url = os.environ.get('DATABASE_URL')

# 2. Corrección para Render: SQLAlchemy necesita 'postgresql://' pero Render da 'postgres://'
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

# 3. Si no hay URL (estás en local), usa SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///database.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- MODELOS DE BASE DE DATOS ---
class Ticket(db.Model):
    id = db.Column(db.String(50), primary_key=True) # ID del Canal de Discord
    name = db.Column(db.String(100))
    region = db.Column(db.String(10)) # EU, NA, ASIA
    status = db.Column(db.String(20), default='open') # open, completed

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.String(50), db.ForeignKey('ticket.id'))
    sender = db.Column(db.String(50)) # 'DiscordUser' o 'WebAgent'
    content = db.Column(db.Text)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Flags de sincronización
    read_by_web = db.Column(db.Boolean, default=False)     # Para notificaciones en web
    synced_to_discord = db.Column(db.Boolean, default=True) # True si viene de Discord, False si la web lo escribió

# Crear tablas si no existen
with app.app_context():
    db.create_all()

# --- RUTAS WEB (ENDPOINTS) ---

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/get_tickets')
def get_tickets():
    # Obtener solo tickets abiertos
    tickets = Ticket.query.filter_by(status='open').all()
    ticket_list = []
    
    for t in tickets:
        # Contar mensajes no leídos
        unread_count = Message.query.filter(
            Message.ticket_id == t.id, 
            Message.read_by_web == False,
            Message.sender != 'WebAgent'
        ).count()
        
        ticket_list.append({
            'id': t.id,
            'name': t.name,
            'region': t.region,
            'unread': unread_count
        })
    return jsonify(ticket_list)

@app.route('/api/get_messages/<ticket_id>')
def get_messages(ticket_id):
    # 1. Marcar mensajes de Discord como leídos al abrir el chat
    unread_msgs = Message.query.filter(
        Message.ticket_id == ticket_id, 
        Message.read_by_web == False,
        Message.sender != 'WebAgent'
    ).all()
    
    for msg in unread_msgs:
        msg.read_by_web = True
    db.session.commit()
    
    # 2. Obtener historial
    messages = Message.query.filter_by(ticket_id=ticket_id).order_by(Message.timestamp).all()
    
    return jsonify([{
        'sender': m.sender,
        'content': m.content,
        'timestamp': m.timestamp.strftime('%H:%M')
    } for m in messages])

@app.route('/api/send_message', methods=['POST'])
def send_message():
    data = request.json
    
    # Crear mensaje
    new_msg = Message(
        ticket_id=data['ticket_id'],
        sender='WebAgent',     # Identificador de la web
        content=data['content'],
        read_by_web=True,      # Ya está leído porque lo escribimos nosotros
        synced_to_discord=False # IMPORTANTE: Esto le dice al Bot que debe enviarlo a Discord
    )
    
    db.session.add(new_msg)
    db.session.commit()
    return jsonify({'status': 'sent'})

@app.route('/api/complete_ticket', methods=['POST'])
def complete_ticket():
    data = request.json
    ticket = Ticket.query.get(data['ticket_id'])
    
    if ticket:
        ticket.status = 'completed'
        db.session.commit()
        
    return jsonify({'status': 'completed'})

if __name__ == '__main__':
    app.run(debug=True)