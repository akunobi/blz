from bot import app, db

# Este script borra todo y lo crea de nuevo
with app.app_context():
    print("Eliminando tablas antiguas...")
    db.drop_all()
    print("Creando tablas nuevas con columnas actualizadas...")
    db.create_all()
    print("¡Base de datos reiniciada con éxito!")