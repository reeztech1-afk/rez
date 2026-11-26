"""Worker entrypoint: discovers and serves the Higgsfield driver workflow."""
import asyncio

import mistralai.workflows as workflows

from flow import HiggsfieldWorkflow


async def main() -> None:
    await workflows.run_worker([HiggsfieldWorkflow])


if __name__ == "__main__":
    asyncio.run(main())
