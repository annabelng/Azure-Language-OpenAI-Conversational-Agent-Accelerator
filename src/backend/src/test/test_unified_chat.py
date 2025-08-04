# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
import pytest
import os
import sys
import subprocess
import requests
import time
from typing import Generator

"""
This module contains test cases for the chat endpoint of the app with unified orchestration.
It includes single-turn and multi-turn interactions with parameterized test cases.
The tests are designed to validate the responses from the chat endpoint based on predefined scenarios.

Test different routing strategies by setting the ROUTER_TYPE environment variable.
For example:
export ROUTER_TYPE=TRIAGE_AGENT
pytest test/test_unified_chat.py -s -v

Possible values for ROUTER_TYPE:
- BYPASS
- CLU
- CQA
- ORCHESTRATION
- FUNCTION_CALLING
- TRIAGE_AGENT
"""

# Test cases for the chat endpoint
SINGLE_TURN_TEST_CASES = [
    {
        "name": "return_policy",
        "current_question": "What is the return policy",
        "history": [
            {
                "role": "",
                "content": ""
            }
        ],
        "expected_response": [
            "Contoso Outdoors is proud to offer a 30 day refund policy. Return unopened, unused products within 30 days of purchase to any Contoso Outdoors store for a full refund."
        ]
    },
    {
        "name": "order_status",
        "current_question": "What is the status of order 12345?",
        "history": [
            {
                "role": "",
                "content": ""
            }
        ],
        "expected_response": ["Order 12345 has shipped."]
    },
    {
        "name": "order_refund",
        "current_question": "I want to refund order 0984",
        "history": [
            {
                "role": "",
                "content": ""
            }
        ],
        "expected_response": ["Refund is still processing for order 0984."]
    },
    {
        "name": "order_cancel",
        "current_question": "Please cancel my order 56789",
        "history": [
            {
                "role": "",
                "content": ""
            }
        ],
        "expected_response": ["Order 56789 has successfully been cancelled."]
    },
    {
        "name": "need_more_info_refund",
        "current_question": "Was I refunded for my order?",
        "history": [
            {
                "role": "",
                "content": ""
            }
        ],
        "expected_response": ["Please specify order ID in order to check refund status."]
    },
    {
        "name": "need_more_info_order_status",
        "current_question": "I want to know the status of my order",
        "history": [
            {
                "role": "",
                "content": ""
            }
        ],
        "expected_response": ["Please specify order ID in order to check order status."]
    },
    {
        "name": "need_more_info_order_cancel",
        "current_question": "I want to cancel my order",
        "history": [
            {
                "role": "",
                "content": ""
            }
        ],
        "expected_response": ["Please specify order ID in order to cancel order."]
    }
]


# Launch the FastAPI server using uvicorn for testing purposes
@pytest.fixture(scope="session")
def uvicorn_server() -> Generator:
    """Start uvicorn server for testing"""
    # Set environment variables
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"

    # Start server using python -m
    process = subprocess.Popen([
        sys.executable, "-m", "uvicorn",
        "unified_app:app",
        "--host", "127.0.0.1",
        "--port", "7000",
        "--reload"
    ], env=env)

    # Wait for server to start
    url = "http://127.0.0.1:7000"
    for _ in range(30):
        try:
            requests.get(url)
            break
        except requests.ConnectionError:
            time.sleep(1)

    yield url  # Return the server URL for tests to use

    # Cleanup
    process.terminate()
    process.wait()


# Test the chat endpoint with parameterized test cases for single turn interactions
@pytest.mark.parametrize("test_case", SINGLE_TURN_TEST_CASES, ids=lambda x: x["name"])
def test_single_turn(uvicorn_server: str, test_case: dict):
    """Test chat endpoint responses"""

    response = requests.post(
        f"{uvicorn_server}/chat",
        json={"message": test_case["current_question"], "history": test_case["history"]},
    )

    # Check response
    assert response.status_code == 200, f"Expected status 200, got {response.status_code}"
    data = response.json()

    # Verify response
    assert data["messages"] == test_case["expected_response"], (
        f"Response mismatch for test '{test_case['name']}'. "
        f"Expected: {test_case['expected_response']}, Actual: {data['messages']}"
    )
