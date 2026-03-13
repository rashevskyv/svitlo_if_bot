import asyncio
import aiohttp
import json
import os
import sys
import logging
from datetime import datetime, timezone
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to sys.path to import modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.api_client import SvitloApiClient, IF_REGION_ID

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
_LOGGER = logging.getLogger("if_sync_service")

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "if_schedules.json")

async def sync_if_data():
    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    async with aiohttp.ClientSession() as session:
        client = SvitloApiClient(session=session)
        
        _LOGGER.info("Starting IF data synchronization...")
        
        # 1. Fetch available queues
        queues = await client._fetch_if_queues()
        if not queues:
            _LOGGER.warning("Could not fetch IF queues, using fallback list")
            queues = [f"{g}.{s}" for g in range(1, 7) for s in range(1, 3)]
        
        _LOGGER.info(f"Syncing {len(queues)} queues: {queues}")
        
        sync_result = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "region_id": IF_REGION_ID,
            "schedules": {}
        }
        
        for q in queues:
            raw_data = await client._fetch_if_schedule(q)
            if raw_data:
                parsed_data = client._parse_if_schedule(raw_data, q)
                if parsed_data:
                    sync_result["schedules"][q] = parsed_data
                    _LOGGER.info(f"Successfully synced queue {q}")
                else:
                    _LOGGER.warning(f"Failed to parse data for queue {q}")
            else:
                _LOGGER.warning(f"Failed to fetch data for queue {q}")
        
        if sync_result["schedules"]:
            # Save locally
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
                json.dump(sync_result, f, ensure_ascii=False, indent=2)
            _LOGGER.info(f"Data saved locally to {OUTPUT_FILE}")

            # Upload to Gist
            gist_id = os.getenv("GIST_ID")
            github_token = os.getenv("GITHUB_TOKEN")
            
            if gist_id and github_token:
                _LOGGER.info(f"Uploading to Gist {gist_id}...")
                url = f"https://api.github.com/gists/{gist_id}"
                headers = {
                    "Authorization": f"token {github_token}",
                    "Accept": "application/vnd.github.v3+json"
                }
                payload = {
                    "files": {
                        "if_schedules.json": {
                            "content": json.dumps(sync_result, ensure_ascii=False)
                        }
                    }
                }
                async with session.patch(url, headers=headers, json=payload) as resp:
                    if resp.status == 200:
                        _LOGGER.info("Successfully updated GitHub Gist")
                    else:
                        text = await resp.text()
                        _LOGGER.error(f"Failed to update Gist: {resp.status} - {text}")
            else:
                _LOGGER.warning("GIST_ID or GITHUB_TOKEN not found in environment, skipping Gist upload")
            
            _LOGGER.info("Synchronization complete.")
        else:
            _LOGGER.error("Synchronization failed: No data fetched.")

if __name__ == "__main__":
    asyncio.run(sync_if_data())
