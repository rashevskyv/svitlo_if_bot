import aiohttp
import asyncio
import json
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
_LOGGER = logging.getLogger(__name__)

IF_API_URL = "https://be-svitlo.oe.if.ua/schedule-by-queue"

async def fetch_if_schedule(queue: str):
    url = f"{IF_API_URL}?queue={queue}"
    print(f"Fetching {url}...")
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                print(f"Error: {resp.status}")
                return None
            return await resp.json()

def parse_if_schedule(raw_data: list, queue: str):
    parsed = {}
    print(f"Parsing {len(raw_data)} days of data...")
    for day_data in raw_data:
        date_str = day_data.get("eventDate")
        print(f"Found date: {date_str}")
        
        if not date_str: continue
        
        try:
            dt = datetime.strptime(date_str, "%d.%m.%Y")
            iso_date = dt.date().isoformat()
        except Exception as e:
            print(f"Date parse error: {e}")
            continue
            
        day_schedule = {}
        # Init grid
        for h in range(24):
            day_schedule[f"{h:02d}:00"] = 1
            day_schedule[f"{h:02d}:30"] = 1
        
        intervals = day_data.get("queues", {}).get(queue, [])
        print(f"  Intervals for {queue}: {intervals}")
        
        for interval in intervals:
            start_str = interval.get("from")
            end_str = interval.get("to")
            if not start_str or not end_str: continue
            
            try:
                start_h, start_m = map(int, start_str.split(":"))
                end_h, end_m = map(int, end_str.split(":"))
                
                curr_h, curr_m = start_h, start_m
                while (curr_h < end_h) or (curr_h == end_h and curr_m < end_m):
                    day_schedule[f"{curr_h:02d}:{curr_m:02d}"] = 2
                    curr_m += 30
                    if curr_m >= 60:
                        curr_m = 0
                        curr_h += 1
            except Exception as e:
                print(f"  Interval parse error: {e}")
                
        parsed[iso_date] = day_schedule
    return parsed

async def main():
    queue = "5.2"
    raw_data = await fetch_if_schedule(queue)
    if raw_data:
        print(json.dumps(raw_data, indent=2, ensure_ascii=False))
        parsed = parse_if_schedule(raw_data, queue)
        print("\nParsed Data Keys:", parsed.keys())
        
        # Check tomorrow
        tomorrow = (datetime.now().date().replace(day=datetime.now().day+1)).isoformat()
        if tomorrow in parsed:
            print(f"✅ Tomorrow ({tomorrow}) found in parsed data!")
        else:
            print(f"❌ Tomorrow ({tomorrow}) NOT found in parsed data.")
            # Try to find what dates ARE there
            today = datetime.now().date().isoformat()
            print(f"Today is {today}")

if __name__ == "__main__":
    asyncio.run(main())
