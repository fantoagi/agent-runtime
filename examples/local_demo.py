from pathlib import Path

from agent_runtime.sdk import create_local_runtime, demo_agent


async def main() -> None:
    runtime = create_local_runtime(Path.cwd())
    runtime.register_agent(demo_agent())
    run = await runtime.run("demo", "19 * 23")
    print(f"{run.id}: {run.status} -> {run.result}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
