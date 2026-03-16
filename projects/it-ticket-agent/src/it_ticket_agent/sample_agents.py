from fastapi import FastAPI

from .agent_clients import LocalAgentRuntime
from .schemas import TaskPackage, model_to_dict


app = FastAPI(title="Sample Agent Runtime")
runtime = LocalAgentRuntime()


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}


@app.post("/api/v1/agents/{agent_name}/run")
async def run_agent(agent_name: str, task: TaskPackage):
    result = await runtime.run(agent_name, task)
    return model_to_dict(result)
