#!/usr/bin/env python3
"""Simple server for the StrokeGuard dashboard."""

import http.server
import socketserver
import webbrowser
import os

PORT = 8080

os.chdir(os.path.dirname(os.path.abspath(__file__)))

Handler = http.server.SimpleHTTPRequestHandler
Handler.extensions_map.update({
    '.js': 'application/javascript',
    '.json': 'application/json',
})

print(f"Starting StrokeGuard AI Dashboard at http://localhost:{PORT}")
webbrowser.open(f"http://localhost:{PORT}/app.html")

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    httpd.serve_forever()
