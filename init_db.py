from app import app, db

print("Inicializando base de datos...")
with app.app_context():
    db.create_all()
    print("¡Tablas creadas exitosamente!")