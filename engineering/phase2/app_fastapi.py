"""可執行參考服務（FastAPI，production-grade）：包 api_service 之 OpenAPI 端點 + 可選 Bearer 認證。
認證 token 由環境變數 WOUNDAI_API_TOKEN 提供（未設＝開發模式不啟用）；不硬編任何祕密。
啟動：uvicorn app_fastapi:app --port 8000"""
import io, os, sys
import numpy as np
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from PIL import Image
HERE = os.path.dirname(os.path.abspath(__file__)); P0 = os.path.join(HERE, "..", "phase0")
sys.path.insert(0, P0); sys.path.insert(0, HERE)
from model_registry import ModelRegistry
from feature_flags import FeatureFlags
import api_service as api

def require_auth(authorization: Optional[str] = Header(None)):
    expected = os.environ.get("WOUNDAI_API_TOKEN")          # 未設 → 開發模式不驗
    if expected and authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="unauthorized")

class AnnotationSubmitModel(BaseModel):
    image_id: str
    edited_mask_png_b64: str
    editor_id: str
    model_id: Optional[str] = None
    px_per_mm: Optional[float] = None

def create_app() -> FastAPI:
    app = FastAPI(title="WoundAI Annotation & Segmentation API", version="0.1.0")
    reg = ModelRegistry(os.path.join(P0, "model_registry.json"))
    flags = FeatureFlags(os.path.join(P0, "feature_flags.json"))

    @app.get("/healthz")
    def healthz(): return {"status": "ok"}

    @app.post("/segment")
    async def segment(image: UploadFile = File(...), model_id: Optional[str] = Form(None),
                      image_id: Optional[str] = Form(None), _=Depends(require_auth)):
        img = np.asarray(Image.open(io.BytesIO(await image.read())).convert("RGB"))
        out, code = api.handle_segment(img, flags, reg, model_id=model_id, image_id=image_id)
        return JSONResponse(out, status_code=code)

    @app.post("/annotations")
    def annotations(sub: AnnotationSubmitModel, _=Depends(require_auth)):
        out, code = api.handle_annotations(sub.model_dump())
        return JSONResponse(out, status_code=code)

    @app.get("/annotation-tasks")
    def tasks(_=Depends(require_auth)):
        out, code = api.handle_annotation_tasks()
        return JSONResponse(out, status_code=code)
    return app
app = create_app()
