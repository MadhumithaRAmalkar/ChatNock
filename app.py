# app.py

# --- GROUP ALL IMPORTS AT THE TOP ---
import os
import logging
import ipaddress
from datetime import datetime, timedelta
from functools import wraps # Needed for admin_required if defined here

from flask import (Flask, redirect, url_for, request, render_template, flash,
                   jsonify, session as flask_session, abort)
from flask_login import (LoginManager, login_user, logout_user, login_required,
                         current_user)
# Import disconnect for potential use later
from flask_socketio import SocketIO, emit, join_room, leave_room, disconnect

# --- Gevent setup (if using gevent) ---
try:
    import gevent.monkey
    gevent.monkey.patch_all()
    ASYNC_MODE = 'gevent'
    logging.info("Using gevent async_mode.")
except ImportError:
    ASYNC_MODE = None
    logging.warning("gevent not found. Flask-SocketIO may use a different async mode.")
    pass # Or raise an error: raise RuntimeError("gevent is required but not installed")


# --- Import project-specific modules ---
from config import Config
# Import db and models (including Message)
from database import db, User, Session, Message
# Import network controller functions
from network_controller import (is_authenticated as nc_is_authenticated,
                                add_to_allowed_list, remove_from_allowed_list,
                                get_mac_address, authenticated_ips)
# Import admin blueprint and potentially its decorator
from admin_routes import admin_bp, admin_required


# --- INITIALIZE FLASK APP (ONCE) ---
app = Flask(__name__)
app.config.from_object(Config)


# --- CONFIGURE EXTENSIONS ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s:%(message)s [%(name)s]')
db.init_app(app)
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "Please log in to access this page."
login_manager.login_message_category = "info"
# Pass manage_session=False if Flask-Login handles sessions primarily
# This can prevent conflicts with how SocketIO uses the session context
socketio = SocketIO(app, async_mode=ASYNC_MODE, manage_session=False)


# --- REGISTER BLUEPRINTS ---
app.register_blueprint(admin_bp)


# --- FLASK-LOGIN USER LOADER ---
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


# --- CREATE DB TABLES AND ADMIN USER ---
with app.app_context():
    # Important: Ensure this runs AFTER db is initialized and models are defined
    db.create_all()
    admin = User.query.filter_by(username=app.config['ADMIN_USERNAME']).first()
    if not admin:
        logging.info(f"Creating admin user: {app.config['ADMIN_USERNAME']}")
        admin = User(username=app.config['ADMIN_USERNAME'], is_admin=True)
        admin.set_password(app.config['ADMIN_PASSWORD'])
        db.session.add(admin); db.session.commit()
        logging.info("Admin user created.")
    else: logging.info("Admin user already exists.")


# --- HELPER FUNCTIONS ---
def is_client_in_managed_network(ip_str):
    try:
        client_ip = ipaddress.ip_address(ip_str)
        for network_str in app.config.get('ALLOWED_NETWORKS', []):
            network = ipaddress.ip_network(network_str, strict=False)
            if client_ip in network: return True
    except ValueError: logging.error(f"Invalid IP address format or network string: {ip_str}"); return False
    except Exception as e: logging.error(f"Error checking managed network for {ip_str}: {e}"); return False
    return False

def is_ip_excluded(ip_str):
    return ip_str in app.config.get('EXCLUDED_IPS', [])


# --- BEFORE REQUEST HANDLER ---
@app.before_request
def check_authentication():
    # This handler mainly focuses on network level auth before accessing standard HTTP routes.
    # SocketIO connections are handled differently (often checking auth within connect handler).
    # Login/logout/static/favicon/socketio are exempt from network checks here.
    client_ip = request.remote_addr
    endpoint = request.endpoint

    if endpoint and (endpoint == 'static' or endpoint == 'favicon' or \
                     endpoint.startswith('socketio') or \
                     endpoint in ['login', 'logout']):
         logging.debug(f"Skipping network auth check for endpoint: {endpoint}")
         return

    if client_ip == app.config.get('PORTAL_IP'):
         logging.debug(f"Skipping network auth check for portal server IP: {client_ip}")
         return

    # Admin routes are protected by @admin_required using Flask-Login, skip network check here
    is_admin_endpoint = endpoint and endpoint.startswith(admin_bp.name + '.')
    if is_admin_endpoint:
        logging.debug(f"Allowing request for potential admin endpoint: {endpoint}, Flask-Login/decorators will handle auth.")
        return

    # Check network rules for other routes
    if not is_client_in_managed_network(client_ip):
        logging.debug(f"IP {client_ip} not in managed network. Allowing request (application logic may still deny).")
        return # Allow access but let application routes handle if login is needed

    if is_ip_excluded(client_ip):
        logging.debug(f"IP {client_ip} is excluded. Allowing request.")
        return

    # If IP is *not* authenticated via network controller, redirect to login
    if not nc_is_authenticated(client_ip):
        logging.info(f"IP {client_ip} needs network authentication for endpoint '{endpoint}'. Redirecting to login.")
        return redirect(url_for('login'))

    # If network authenticated, proceed. Ensure Flask-Login session is established if needed (done within routes usually).
    logging.debug(f"IP {client_ip} network authenticated for endpoint '{endpoint}'. Allowing request.")
    # We don't automatically log in here anymore, let routes handle it explicitly.


# --- MAIN APPLICATION ROUTES ---
@app.route('/')
def index():
    # If already fully logged in (Flask-Login), go to success page
    if current_user.is_authenticated:
        return redirect(url_for('success'))
    # Otherwise, rely on before_request to redirect unauthenticated IPs to login
    # Or, if network authenticated but not Flask-Login, also force login
    client_ip = request.remote_addr
    if nc_is_authenticated(client_ip):
        # Network auth'd but didn't hit the current_user.is_authenticated check
        logging.info(f"Index accessed by network authenticated IP {client_ip} without Flask-Login session. Redirecting to login.")
    # If not network auth'd, before_request should have redirected already, but redirect again just in case.
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        # If already logged in, make sure network access is granted and go to success
        client_ip = request.remote_addr
        if not nc_is_authenticated(client_ip):
             session_db_obj = Session.query.filter_by(user_id=current_user.id, ip_address=client_ip, active=True).order_by(Session.start_time.desc()).first()
             if session_db_obj:
                  logging.info(f"Re-granting network access for already logged in user {current_user.username}")
                  add_to_allowed_list(client_ip, session_db_obj.id, duration=app.config['SESSION_TIMEOUT'])
             else: # Should not happen if Flask-Login session exists, but handle it
                  logout_user(); flash("Session mismatch. Please log in again.", "warning"); return redirect(url_for('login'))
        return redirect(url_for('success'))

    if request.method == 'POST':
        username = request.form.get('username'); password = request.form.get('password'); remember = request.form.get('remember') == 'on'
        if not username or not password: flash('Username and password are required.', 'warning'); return render_template('login.html')
        user = User.query.filter_by(username=username).first()
        if user and user.check_password(password):
            login_user(user, remember=remember); logging.info(f"User {username} logged in successfully via form.")
            client_ip = request.remote_addr; client_mac = get_mac_address(client_ip); client_device = request.user_agent.string
            session_db_obj = Session( user_id=user.id, ip_address=client_ip, mac_address=client_mac, device_name=client_device, start_time=datetime.utcnow(), active=True )
            db.session.add(session_db_obj); db.session.commit(); logging.info(f"Created DB session {session_db_obj.id} for user {user.username} at {client_ip}")
            if add_to_allowed_list(client_ip, session_db_obj.id, duration=app.config.get('SESSION_TIMEOUT', timedelta(hours=2))):
                logging.info(f"Added {client_ip} to network allowed list for session {session_db_obj.id}")
                return redirect(url_for('success')) # Redirect to success page
            else:
                flash('Network access could not be granted. Please contact support.', 'danger')
                session_db_obj.active = False; session_db_obj.end_time = datetime.utcnow(); db.session.commit()
                logout_user(); return render_template('login.html')
        else: flash('Invalid username or password.', 'danger'); logging.warning(f"Failed login attempt for username: {username}")
    return render_template('login.html')


@app.route('/success')
@login_required
def success():
    # Renders the intermediate success page with session info and link to chat
    client_ip = request.remote_addr
    if not nc_is_authenticated(client_ip):
        flash('Your network session may have expired. Please log in again.', 'warning'); logout_user(); return redirect(url_for('login'))
    session_db_obj = Session.query.filter_by(user_id=current_user.id, ip_address=client_ip, active=True).order_by(Session.start_time.desc()).first()
    if not session_db_obj:
        logging.error(f"Inconsistency: IP {client_ip} allowed by NC but no active DB session found for user {current_user.username}.")
        flash('Could not retrieve session details.', 'danger'); remove_from_allowed_list(client_ip); logout_user(); return redirect(url_for('login'))
    now = datetime.utcnow(); session_duration = app.config.get('SESSION_TIMEOUT', timedelta(hours=2)); expiration_time = session_db_obj.start_time + session_duration
    if now >= expiration_time:
        logging.info(f"Session {session_db_obj.id} for {client_ip} found expired on success page access.")
        session_db_obj.end_time = expiration_time; session_db_obj.active = False; db.session.commit()
        remove_from_allowed_list(client_ip); logout_user(); flash('Your session has expired. Please log in again.', 'info'); return redirect(url_for('login'))
    return render_template('success.html', session=session_db_obj, expiration_time=expiration_time, now=now)


@app.route('/chat')
@login_required # Requires Flask-Login session
def chat():
    """Serves the main chat page."""
    client_ip = request.remote_addr
    # Verify network access as well before rendering chat
    if not nc_is_authenticated(client_ip):
        flash('Your network session seems to have ended. Please log in again.', 'warning')
        # Attempt to re-grant based on active Flask session if possible
        session_db_obj = Session.query.filter_by(user_id=current_user.id, ip_address=client_ip, active=True).order_by(Session.start_time.desc()).first()
        if session_db_obj:
            if not add_to_allowed_list(client_ip, session_db_obj.id, duration=app.config.get('SESSION_TIMEOUT', timedelta(hours=2))):
                logout_user(); return redirect(url_for('login')) # Failed to re-grant
            logging.info(f"Re-granted network access for {client_ip} on chat page access.")
        else:
            logout_user(); return redirect(url_for('login')) # No active session found

    return render_template('chat.html') # Renders the chat interface


@app.route('/logout')
@login_required
def logout():
    # Logs out both Flask-Login and network controller access
    client_ip = request.remote_addr; user_id = current_user.id; username = current_user.username
    logging.info(f"Logout requested by user {username} from IP {client_ip}")

    # Disconnect user from SocketIO if actively connected
    # Find the SID associated with the username (requires better tracking than simple dict)
    # Example (if using active_users_info correctly):
    user_info = active_users_info.get(username)
    if user_info and user_info.get('sid'):
         logging.info(f"Disconnecting user {username} from SocketIO (SID: {user_info['sid']}) on logout.")
         socketio.disconnect(user_info['sid'])
         # Clean up tracking (disconnect handler should also do this, but belt-and-suspenders)
         room_left = user_info.get('room')
         if username in active_users_info: del active_users_info[username]
         if room_left and room_left in rooms_data and username in rooms_data[room_left]:
             rooms_data[room_left].remove(username)
             if not rooms_data[room_left]: del rooms_data[room_left]
             # Optionally emit status update about logout from here if needed

    # Terminate DB sessions
    sessions_to_terminate = Session.query.filter_by( user_id=user_id, ip_address=client_ip, active=True ).all()
    if sessions_to_terminate:
        for session_db_obj in sessions_to_terminate:
            session_db_obj.end_time = datetime.utcnow(); session_db_obj.active = False; logging.info(f"Marked DB session {session_db_obj.id} as inactive.")
        db.session.commit()
        if remove_from_allowed_list(client_ip): logging.info(f"Removed {client_ip} from network allowed list.")
        else: logging.warning(f"Attempted to remove {client_ip} on logout, but it wasn't found in the allowed list.")
    else:
        logging.warning(f"Logout request from {username} at {client_ip}, but no active DB session found.")
        remove_from_allowed_list(client_ip) # Still try to remove

    logout_user() # Logout Flask-Login session
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))


# --- SOCKET.IO EVENT HANDLERS (Updated for Rooms & History) ---

MAX_HISTORY = 50 # How many messages to load initially per room
# In-memory tracking (lost on restart)
active_users_info = {} # {username: {'sid': sid, 'room': current_room}}
rooms_data = {} # {room_name: set(usernames)}

def get_users_in_room(room):
    return list(rooms_data.get(room, set()))

@socketio.on('connect')
def handle_connect():
    # Connection is allowed, but user needs Flask-Login session to proceed
    if not current_user.is_authenticated:
        logging.warning(f"Unauthenticated SocketIO connection attempt REFUSED (SID: {request.sid}).")
        return False # Reject the connection explicitly

    sid = request.sid
    username = current_user.username
    logging.info(f'SocketIO Client connected: {username} (SID: {sid}) - Waiting for room join.')
    # Store SID associated with username temporarily, but don't join room yet
    # If user was already tracked (e.g., reconnect), update SID
    active_users_info[username] = {'sid': sid, 'room': None}
    print(f"User connected: {username}, SID: {sid}. Current users: {list(active_users_info.keys())}")


@socketio.on('disconnect')
def handle_disconnect():
    sid = request.sid
    username_to_remove = None
    room_left = None
    for uname, info in list(active_users_info.items()):
        if info.get('sid') == sid:
            username_to_remove = uname
            room_left = info.get('room')
            del active_users_info[uname]
            break # Found user, no need to continue loop

    if username_to_remove:
        logging.info(f'SocketIO Client disconnected: {username_to_remove} (SID: {sid})')
        if room_left:
            if room_left in rooms_data and username_to_remove in rooms_data[room_left]:
                 rooms_data[room_left].remove(username_to_remove)
                 if not rooms_data[room_left]: del rooms_data[room_left] # Clean up empty room tracking
            users_in_room = get_users_in_room(room_left)
            emit('user_update', {'users': users_in_room, 'room': room_left}, room=room_left)
            emit('status', {'msg': f'{username_to_remove} has left the chat.'}, room=room_left)
            print(f"User left {room_left}: {username_to_remove}. Users remaining: {users_in_room}")
        else: print(f"User disconnected without joining a room: {username_to_remove}")
    else: logging.warning(f"Disconnect received for unknown SID: {sid}")
    print(f"Current users after disconnect: {list(active_users_info.keys())}")


@socketio.on('join')
def handle_join(data):
    if not current_user.is_authenticated: return False
    username = current_user.username
    sid = request.sid
    new_room = data.get('room', 'general').strip()
    if not new_room : new_room = 'general'
    new_room = new_room[:64] # Limit room name length

    # Ensure user info exists and SID matches current connection
    if username not in active_users_info or active_users_info[username].get('sid') != sid:
        logging.warning(f"Join attempt from unrecognized user/SID: {username}/{sid}. Re-associating.")
        active_users_info[username] = {'sid': sid, 'room': None} # Reset/Associate

    previous_room = active_users_info[username].get('room')

    # --- Leave previous room logic ---
    if previous_room and previous_room != new_room:
        try:
            leave_room(previous_room, sid=sid) # Explicitly leave using SID
            if previous_room in rooms_data and username in rooms_data[previous_room]:
                rooms_data[previous_room].remove(username)
                if not rooms_data[previous_room]: del rooms_data[previous_room]
            logging.info(f'{username} left room: {previous_room}')
            prev_room_users = get_users_in_room(previous_room)
            emit('status', {'msg': f'{username} has left the room.'}, room=previous_room)
            emit('user_update', {'users': prev_room_users, 'room': previous_room}, room=previous_room)
            print(f"User left {previous_room}: {username}. Users remaining: {prev_room_users}")
        except Exception as e:
             logging.error(f"Error during leave_room for {username} from {previous_room}: {e}")

    # --- Join new room logic ---
    try:
        join_room(new_room, sid=sid) # Explicitly join using SID
        active_users_info[username]['room'] = new_room
        if new_room not in rooms_data: rooms_data[new_room] = set()
        rooms_data[new_room].add(username)
        logging.info(f'{username} joined room: {new_room}')
        current_room_users = get_users_in_room(new_room)
        print(f"User joined {new_room}: {username}. Users now: {current_room_users}")
    except Exception as e:
         logging.error(f"Error during join_room for {username} to {new_room}: {e}")
         # Maybe disconnect user or send error message?
         return # Stop processing if join fails

    # --- Load and Emit Message History ---
    try:
        messages = Message.query.filter_by(room=new_room)\
                                .order_by(Message.timestamp.desc())\
                                .limit(MAX_HISTORY)\
                                .all()
        messages.reverse() # Show oldest first
        history = [msg.to_dict() for msg in messages]
    except Exception as e:
        logging.error(f"Error fetching message history for room {new_room}: {e}")
        history = []
    # Emit history *only* to the joining user (using their SID)
    emit('message_history', {'room': new_room, 'history': history}, room=sid)
    print(f"Sent {len(history)} history messages for {new_room} to {username} (SID: {sid})")

    # --- Notify room and send updated user list ---
    emit('status', {'msg': f'{username} has entered the room.'}, room=new_room, skip_sid=sid)
    emit('user_update', {'users': current_room_users, 'room': new_room}, room=new_room)


@socketio.on('chat_message')
def handle_chat_message(data):
    if not current_user.is_authenticated: return
    username = current_user.username
    sid = request.sid
    message_body = data.get('msg', '').strip()
    room = data.get('room', '').strip()
    if not room : # Message must be associated with a room
         logging.warning(f"Message attempt from {username} without specifying room.")
         return

    # Verify user is tracked and in the specified room
    user_info = active_users_info.get(username)
    if not user_info or user_info.get('sid') != sid or user_info.get('room') != room:
         logging.warning(f"Message rejected: User {username} (SID:{sid}) mismatch or not in room {room}. UserInfo: {user_info}")
         # Optionally send error back to client: emit('error', {'msg': 'Mismatch sending message'}, room=sid)
         return

    if not message_body: return # Ignore empty

    # Save message to Database
    try:
        new_msg = Message(body=message_body, user_id=current_user.id, room=room)
        db.session.add(new_msg)
        db.session.commit()
        logging.info(f'Message saved: Room {room}, User {username}, Msg: {message_body[:50]}...')
        emit_data = new_msg.to_dict() # Use DB data (includes timestamp, ID)
    except Exception as e:
        db.session.rollback(); logging.error(f"Error saving message for room {room}, user {username}: {e}"); return

    # Broadcast message to room
    emit('chat_message', emit_data, room=room)
    print(f"Broadcast message in {room} from {username}")


# --- RUN THE APP ---
if __name__ == '__main__':
    host = '0.0.0.0'
    port = 5000
    logging.info(f"Starting Flask-SocketIO server ({ASYNC_MODE or 'default'}) on http://{host}:{port}...")
    # Set use_reloader=False if using gevent's WSGI server directly
    # debug=True enables debugger and usually the reloader with socketio.run
    socketio.run(app, host=host, port=port, debug=True)

 #Type this link in browser for output
    #http://127.0.0.1:5000
#http://localhost:5000
