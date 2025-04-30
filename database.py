# database.py
from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

# --- User Model (as before) ---
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, index=True, nullable=False)
    password_hash = db.Column(db.String(128), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sessions = db.relationship('Session', backref='user', lazy='dynamic')
    messages = db.relationship('Message', backref='author', lazy='dynamic') # Relationship to messages

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'

# --- Session Model (as before) ---
class Session(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), index=True)
    ip_address = db.Column(db.String(45), nullable=False, index=True)
    mac_address = db.Column(db.String(17), nullable=True)
    device_name = db.Column(db.String(256), nullable=True)
    start_time = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    end_time = db.Column(db.DateTime, nullable=True)
    active = db.Column(db.Boolean, default=True, index=True)

    def __repr__(self):
        return f'<Session {self.id} User {self.user_id} IP {self.ip_address}>'


# --- NEW: Message Model ---
class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, index=True, default=datetime.utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False) # Who sent it
    room = db.Column(db.String(64), index=True, nullable=False) # Which room it belongs to

    def __repr__(self):
        return f'<Message {self.id} Room {self.room} User {self.user_id}>'

    # Optional: Method to convert message to dictionary for sending via SocketIO
    def to_dict(self):
        return {
            'id': self.id,
            'user': self.author.username, # Get username via backref
            'msg': self.body,
            'timestamp': self.timestamp.isoformat() + 'Z', # ISO format UTC
            'room': self.room
        }