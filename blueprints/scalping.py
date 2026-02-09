# Simple Flask route to serve the scalping interface
from flask import Blueprint, send_from_directory, jsonify
import os
from pathlib import Path

scalping_bp = Blueprint('scalping', __name__)
BASE_DIR = Path(__file__).resolve().parent.parent

@scalping_bp.route('/scalping-legacy', strict_slashes=False)
@scalping_bp.route('/scalping-old', strict_slashes=False)
@scalping_bp.route('/scalping-classic', strict_slashes=False)
@scalping_bp.route('/legacy/scalping', strict_slashes=False)
def scalping_interface():
    """Serve the legacy scalping interface HTML."""
    return send_from_directory(BASE_DIR, 'scalping_interface.html')

@scalping_bp.route('/chart_window.html', strict_slashes=False)
def chart_window():
    """Serve the chart window"""
    return send_from_directory(BASE_DIR, 'chart_window.html')

@scalping_bp.route('/auto_trading_window.html', strict_slashes=False)
def auto_trading_window():
    """Serve the auto trading window"""
    return send_from_directory(BASE_DIR, 'auto_trading_window.html')

@scalping_bp.route('/scalping/config', strict_slashes=False)
def scalping_config():
    """Return runtime config (WebSocket URL) for the scalping interface"""
    return jsonify({
        'wsUrl': os.getenv('WEBSOCKET_URL', 'ws://127.0.0.1:8765')
    })

# Register this blueprint in app.py
