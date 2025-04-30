import os
from datetime import timedelta

basedir = os.path.abspath(os.path.dirname(__file__))

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'a-very-secret-dev-key-change-me'
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
        'sqlite:///' + os.path.join(basedir, 'portal.db')
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Authentication settings
    SESSION_TIMEOUT = timedelta(hours=2)  # Re-authenticate after 2 hours

    # Network settings (Crucial for captive portal logic)
    # Define the network ranges this portal manages
    ALLOWED_NETWORKS = ['192.168.1.0/24'] # Example, adjust to your network
    # IPs within the ALLOWED_NETWORKS that bypass the portal (e.g., gateway, DNS)
    EXCLUDED_IPS = ['192.168.1.1'] # Example, adjust as needed
    PORTAL_IP = '192.168.1.100' # Example: The IP address of the server running this Flask app

    # Admin settings
    ADMIN_USERNAME = os.environ.get('ADMIN_USER') or 'admin'
    # IMPORTANT: Use environment variables or a secrets manager in production!
    ADMIN_PASSWORD = os.environ.get('ADMIN_PASS') or 'password123'