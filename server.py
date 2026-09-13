import http.server
import socketserver
import os

PORT = 8080
DIR = os.path.dirname(os.path.abspath(__file__))

os.chdir(DIR)
Handler = http.server.SimpleHTTPRequestHandler

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Server running at http://localhost:{PORT}")
    print(f"Serving directory: {DIR}")
    httpd.serve_forever()
