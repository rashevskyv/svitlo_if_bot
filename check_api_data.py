import aiohttp
import asyncio
import json

async def test():
    async with aiohttp.ClientSession() as s:
        async with s.get('https://dtek-api.svitlo-proxy.workers.dev/') as r:
            data = await r.json(content_type=None)
            body = json.loads(data['body'])
            regions = body['regions']
            print("Available regions in API (first 5):")
            for reg in regions[:5]:
                print(f"Keys: {reg.keys()}")
                # Try to find name or similar
                name = reg.get('name') or reg.get('title') or reg.get('cpu')
                print(f"- {name} (cpu: {reg.get('cpu')})")

if __name__ == '__main__':
    asyncio.run(test())
