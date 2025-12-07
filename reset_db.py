from bot import app, db

with app.app_context():
    print("⚠️ Deleting old tables...")
    db.drop_all()
    print("✅ Creating new tables with correct columns...")
    db.create_all()
    print("🚀 Database reset complete!")