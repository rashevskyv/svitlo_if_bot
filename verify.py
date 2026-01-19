import asyncio
import sys
import os
from datetime import datetime

# Додаємо шлях до проекту
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from services.api_client import SvitloApiClient
from services.image_generator import generate_schedule_image, convert_api_to_half_list

async def test():
    api = SvitloApiClient()
    
    print("1. Тестування отримання регіонів...")
    regions = await api.get_regions()
    if regions:
        print(f"Отримано {len(regions)} регіонів.")
        # Беремо Івано-Франківськ для тесту
        if "ivano-frankivska-oblast" in regions:
            print("Івано-Франківська область знайдена.")
        else:
            print("Івано-Франківська область НЕ знайдена!")
    else:
        print("Помилка при отриманні регіонів!")
        return

    print("\n2. Тестування отримання розкладу (IF, черга 4.2)...")
    schedule = await api.fetch_schedule("ivano-frankivska-oblast", "4.2")
import asyncio
import sys
import os
from datetime import datetime

# Додаємо шлях до проекту
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from services.api_client import SvitloApiClient
from services.image_generator import generate_schedule_image, convert_api_to_half_list

async def test():
    api = SvitloApiClient()
    
    print("1. Тестування отримання регіонів...")
    regions = await api.get_regions()
    if regions:
        print(f"Отримано {len(regions)} регіонів.")
        # Беремо Івано-Франківськ для тесту
        if "ivano-frankivska-oblast" in regions:
            print("Івано-Франківська область знайдена.")
        else:
            print("Івано-Франківська область НЕ знайдена!")
    else:
        print("Помилка при отриманні регіонів!")
        return

    print("\n2. Тестування отримання розкладу (IF, черга 4.2)...")
    schedule = await api.fetch_schedule("ivano-frankivska-oblast", "4.2")
    if schedule:
        print("Розклад отримано успішно.")
        
        print("\n3. Генерація зображень (Список)...")
        today_half = convert_api_to_half_list(schedule["schedule"].get(schedule["date_today"], {}))
        tomorrow_half = convert_api_to_half_list(schedule["schedule"].get(schedule["date_tomorrow"], {}))
        current_dt = datetime.now()
        queue_id = "4.2" # Assuming queue_id is still "4.2" for this test
        images = generate_schedule_image(today_half, tomorrow_half, current_dt, mode="dynamic", queue_id=queue_id)
        for i, img_buf in enumerate(images):
            with open(f"test_dynamic_{i}.png", "wb") as f:
                f.write(img_buf.getbuffer())
        print(f"✅ Тестові зображення динамічного режиму збережено ({len(images)} шт)")
    else:
        print("Помилка при отриманні розкладу!")

if __name__ == "__main__":
    asyncio.run(test())
