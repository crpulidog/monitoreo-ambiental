import gzip
import os
import uuid
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request, status, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from server.db import CentralDatabase
from server.thingspeak_importer import parsear_thingspeak_json
from etl.etl_hourly import EnvironmentalETL

import requests

app = FastAPI(
    title="Sistema de Monitoreo Ambiental – API de Ingestión",
    version="1.0.0",
)

db = CentralDatabase()
TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


# ---------------------------------------------------------------------------
# Modelos
# ---------------------------------------------------------------------------

class LecturaItem(BaseModel):
    id: str = Field(..., description="UUID único generado en el nodo edge")
    nodo_id: str
    sensor: str
    valor: float
    es_alerta: Optional[bool] = False
    ts: str


# ---------------------------------------------------------------------------
# Middleware: descompresión GZIP transparente
# ---------------------------------------------------------------------------

@app.middleware("http")
async def gzip_middleware(request: Request, call_next):
    if request.headers.get("content-encoding") == "gzip":
        body = await request.body()
        try:
            decompressed = gzip.decompress(body)
            request._body = decompressed

            async def receive():
                return {"type": "http.request", "body": decompressed, "more_body": False}

            request._receive = receive
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Error al descomprimir payload: {exc}",
            )
    return await call_next(request)


# ---------------------------------------------------------------------------
# Rutas de salud y métricas
# ---------------------------------------------------------------------------

@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.get("/api/v1/metrics")
def obtener_metricas():
    return {
        "total_lecturas": db.contar_lecturas(),
        "resumen_por_sensor": db.obtener_resumen_sensores(),
    }


# ---------------------------------------------------------------------------
# Rutas de ingesta
# ---------------------------------------------------------------------------

@app.post("/api/v1/sync", status_code=status.HTTP_200_OK)
async def recibir_lote(lecturas: List[LecturaItem]):
    """Ingesta idempotente de lecturas desde nodos edge (Store-and-Forward)."""
    if not lecturas:
        return {"status": "empty", "ingestados": 0}
    datos = [item.model_dump() for item in lecturas]
    db.insertar_lote_idempotente(datos)
    return {
        "status": "success",
        "recibidos": len(lecturas),
        "total_en_servidor": db.contar_lecturas(),
    }


@app.post("/api/v1/import/thingspeak")
async def importar_thingspeak(request: Request):
    """Recibe, parsea e ingesta un JSON exportado de ThingSpeak."""
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"JSON inválido: {exc}")

    try:
        lecturas, meta = parsear_thingspeak_json(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not lecturas:
        return {
            "status": "empty",
            "mensaje": "No se encontraron lecturas numéricas en los feeds.",
            "meta": meta,
        }

    db.insertar_lote_idempotente(lecturas)

    try:
        EnvironmentalETL().ejecutar_pipeline()
    except Exception:
        pass

    alertas = [l for l in lecturas if l["es_alerta"]]
    return {
        "status": "success",
        "mensaje": f"Se procesaron {meta['total_feeds']} feeds de ThingSpeak.",
        "canal": meta["channel_name"],
        "lecturas_generadas": len(lecturas),
        "alertas_detectadas": len(alertas),
        "total_en_servidor": db.contar_lecturas(),
        "meta": meta,
    }


# ---------------------------------------------------------------------------
# Rutas de visualización (KPIs, series, alertas)
# ---------------------------------------------------------------------------

@app.get("/api/v1/kpis")
def obtener_kpis():
    return db.obtener_kpis()

@app.get("/api/v1/timeseries")
def obtener_timeseries(limite: int = 1500, fecha: str = None):
    return db.obtener_series_temporales(limite=limite, fecha=fecha)

@app.get("/api/v1/alerts")
def obtener_alertas(limite: int = 15):
    return db.obtener_alertas_recientes(limite=limite)


# ---------------------------------------------------------------------------
# Simulación de telemetría (entorno de desarrollo / demo)
# ---------------------------------------------------------------------------

@app.post("/api/v1/simulate")
def simular_telemetria(con_alerta: bool = False, cantidad: int = 5):
    """Genera lecturas de demostración sin hardware físico conectado."""
    sensores = ["temperatura", "humedad", "pm25", "co2"]
    rangos = {
        "temperatura": ((24.0, 34.0), (46.0, 53.0)),
        "humedad":     ((70.0, 95.0), (70.0, 95.0)),
        "pm25":        ((8.0, 25.0),  (110.0, 220.0)),
        "co2":         ((410.0, 540.0), (410.0, 540.0)),
    }
    nuevas = []
    for i in range(cantidad):
        s = random.choice(sensores)
        es_critico = con_alerta and i == 0
        rango = rangos[s][1 if es_critico else 0]
        nuevas.append(
            {
                "id": str(uuid.uuid4()),
                "nodo_id": "nodo-demo-01",
                "sensor": s,
                "valor": round(random.uniform(*rango), 2),
                "es_alerta": es_critico,
                "ts": datetime.now(timezone.utc).isoformat(),
            }
        )
    db.insertar_lote_idempotente(nuevas)
    return {"status": "success", "generadas": len(nuevas), "total": db.contar_lecturas()}

# ---- Actualizar del canal automáticamente ------
@app.get("/api/v1/sync/thingspeak")
def sincronizar_thingspeak(
    channel_id: str = Query(..., description="ID del canal de ThingSpeak"),
    api_key: str = Query(None, description="Read API Key (si el canal es privado)")
):
    """
    Obtiene los últimos datos directamente de la API de ThingSpeak,
    los parsea y los inserta en la base de datos de forma idempotente.
    """
    url = f"https://api.thingspeak.com/channels/{channel_id}/feeds.json"
    params = {}
    if api_key:
        params["api_key"] = api_key

    try:
        # Usamos requests, que ya está en tu requirements.txt
        response = requests.get(url, params=params, timeout=15)
        response.raise_for_status() 
        raw_data = response.json()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error al conectar con ThingSpeak: {exc}")

    try:
        lecturas, meta = parsear_thingspeak_json(raw_data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Error en el formato de datos: {exc}")

    if not lecturas:
        return {"status": "empty", "mensaje": "ThingSpeak no devolvió lecturas numéricas nuevas."}

    db.insertar_lote_idempotente(lecturas)
    
    return {
        "status": "success",
        "mensaje": f"Sincronizados {len(lecturas)} registros desde ThingSpeak.",
        "lecturas_importadas": len(lecturas),
        "total_en_servidor": db.contar_lecturas()
    }

# ---- Petición por fechas ------
@app.get("/api/v1/fechas")
def obtener_fechas():
    return db.obtener_fechas_disponibles()

# ---------------------------------------------------------------------------
# Tablero web
# ---------------------------------------------------------------------------

@app.get("/", response_class=RedirectResponse)
def raiz():
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
def servir_dashboard():
    html_file = TEMPLATES_DIR / "dashboard.html"
    if not html_file.exists():
        raise HTTPException(status_code=404, detail="Plantilla no encontrada.")
    return HTMLResponse(content=html_file.read_text(encoding="utf-8"))
