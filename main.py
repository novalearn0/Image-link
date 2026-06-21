"""
main.py - Entry point for the Telegram Caption Bot.
"""

import asyncio
import os
import sys

# Create logs directory before loguru tries to write to it
os.makedirs("logs", exist_ok=True)

# Fix for Python 3.10+ asyncio event loop policy
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def main():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        from bot import run_bot
        loop.run_until_complete(run_bot())
    except KeyboardInterrupt:
        pass
    finally:
        loop.close()


if __name__ == "__main__":
    main()
