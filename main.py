import sys

from clip_synth.core.application import Application


def main():
    app = Application()

    if sys.platform == "win32":
        import asyncio
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    sys.exit(app.run())


if __name__ == "__main__":
    main()
