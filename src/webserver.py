"""Simple web server for VRM model viewer."""
import json
import mimetypes
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import unquote

from config import config
from storage import MetadataStore


class VRMViewerHandler(SimpleHTTPRequestHandler):
    """HTTP handler for VRM viewer with API endpoints."""
    
    def __init__(self, *args, **kwargs):
        self.web_dir = Path(__file__).parent.parent / "web"
        self.data_dir = config.data_dir
        super().__init__(*args, directory=str(self.web_dir), **kwargs)
    
    def do_GET(self):
        """Handle GET requests."""
        path = unquote(self.path)
        
        # API endpoints
        if path == "/api/models":
            self.send_models_json()
            return
        
        if path == "/api/count":
            self.send_model_count()
            return
        
        # Serve model files
        if path.startswith("/models/"):
            self.serve_model_file(path[8:])  # Remove "/models/" prefix
            return
        
        # Serve thumbnails
        if path.startswith("/thumbnails/"):
            self.serve_thumbnail(path[12:])  # Remove "/thumbnails/" prefix
            return
        
        # Default: serve static files from web directory
        super().do_GET()
    
    def send_models_json(self):
        """Send list of models as JSON."""
        try:
            store = MetadataStore(config.db_path)
            records = store.list_all()
            store.close()
            
            # Convert to JSON-serializable format
            models = []
            for r in records:
                # Get relative path for file
                file_path = Path(r.file_path)
                
                # Get relative thumbnail path
                thumb_path = None
                if r.thumbnail_path:
                    thumb_path = Path(r.thumbnail_path).name
                    if r.source:
                        thumb_path = f"{r.source}/{thumb_path}"
                
                models.append({
                    "id": r.id,
                    "name": r.name,
                    "artist": r.artist,
                    "source": r.source,
                    "source_url": r.source_url,
                    "file_path": file_path.name,
                    "file_type": r.file_type,
                    "size_bytes": r.size_bytes,
                    "thumbnail_path": thumb_path,
                    "license": r.license,
                    "acquired_at": r.acquired_at,
                })
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(json.dumps(models).encode())
            
        except Exception as e:
            self.send_error(500, str(e))
    
    def send_model_count(self):
        """Send current model count for polling."""
        try:
            store = MetadataStore(config.db_path)
            records = store.list_all()
            store.close()
            
            # Count all models
            model_count = len(records)
            
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(json.dumps({"count": model_count}).encode())
            
        except Exception as e:
            self.send_error(500, str(e))
    
    def serve_model_file(self, filename: str):
        """Serve a VRM model file."""
        # Search in raw directories for the file
        for source_dir in self.data_dir.glob("raw/*"):
            file_path = source_dir / filename
            if file_path.exists():
                self.serve_file(file_path, "model/gltf-binary")
                return
        
        # Also check extracted directory
        for extracted_dir in self.data_dir.glob("extracted/*"):
            file_path = extracted_dir / filename
            if file_path.exists():
                self.serve_file(file_path, "model/gltf-binary")
                return
        
        self.send_error(404, f"Model not found: {filename}")
    
    def serve_thumbnail(self, path: str):
        """Serve a thumbnail image."""
        thumb_path = self.data_dir / "thumbnails" / path
        
        if thumb_path.exists():
            content_type, _ = mimetypes.guess_type(str(thumb_path))
            self.serve_file(thumb_path, content_type or "image/png")
        else:
            self.send_error(404, f"Thumbnail not found: {path}")
    
    def serve_file(self, file_path: Path, content_type: str):
        """Serve a file with proper headers."""
        try:
            content = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(content))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, str(e))
    
    def log_message(self, format, *args):
        """Custom log format."""
        print(f"[WebServer] {args[0]}")


def run_server(host: str = "localhost", port: int = 8080):
    """Run the web server."""
    server = HTTPServer((host, port), VRMViewerHandler)
    print(f"🎭 VRM Viewer running at http://{host}:{port}")
    print("Press Ctrl+C to stop")
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.shutdown()


if __name__ == "__main__":
    run_server()
