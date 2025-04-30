import subprocess
import logging
from datetime import datetime, timedelta
# Need to import Config within functions or pass duration explicitly to avoid circular dependency if Config imports network_controller
# from config import Config # Potential circular import if Config imports this

# --- Placeholder Network Control ---
# WARNING: This is a simplified in-memory dictionary.
# It does NOT persist across application restarts and does NOT actually control network access.
# A real implementation needs to interact with firewall rules (iptables, nftables, router API, etc.).
authenticated_ips = {}  # Format: {ip_address: (session_id, expiration_time)}

def is_authenticated(ip_address):
    """Check if an IP address is in our placeholder list and not expired."""
    global authenticated_ips
    now = datetime.utcnow()

    if ip_address in authenticated_ips:
        session_id, expiration_time = authenticated_ips[ip_address]
        if now < expiration_time:
            logging.debug(f"IP {ip_address} found in allowed list, session {session_id}, expires {expiration_time}.")
            return True
        else:
            # Expired, remove from the placeholder list
            logging.info(f"IP {ip_address} found but expired at {expiration_time}. Removing.")
            # Call the actual removal function to potentially clean up firewall rules too
            remove_from_allowed_list(ip_address) # This will pop it from the dict
            return False # Expired

    logging.debug(f"IP {ip_address} not found in allowed list.")
    return False

def add_to_allowed_list(ip_address, session_id, duration):
    """Add an IP address to the placeholder allowed list."""
    global authenticated_ips
    # duration should be a timedelta object passed from app.py (using Config.SESSION_TIMEOUT)

    expiration_time = datetime.utcnow() + duration
    authenticated_ips[ip_address] = (session_id, expiration_time)

    # --- !!! REAL IMPLEMENTATION NEEDED HERE !!! ---
    # Example using iptables (requires root/sudo privileges & careful rule management)
    # Ensure rules target the correct interface and chain (e.g., FORWARD, or a custom chain)
    # Make sure to handle duplicate rules and cleanup.
    try:
        # Example: Insert rule to allow traffic FROM this IP in the FORWARD chain
        # This assumes the portal server acts as a gateway or router. Adjust accordingly.
        # command_add = f"sudo iptables -I FORWARD 1 -s {ip_address} -j ACCEPT"
        # logging.info(f"Executing: {command_add}")
        # Note: Running subprocess with sudo from a web app is highly insecure.
        # A better approach involves a separate privileged daemon controlled via IPC/RPC,
        # or using specific firewall management libraries/APIs.
        # subprocess.run(command_add, shell=True, check=True, capture_output=True)
        logging.info(f"(Placeholder) Added {ip_address} to allowed list (Session: {session_id}) until {expiration_time}. Real firewall rule needed.")
        # --- End of Real Implementation Area ---
        return True
    except Exception as e:
        logging.error(f"Failed to add firewall rule for {ip_address}: {e}")
        # If firewall rule fails, remove from our placeholder dict too
        if ip_address in authenticated_ips:
            authenticated_ips.pop(ip_address)
        return False

def remove_from_allowed_list(ip_address):
    """Remove an IP address from the placeholder allowed list."""
    global authenticated_ips
    removed_from_dict = False
    if ip_address in authenticated_ips:
        session_id, _ = authenticated_ips.pop(ip_address)
        removed_from_dict = True
        logging.info(f"Removed {ip_address} (Session: {session_id}) from internal list.")
    else:
        logging.debug(f"Attempted to remove {ip_address}, but it was not in the internal list.")

    # --- !!! REAL IMPLEMENTATION NEEDED HERE !!! ---
    # Example using iptables (requires corresponding rule removal)
    try:
        # command_del = f"sudo iptables -D FORWARD -s {ip_address} -j ACCEPT"
        # logging.info(f"Executing: {command_del}")
        # subprocess.run(command_del, shell=True, check=False, capture_output=True) # Use check=False as rule might not exist
        logging.info(f"(Placeholder) Removed {ip_address} from allowed list. Real firewall rule removal needed.")
        # --- End of Real Implementation Area ---
        return removed_from_dict # Return True if it was in the dictionary initially
    except Exception as e:
        logging.error(f"Failed to remove firewall rule for {ip_address}: {e}")
        # Even if firewall fails, we keep it removed from the dict
        return removed_from_dict

def get_mac_address(ip_address):
    """Attempt to get MAC address from local ARP table for a given IP."""
    # This is OS-dependent and relies on the server having ARP entries for clients.
    # May not work reliably, especially across different subnets or with network segmentation.
    if ip_address == '127.0.0.1' or ip_address == '::1':
        return None # Loopback doesn't have a MAC in the usual sense

    try:
        # Command for Linux/macOS using 'arp -n'
        # Use 'arp -a' on Windows, parsing might differ
        pid = subprocess.Popen(['arp', '-n', ip_address], stdout=subprocess.PIPE)
        s = pid.communicate()[0].decode()
        # Example arp -n output:
        # Address                  HWtype  HWaddress           Flags Mask            Iface
        # 192.168.1.10             ether   00:11:22:33:44:55   C                     eth0
        lines = s.splitlines()
        if len(lines) < 2: return None # Header + No entry found
        # Find the line containing the IP address
        for line in lines[1:]: # Skip header
            parts = line.split()
            if len(parts) >= 3 and parts[0] == ip_address:
                 mac = parts[2]
                 # Basic validation for MAC format
                 if len(mac) == 17 and mac.count(':') == 5:
                     # Filter out incomplete ARP entries like '(incomplete)'
                     if 'incomplete' not in mac.lower():
                        logging.debug(f"Found MAC {mac} for IP {ip_address}")
                        return mac
        logging.warning(f"Could not find valid MAC address for {ip_address} in ARP table.")
        return None
    except FileNotFoundError:
        logging.error("ARP command not found. Cannot retrieve MAC address.")
        return None
    except Exception as e:
        logging.error(f"Error getting MAC address for {ip_address}: {e}")
        return None