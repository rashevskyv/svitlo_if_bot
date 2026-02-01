from datetime import datetime
from services.image_generator import generate_schedule_image, convert_api_to_half_list, get_next_event_info
import os

def debug_overlap():
    # 20:22 Today
    now = datetime(2026, 2, 1, 20, 22)
    
    # Outages today: 02:00–05:30 (indices 4..11)
    today_half = ["on"] * 48
    for i in range(4, 11):
        today_half[i] = "off"
    
    # Outages tomorrow: 20:00–22:00 (indices 40..43)
    tomorrow_half = ["on"] * 48
    for i in range(40, 44):
        tomorrow_half[i] = "off"
    
    print("Generating dynamic mode image...")
    images = generate_schedule_image(
        today_half, tomorrow_half, now, 
        mode="dynamic", 
        queue_id="5.2 TEST",
        region_name="Івано-Франківська область",
        bot_username="svitlo_if_bot"
    )
    
    # Verify the text
    forecast_text = get_next_event_info(today_half, tomorrow_half, now)
    print("\n--- GENERATED CAPTION TEXT ---")
    print(forecast_text)
    print("------------------------------\n")
    
    output_path = "debug_overlap.png"
    with open(output_path, "wb") as f:
        f.write(images[0].getvalue())
        
    print(f"✅ Image saved to {os.path.abspath(output_path)}")
    print("Please check the image for a red outer arc in the 20:00-22:00 area.")

if __name__ == "__main__":
    debug_overlap()
