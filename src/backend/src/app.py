import os
from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import asynccontextmanager
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from pydantic import BaseModel
from semantic_kernel_orchestrator import SemanticKernelOrchestrator
from azure.identity.aio import DefaultAzureCredential
from semantic_kernel.agents import AzureAIAgent
from dotenv import load_dotenv
import logging
load_dotenv()

# Environment variables
PROJECT_ENDPOINT = os.environ.get("AGENTS_PROJECT_ENDPOINT")
MODEL_NAME = os.environ.get("AOAI_DEPLOYMENT")
AGENT_IDS = {
    "TRIAGE_AGENT_ID": os.environ.get("TRIAGE_AGENT_ID"),
    "HEAD_SUPPORT_AGENT_ID": os.environ.get("HEAD_SUPPORT_AGENT_ID"),
    "ORDER_STATUS_AGENT_ID": os.environ.get("ORDER_STATUS_AGENT_ID"),
    "ORDER_CANCEL_AGENT_ID": os.environ.get("ORDER_CANCEL_AGENT_ID"),
    "ORDER_REFUND_AGENT_ID": os.environ.get("ORDER_REFUND_AGENT_ID"),
}

DIST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "dist"))
# log dist_dir
logging.warning(f"DIST_DIR: {DIST_DIR}")

class ChatRequest(BaseModel):
    message: str

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup
    try:
        logging.basicConfig(level=logging.WARNING)
        logging.warning("Setting up Azure credentials and client...")
        logging.warning(f"Using PROJECT_ENDPOINT: {PROJECT_ENDPOINT}")
        logging.warning(f"Using MODEL_NAME: {MODEL_NAME}")
        creds = DefaultAzureCredential(exclude_interactive_browser_credential=False)
        await creds.__aenter__()

        client = AzureAIAgent.create_client(credential=creds, endpoint=PROJECT_ENDPOINT)
        await client.__aenter__()

        orchestrator = SemanticKernelOrchestrator(client, MODEL_NAME, PROJECT_ENDPOINT, AGENT_IDS)
        await orchestrator.initialize()

        # Store in app state
        app.state.creds = creds
        app.state.client = client
        app.state.orchestrator = orchestrator

        yield

    except Exception as e:
        logging.error(f"Error during setup: {e}")
        raise HTTPException(status_code=500, detail="Internal Server Error")

    finally:
        # Teardown
        await client.__aexit__(None, None, None)
        await creds.__aexit__(None, None, None)

# Create FastAPI app with lifespan
app = FastAPI(lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=os.path.join(DIST_DIR, "assets")), name="assets")


@app.get("/")
async def serve_frontend():
    return FileResponse(os.path.join(DIST_DIR, "index.html"))

# Comment out for local testing
# @app.get("/")
# async def home_page():
#     """
#     Render the home page with a simple message.
#     """
#     return JSONResponse(content={"message": "Welcome to the Semantic Kernel Orchestrator API testing test test!"})

# Define the chat endpoint
@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    orchestrator = app.state.orchestrator
    response = await orchestrator.process_message(request.message)
    logging.warning(f"Response from orchestrator: {response}")
    return JSONResponse(content={"messages": [response]}, status_code=200)
