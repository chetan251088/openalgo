# Simple Flask route to serve the scalping interface
from flask import Blueprint, send_from_directory, jsonify
import os

scalping_bp = Blueprint('scalping', __name__)

@scalping_bp.route('/scalping')
def scalping_interface():
    """Serve the scalping interface HTML"""
    return send_from_directory('.', 'scalping_interface.html')

@scalping_bp.route('/chart_window.html')
def chart_window():
    """Serve the chart window"""
    return send_from_directory('.', 'chart_window.html')

@scalping_bp.route('/auto_trading_window.html')
def auto_trading_window():
    """Serve the auto trading window"""
    return send_from_directory('.', 'auto_trading_window.html')

@scalping_bp.route('/scalping/config')
def scalping_config():
    """Return runtime config (WebSocket URL) for the scalping interface"""
    return jsonify({
        'wsUrl': os.getenv('WEBSOCKET_URL', 'ws://127.0.0.1:8765')
    })

# Register this blueprint in app.py
