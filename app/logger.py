# app/logger.py
import os
import logging

LOG_PATH = os.getenv("LOG_PATH", "fxmonitor.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_PATH, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger("fxmonitor")
