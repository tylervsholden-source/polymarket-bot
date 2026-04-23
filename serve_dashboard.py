"""
Minimal Dashboard Server
Bu dosya sadece web_premium/index.html dosyasını sunar ve /api/status üzerinden 
ajan kararlarını JSON olarak front-end'e iletir.
(Ek bir dependency gerektirmez, built-in kütüphaneler kullanır)
"""

import os
import json
import logging
from http.server import HTTPServer, SimpleHTTPRequestHandler

PORT = 8080
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
WEB_DIR = os.path.join(BASE_DIR, "web_premium")
DATA_FILE = os.path.join(BASE_DIR, "data", "status.json")

class DashboardHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        # API Route: Serve status.json directly
        if self.path == '/api/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            
            try:
                if os.path.exists(DATA_FILE):
                    with open(DATA_FILE, 'rb') as f:
                        self.wfile.write(f.read())
                else:
                    self.wfile.write(b'{"decisions": [], "cycle": 0}')
            except Exception as e:
                logging.error(f"Error reading status.json: {e}")
                self.wfile.write(b'{"decisions": [], "cycle": 0}')
            return
            
        # Default route serves index.html (handled by SimpleHTTPRequestHandler)
        return super().do_GET()

    # Squelch normal logging to not spam the terminal
    def log_message(self, format, *args):
        pass

if __name__ == '__main__':
    # Ensure web_premium directory exists
    os.makedirs(WEB_DIR, exist_ok=True)
    
    server_address = ('', PORT)
    httpd = HTTPServer(server_address, DashboardHandler)
    
    print("=" * 60)
    print("✨ PREMIUM AGENT DASHBOARD HAZIR ✨")
    print("=" * 60)
    print(f"Lütfen tarayıcınızda şu adresi açın: http://localhost:{PORT}")
    print("Kapatmak için Ctrl+C yapabilirsiniz.")
    print("=" * 60)
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard kapatıldı.")
