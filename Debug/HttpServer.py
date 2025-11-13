from http.server import BaseHTTPRequestHandler, HTTPServer

HOST = ''  # Listen on all available interfaces
PORT = 8080

class RequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/data"):
            print("Received data:", self.path)
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"OK")
        elif self.path.startswith("#MON"):
            print("RX:", self.path)
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

print(f"Starting server on {PORT}...")
server = HTTPServer((HOST, PORT), RequestHandler)
server.serve_forever()
