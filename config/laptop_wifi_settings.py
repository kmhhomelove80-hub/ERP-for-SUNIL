"""Temporary private-LAN laptop trial; no production use."""
import os
from ipaddress import ip_address, ip_network
if os.environ.get('SUNIL_LAPTOP_WIFI_REAL') == '1':
    from .laptop_real_settings import *  # noqa: F403
else:
    from .laptop_settings import *  # noqa: F403

wifi_ip = os.environ.get('SUNIL_LAPTOP_WIFI_IP', '')
address = ip_address(wifi_ip)
if not any(address in ip_network(network) for network in ['10.0.0.0/8','172.16.0.0/12','192.168.0.0/16']):
    raise RuntimeError('A private IPv4 address is required for the Wi-Fi trial.')
ALLOWED_HOSTS = [wifi_ip]
DEBUG = False  # Do not expose debug tracebacks to other Wi-Fi clients.
