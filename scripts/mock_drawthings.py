"""A fake Draw Things API server for testing the pipeline without the app.

Run:  python scripts/mock_drawthings.py   (listens on http://127.0.0.1:7860)
Returns simple placeholder images: colored for cover prompts, black-and-white line art otherwise.
"""
import base64
import io
import json
import random
from http.server import BaseHTTPRequestHandler, HTTPServer

from PIL import Image, ImageDraw


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({"model": "mock_model.ckpt", "steps": 4}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        w, h = body["width"], body["height"]
        colored = "cover illustration" in body["prompt"]
        img = Image.new("RGB", (w, h), (120, 190, 240) if colored else "white")
        d = ImageDraw.Draw(img)
        for _ in range(8):
            x, y, r = random.randint(120, w - 120), random.randint(120, h - 120), random.randint(30, 90)
            d.ellipse([x - r, y - r, x + r, y + r], outline="black", width=6,
                      fill=(250, 120, 150) if colored else None)
        buf = io.BytesIO()
        img.save(buf, "PNG")
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps(
            {"images": ["data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()]}).encode())


if __name__ == "__main__":
    print("Mock Draw Things listening on http://127.0.0.1:7860 (Ctrl+C to stop)")
    HTTPServer(("127.0.0.1", 7860), Handler).serve_forever()
