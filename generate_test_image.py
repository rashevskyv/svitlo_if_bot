from datetime import datetime
from services.image_generator import generate_schedule_image
import os

def generate_test_image():
    # Scenario:
    # Today: Outage 02:00–05:30
    # Tomorrow: Outage 20:00–22:00
    # Current simulation time: 20:22
    
    now = datetime(2026, 2, 2, 20, 22)
    
    # Today's half-hour list (48 slots)
    today_half = ["on"] * 48
    # 02:00-05:30 is slots 4 to 10 (inclusive)
    # 02:00 (4), 02:30 (5), 03:00 (6), 03:30 (7), 04:00 (8), 04:30 (9), 05:00 (10)
    for i in range(4, 11):
        today_half[i] = "off"
        
    # Tomorrow's half-hour list
    tomorrow_half = ["on"] * 48
    # 20:00-22:00 is slots 40 to 43 (inclusive)
    # 20:00 (40), 20:30 (41), 21:00 (42), 21:30 (43)
    for i in range(40, 44):
        tomorrow_half[i] = "off"
        
    print(f"Generating test image for simulation time: {now}")
    images = generate_schedule_image(
        today_half, tomorrow_half, now, 
        mode="dynamic", 
        queue_id="5.2 TEST",
        region_name="Івано-Франківська область",
        bot_username="svitlo_if_bot"
    )
    
    output_path = "test.png"
    if images:
        with open(output_path, "wb") as f:
            f.write(images[0].getvalue())
        print(f"✅ Image saved to {os.path.abspath(output_path)}")
    else:
        print("❌ Failed to generate image.")

if __name__ == "__main__":
    generate_test_image()
