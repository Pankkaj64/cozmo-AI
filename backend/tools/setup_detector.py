"""Explicitly download the chosen official Ultralytics weights; never uploads images."""
import os
from pathlib import Path

runtime = Path(__file__).resolve().parents[1] / '.runtime'
for name in ('ultralytics', 'matplotlib', 'models'):
    (runtime / name).mkdir(parents=True, exist_ok=True)
os.environ['YOLO_CONFIG_DIR'] = str(runtime / 'ultralytics')
os.environ['MPLCONFIGDIR'] = str(runtime / 'matplotlib')
from ultralytics import YOLO, settings
settings.update({'sync': False})
YOLO(str(runtime / 'models' / 'yolo26s.pt'))
YOLO(str(runtime / 'models' / 'yolo11s.pt'))
print('Local YOLO26s and YOLO11s ready. No camera images were sent.')
# Fuse text prompts once at setup. Runtime inference does not require a text encoder.
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.room_detector import ROOM_CLASSES
from ultralytics import YOLOE
# Keep the downloaded text encoder in the model cache.
os.chdir(runtime / 'models')
room = YOLOE(str(runtime / 'models' / 'yoloe-26s-seg.pt'))
room.set_classes(ROOM_CLASSES)
room.save(str(runtime / 'models' / 'library-room-yoloe26s-v2.pt'))
print('Open-vocabulary room weights ready: shelving, lamps, art, rugs, appliances and decor.')
