@app.route('/')
def index():
    client_ip = request.remote_addr

    # If already authenticated, show success page
    if is_authenticated(client_ip):
        return redirect(url_for('success'))

    # Otherwise redirect to login
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        user = User.query.filter_by(username=username).first()

        if user and check_password_hash(user.password_hash, password):
            login_user(user)

            # Get client information
            client_ip = request.remote_addr
            client_mac = get_mac_address(client_ip)  # You'll need to implement this function
            client_device = request.user_agent.string

            # Create a new session
            session = Session(
                user_id=user.id,
                ip_address=client_ip,
                mac_address=client_mac,
                device_name=client_device
            )
            db.session.add(session)
            db.session.commit()

            # Allow network access
            add_to_allowed_list(client_ip, session.id)

            return redirect(url_for('success'))
        else:
            flash('Invalid username or password')

    return render_template('login.html')


@app.route('/success')
@login_required
def success():
    # Get the user's current session
    client_ip = request.remote_addr
    session = Session.query.filter_by(
        user_id=current_user.id,
        ip_address=client_ip,
        active=True
    ).order_by(Session.start_time.desc()).first()

    if not session:
        return redirect(url_for('login'))

    # Calculate remaining time
    current_time = datetime.utcnow()
    start_time = session.start_time
    elapsed = current_time - start_time
    remaining = app.config['SESSION_TIMEOUT'] - elapsed

    # Convert to minutes for display
    remaining_minutes = int(remaining.total_seconds() / 60)

    return render_template('success.html',
                           remaining_minutes=remaining_minutes,
                           session=session)


@app.route('/logout')
@login_required
def logout():
    # Get the user's current session
    client_ip = request.remote_addr
    session = Session.query.filter_by(
        user_id=current_user.id,
        ip_address=client_ip,
        active=True
    ).order_by(Session.start_time.desc()).first()

    if session:
        # Mark session as ended
        session.end_time = datetime.utcnow()
        session.active = False
        db.session.commit()

        # Remove from allowed list
        remove_from_allowed_list(client_ip)

    logout_user()
    return redirect(url_for('login'))
