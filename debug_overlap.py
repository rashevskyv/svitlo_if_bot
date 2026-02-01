from datetime import datetime
from services.image_generator import generate_schedule_image, convert_api_to_half_list, get_next_event_info
import os

def debug_overlap():
    # 19:30 Today
    now = datetime(2026, 2, 1, 19, 30)
    
    # Simple schedules: all "on" today, but some "off" tomorrow during the same time
    today_half = ["on"] * 48
    tomorrow_half = ["on"] * 48
    
    # Tomorrow's evening outage (20:00 - 22:00)
    # This should be "hidden" by today's evening "on" status
    tomorrow_half[40] = "off" # 20:00
    tomorrow_half[41] = "off" # 20:30
    tomorrow_half[42] = "off" # 21:00
    tomorrow_half[43] = "off" # 21:30
    
    # Tomorrow's morning outage (visible in forecast)
    tomorrow_half[16] = "off" # 08:00
    tomorrow_half[17] = "off" # 08:30
    
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
