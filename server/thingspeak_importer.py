import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

# Mapeo por defecto cuando el canal de ThingSpeak no etiqueta sus campos
DEFAULT_FIELD_MAP = {
    "field1": "temperatura",
    "field2": "humedad",
    "field3": "pm25",
    "field4": "co2",
}

# Umbrales de alerta por variable ambiental
UMBRALES_ALERTA = {
    "temperatura": 45.0,
    "pm25": 100.0,
    "co2": 1000.0,
}


def _normalizar_sensor(nombre: str, fallback: str) -> str:
    n = nombre.lower()
    if "temp" in n:
        return "temperatura"
    if "hum" in n:
        return "humedad"
    if "pm2" in n or "particul" in n or "aire" in n:
        return "pm25"
    if "co2" in n or "carbon" in n:
        return "co2"
    if "lux" in n or "light" in n or "luz" in n or "ilum" in n:
        return "iluminacion"
    if "pres" in n or "baro" in n:
        return "presion"
    if "viento" in n or "wind" in n:
        return "viento"
    
    return DEFAULT_FIELD_MAP.get(fallback, fallback)


def parsear_thingspeak_json(
    raw_data: Any,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Normaliza un payload JSON de ThingSpeak a lecturas internas.

    Soporta tres formatos:
      - Respuesta completa: {"channel": {...}, "feeds": [...]}
      - Solo feeds:         {"feeds": [...]}
      - Feed individual:    {"created_at": "...", "field1": "...", ...}
      - Lista de feeds:     [{...}, ...]

    Retorna una tupla (lecturas, metadatos_del_canal).
    Los UUIDs son deterministas por canal+entry_id+sensor para garantizar
    idempotencia al re-importar el mismo JSON.
    """
    channel_info: Dict[str, Any] = {}
    feeds: list = []

    if isinstance(raw_data, dict):
        if "channel" in raw_data and "feeds" in raw_data:
            channel_info = raw_data["channel"]
            feeds = raw_data["feeds"]
        elif "feeds" in raw_data:
            feeds = raw_data["feeds"]
        elif any(k.startswith("field") for k in raw_data):
            feeds = [raw_data]
            channel_info = {"id": raw_data.get("channel_id", "ts-edge")}
        else:
            raise ValueError(
                "El JSON no tiene estructura reconocible de ThingSpeak "
                "(faltan 'channel' y 'feeds')."
            )
    elif isinstance(raw_data, list):
        feeds = raw_data
    else:
        raise ValueError(
            "Formato inválido: se esperaba un objeto JSON o lista de feeds."
        )

    channel_id = str(channel_info.get("id", "ts-channel"))
    nodo_id = channel_info.get("name") or f"nodo-ts-{channel_id}"

    # Construir mapa de campos según etiquetas del canal
    field_map: Dict[str, str] = {}
    for fkey in [f"field{i}" for i in range(1, 9)]:
        label = channel_info.get(fkey)
        field_map[fkey] = (
            _normalizar_sensor(str(label), fkey) if label else DEFAULT_FIELD_MAP.get(fkey, fkey)
        )

    lecturas: List[Dict[str, Any]] = []

    for idx, feed in enumerate(feeds):
        # Normalizar timestamp
        raw_ts = feed.get("created_at", "")
        try:
            ts = datetime.fromisoformat(
                raw_ts.replace("Z", "+00:00")
            ).isoformat()
        except (ValueError, AttributeError):
            ts = datetime.now(timezone.utc).isoformat()

        entry_id = str(feed.get("entry_id", idx))

        for fkey, sensor in field_map.items():
            raw_val = feed.get(fkey)
            if raw_val is None or raw_val == "":
                continue
            try:
                valor = float(raw_val)
            except (ValueError, TypeError):
                continue

            umbral = UMBRALES_ALERTA.get(sensor)
            es_alerta = umbral is not None and valor >= umbral

            # UUID determinista → re-importar no duplica
            uid_key = f"ts-{channel_id}-{entry_id}-{sensor}"
            lectura_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, uid_key))

            lecturas.append(
                {
                    "id": lectura_id,
                    "nodo_id": nodo_id,
                    "sensor": sensor,
                    "valor": round(valor, 2),
                    "es_alerta": es_alerta,
                    "ts": ts,
                }
            )

    return lecturas, {
        "channel_id": channel_id,
        "channel_name": nodo_id,
        "total_feeds": len(feeds),
        "total_lecturas_generadas": len(lecturas),
        "campos_mapeados": field_map,
    }
