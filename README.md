# Monitoreo Ambiental con Conectividad Intermitente

Sistema de captura, sincronización y visualización de datos ambientales diseñado para operar en zonas con conectividad satelital o celular intermitente.

---

## Arquitectura

```
Sensores físicos
      │  (I2C / UART / SPI)
      ▼
Nodo Edge (ESP32 / Raspberry Pi)
      │  Almacenamiento inmediato en SQLite local
      ▼
Cola de sincronización
      │  Prioriza alertas críticas · Comprime en GZIP · Reintenta sin pérdida
      ▼ (cuando hay red)
API REST (FastAPI)
      │  Ingestión idempotente: ON CONFLICT (id) DO NOTHING
      ▼
Base de Datos Central (PostgreSQL en producción / SQLite en desarrollo)
      │
      ├─► ETL horario  →  tabla lectura_hora
      └─► Tablero Web  →  /dashboard
```

---

## Estructura del proyecto

```
.
├── edge/
│   ├── storage.py           # Base SQLite del nodo (Store-and-Forward)
│   ├── collector.py         # Captura de sensores y escritura local
│   └── sync.py              # Sincronización por lotes con GZIP
├── server/
│   ├── db.py                # Base de datos central + cálculo de KPIs
│   ├── main.py              # API FastAPI
│   ├── thingspeak_importer.py  # Importador de JSON de ThingSpeak
│   └── templates/
│       └── dashboard.html   # Tablero web interactivo
├── etl/
│   └── etl_hourly.py        # Agregación horaria
├── dashboards/
│   └── grafana_dashboard.json  # Plantilla de Grafana
├── demo_resilience.py       # Prueba automatizada de corte y reconexión
├── thingspeak_ejemplo.json  # Ejemplo de JSON de ThingSpeak
├── Dockerfile
├── docker-compose.yml
├── Procfile                 # Despliegue en Heroku / Railway / Render
├── runtime.txt
├── requirements.txt
└── .env.example
```

---

## Instalación local

```bash
git clone <URL-del-repositorio>
cd <directorio>

pip install -r requirements.txt

# Iniciar el servidor
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000 --reload
```

Abrir en el navegador: **http://localhost:8000/dashboard**

---

## Despliegue con Docker

Solo para actualizar e instalar docker desde terminal (debian).
```bash
sudo apt remove $(dpkg --get-selections docker.io docker-compose docker-doc docker-buildx podman-docker containerd runc | cut -f1)

sudo apt update
sudo apt install --only-upgrade apt-transport-https ca-certificates curl gnupg
sudo apt upgrade apt-transport-https ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

sudo docker run hello-world

# Opcional (usar docker sin sudo)
sudo usermod -aG docker $USER
```


```bash
# Producción con PostgreSQL incluido
docker compose up --build
```

La API queda disponible en **http://localhost:8000**.

---

## Despliegue en Heroku / Railway / Render

1. Crear la aplicación en la plataforma elegida.
2. Configurar la variable de entorno `DATABASE_URL` apuntando a una instancia de PostgreSQL.
3. Conectar el repositorio o hacer `push` al remote de la plataforma.
4. El `Procfile` y `runtime.txt` configuran el servidor automáticamente.

---

## API REST – Resumen de endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| GET | `/dashboard` | Tablero web interactivo |
| GET | `/health` | Estado del servicio |
| POST | `/api/v1/sync` | Ingestión de lotes desde nodos edge (GZIP) |
| POST | `/api/v1/import/thingspeak` | Importar JSON de ThingSpeak |
| GET | `/api/v1/kpis` | KPIs ambientales e índices de resiliencia |
| GET | `/api/v1/timeseries` | Series temporales por sensor |
| GET | `/api/v1/alerts` | Alertas críticas recientes |
| POST | `/api/v1/simulate` | Generar datos de prueba (entorno de desarrollo) |

Documentación interactiva disponible en **http://localhost:8000/docs**.

---

## Importar datos de ThingSpeak

1. Exportar el canal desde ThingSpeak (formato JSON).
2. Abrir el tablero y hacer clic en **Importar ThingSpeak**.
3. Pegar el JSON o subir el archivo `.json`.
4. Presionar **Procesar y Visualizar**.

Los campos se mapean automáticamente:
- `field1` → temperatura
- `field2` → humedad
- `field3` → PM2.5
- `field4` → CO₂

Los UUIDs son deterministas por `channel_id + entry_id + sensor`, por lo que re-importar el mismo JSON no genera duplicados.

---

## Prueba de resiliencia

```bash
python demo_resilience.py
```

Simula un corte de 30 minutos, captura continua en modo offline y reconexión, verificando cero pérdidas y cero duplicados.

---

## Requisitos de hardware (nodo físico)

| Sensor | Variable | Interfaz |
|--------|----------|----------|
| BME280 / SHT31 | Temperatura y Humedad | I2C |
| PMS5003 / SDS011 | PM2.5 | UART |
| SCD30 / MH-Z19B | CO₂ | I2C / UART |

Microcontrolador compatible: ESP32 (MicroPython) o Raspberry Pi (Python 3).

---

## Variables de entorno

| Variable | Descripción | Valor por defecto |
|----------|-------------|-------------------|
| `DATABASE_URL` | Cadena de conexión a PostgreSQL | `sqlite:///servidor_central.db` |
| `EDGE_DB_PATH` | Ruta al SQLite del nodo edge | `nodo.db` |
| `PORT` | Puerto del servidor (Heroku/Render) | `8000` |
