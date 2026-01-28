import aiohttp
import asyncio
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)

IF_QUEUES_URL = "https://be-svitlo.oe.if.ua/gpv-queue-list"

async def fetch_queues():
    print(f"Fetching {IF_QUEUES_URL}...")
    async with aiohttp.ClientSession() as session:
        async with session.get(IF_QUEUES_URL) as resp:
            if resp.status != 200:
                print(f"Error: {resp.status}")
                return None
            return await resp.json()

async def main():
    queues = await fetch_queues()
    if queues:
        print(json.dumps(queues, indent=2, ensure_ascii=False))
        
        # Check if 5.2 is in the list
        # Assuming the structure is a list of dicts or strings
        found = False
        if isinstance(queues, list):
            for q in queues:
                # Adjust based on actual structure
                if isinstance(q, dict) and q.get("queue") == "5.2":
                    found = True
                    break
                if q == "5.2":
                    found = True
                    break
        
        if found:
            print("✅ Queue 5.2 found in queue list!")
        else:
            print("❌ Queue 5.2 NOT found in queue list.")

if __name__ == "__main__":
    asyncio.run(main())
