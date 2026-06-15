"""可執行參考服務（Flask）：把 api_service 包成 HTTP 端點。生產可換 FastAPI / 加認證。
啟動：python app.py（或 flask run）。維持 graceful degrade，缺模型回 503。"""
import io, os, sys
import numpy as np
from flask import Flask, request, jsonify
from PIL import Image
HERE = os.path.dirname(os.path.abspath(__file__)); P0 = os.path.join(HERE, "..", "phase0")
sys.path.insert(0, P0); sys.path.insert(0, HERE)
from model_registry import ModelRegistry
from feature_flags import FeatureFlags
import api_service as api
def create_app():
    app = Flask(__name__)
    reg = ModelRegistry(os.path.join(P0, "model_registry.json"))
    flags = FeatureFlags(os.path.join(P0, "feature_flags.json"))
    @app.post("/segment")
    def segment():
        f = request.files.get("image")
        if f is None: return jsonify({"status": "unavailable", "error": "image required"}), 400
        img = np.asarray(Image.open(io.BytesIO(f.read())).convert("RGB"))
        out, code = api.handle_segment(img, flags, reg,
                                       model_id=request.form.get("model_id"),
                                       image_id=request.form.get("image_id"))
        return jsonify(out), code
    @app.post("/annotations")
    def annotations():
        out, code = api.handle_annotations(request.get_json(force=True))
        return jsonify(out), code
    @app.get("/annotation-tasks")
    def tasks():
        out, code = api.handle_annotation_tasks()
        return jsonify(out), code
    return app
if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=int(os.environ.get("PORT", 8000)))
