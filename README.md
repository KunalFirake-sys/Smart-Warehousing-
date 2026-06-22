# Smart Warehouse Digital Twin

A real-time warehouse digital twin integrating computer vision, IoT sensing, and a live dashboard.

## Tech Stack

- Python
- OpenCV
- YOLOv8
- FastAPI
- WebSockets
- SQLite
- ESP32

## Features

- Real-time inventory monitoring
- YOLOv8 object detection
- ESP32 sensor integration
- Sensor fusion for anomaly detection
- Live dashboard updates
- Automated alerts and logging

## Performance

- mAP@50: 94%
- F1 Score: 0.99
- Precision: 1.00
- Latency: < 3 seconds

## Project Structure

```text
backend/
templates/
run.py
alerts.py
logger.py
best.pt
```
Steps to Run:
1.Open Warehouse folder on the desktop in vs code

2.edit warehouse/backend/config.py — set MODEL_PATH(copy the path to the best.pt present in warehouse folder) and CAMERA_INDEX(set to 0 if no default cam otherwise set to 1)

3. Open Dashboard
Open index.html in present in warehouse folder 

4.run this command to install all prerequisites(if not on the clg pc)(clg pc is preconfigured)
pip install -r requirements.txt

if it doesnt work install these packages manually
"fastapi
uvicorn[standard]
ultralytics
opencv-python
python-multipart
openpyxl
pandas
numpy"

5. Run Detection System
Open a new terminal and run:

.\python\python.exe run.py (for clg PC) 

execute run.py normally (for any other laptop)
## Author

Kunal Firake
