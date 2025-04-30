# admin_routes.py
import logging
from flask import Blueprint, render_template, request, jsonify, flash, redirect, url_for
from flask_login import login_required, current_user
from functools import wraps # For the decorator
from datetime import datetime, timedelta

# Import necessary things from your other files
from database import db, User, Session
from network_controller import remove_from_allowed_list

# --- Admin Helper Decorator (Needs to be defined or imported here too) ---
def admin_required(f):
    @login_required
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_admin:
            flash('Admin access required.', 'danger')
            return redirect(url_for('index'))
        return f(*args, **kwargs)
    return decorated_function

# Create the Blueprint
# 'admin' is the name of the blueprint
# __name__ helps locate the blueprint's resources
# url_prefix='/admin' automatically adds /admin before all routes in this file
admin_bp = Blueprint('admin', __name__, url_prefix='/admin')

# Now use the blueprint decorator
@admin_bp.route('/') # This will be accessed at /admin/
@admin_required
def admin_dashboard():
    # ... (your dashboard code using render_template, db.session.query, etc.) ...
    active_sessions = Session.query.filter_by(active=True).order_by(Session.start_time.desc()).all()
    yesterday = datetime.utcnow() - timedelta(days=1)
    recent_sessions = Session.query.filter(Session.start_time >= yesterday).order_by(Session.start_time.desc()).all()
    user_count = User.query.count()
    now = datetime.utcnow()
    return render_template('admin/dashboard.html',
                          active_sessions=active_sessions,
                          recent_sessions=recent_sessions,
                          user_count=user_count,
                          now=now)

@admin_bp.route('/users') # Access at /admin/users
@admin_required
def admin_users():
    # ... (your user listing code) ...
    users = User.query.order_by(User.username).all()
    return render_template('admin/users.html', users=users)

@admin_bp.route('/users/create', methods=['POST']) # Access at /admin/users/create
@admin_required
def admin_create_user():
    # ... (your user creation code) ...
    if not request.is_json: return jsonify({'error': 'Request must be JSON'}), 400
    data = request.json
    # (rest of create user logic)
    username = data.get('username')
    password = data.get('password')
    is_admin = data.get('is_admin', False)
    if not username or not password: return jsonify({'error': 'Username and password are required'}), 400
    if User.query.filter_by(username=username).first(): return jsonify({'error': 'Username already exists'}), 409
    user = User(username=username, is_admin=bool(is_admin))
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    logging.info(f"Admin {current_user.username} created user {username} (ID: {user.id})")
    return jsonify({
        'success': True, 'user': { 'id': user.id, 'username': user.username, 'is_admin': user.is_admin, 'created_at': user.created_at.isoformat() }
    }), 201


@admin_bp.route('/users/delete/<int:user_id>', methods=['POST']) # Access at /admin/users/delete/<id>
@admin_required
def admin_delete_user(user_id):
    # ... (your user deletion code) ...
    user_to_delete = User.query.get(user_id)
    if not user_to_delete: return jsonify({'error': 'User not found'}), 404
    if user_to_delete.id == current_user.id: return jsonify({'error': 'Cannot delete yourself'}), 400
    if user_to_delete.is_admin and User.query.filter_by(is_admin=True).count() <= 1: return jsonify({'error': 'Cannot delete the last admin user'}), 400
    active_sessions = Session.query.filter_by(user_id=user_id, active=True).all()
    for session in active_sessions:
        session.end_time = datetime.utcnow()
        session.active = False
        remove_from_allowed_list(session.ip_address)
        logging.info(f"Terminated session {session.id} for user {user_to_delete.username} during deletion.")
    username = user_to_delete.username
    db.session.delete(user_to_delete)
    db.session.commit()
    logging.info(f"Admin {current_user.username} deleted user {username} (ID: {user_id})")
    return jsonify({'success': True})


@admin_bp.route('/sessions/terminate/<int:session_id>', methods=['POST']) # Access at /admin/sessions/terminate/<id>
@admin_required
def admin_terminate_session(session_id):
    # ... (your session termination code) ...
    session = Session.query.get(session_id)
    if not session: return jsonify({'error': 'Session not found'}), 404
    if not session.active: return jsonify({'error': 'Session is already inactive'}), 400
    session.end_time = datetime.utcnow()
    session.active = False
    db.session.commit()
    logging.info(f"Admin {current_user.username} terminated session {session.id} (User: {session.user.username}, IP: {session.ip_address})")
    if remove_from_allowed_list(session.ip_address): logging.info(f"Removed {session.ip_address} from network allowed list.")
    else: logging.warning(f"Attempted to remove {session.ip_address} for terminated session {session.id}, but it wasn't found in the allowed list.")
    return jsonify({'success': True})

# Add any other admin routes here...