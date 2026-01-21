import asyncio
from datetime import datetime
from services.image_generator import generate_schedule_image, convert_api_to_half_list
import os

async def test_image_enhancements():
    print("Generating sample images with region and bot handle...")
    
    # Mock data
    today_data = {f"{h:02d}:{m:02d}": (1 if h < 12 else 2) for h in range(24) for m in (0, 30)}
    today_half = convert_api_to_half_list(today_data)
    
    now_dt = datetime.now()
    region_name = "Дніпропетровська область"
    bot_username = "svitlo_monitor_tg_bot"
    
    # Generate classic
    print("Generating classic view...")
    classic_images = generate_schedule_image(
        today_half, [], now_dt, mode="classic", queue_id="5.2", 
        region_name=region_name, bot_username=bot_username
    )
    with open("verify_classic.png", "wb") as f:
        f.write(classic_images[0].getbuffer())
        
    # Generate dynamic
    print("Generating dynamic view...")
    dynamic_images = generate_schedule_image(
        today_half, [], now_dt, mode="dynamic", queue_id="5.2", 
        region_name=region_name, bot_username=bot_username
    )
    with open("verify_dynamic.png", "wb") as f:
        f.write(dynamic_images[0].getbuffer())
        
    # Generate list
    print("Generating list view...")
    list_images = generate_schedule_image(
        today_half, [], now_dt, mode="list", queue_id="5.2", 
        region_name=region_name, bot_username=bot_username
    )
    with open("verify_list.png", "wb") as f:
        f.write(list_images[0].getbuffer())
        
    print("\nImages generated: verify_classic.png, verify_dynamic.png, verify_list.png")
    print("Please check them manually to confirm labels.")

if __name__ == "__main__":
    asyncio.run(test_image_enhancements())
