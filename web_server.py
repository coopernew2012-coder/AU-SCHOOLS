"""AU Schools health endpoint. No Minecraft integration endpoints."""
from aiohttp import web
from config import WEB_HOST, WEB_PORT

async def handle_health(request):
    return web.json_response({"status": "ok", "service": "AU Schools"})

async def start_web_server(bot=None):
    app = web.Application()
    app.router.add_get("/health", handle_health)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, WEB_HOST, WEB_PORT).start()
    return runner
