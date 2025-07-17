# Copyright (c) Microsoft Corporation.
# Licensed under the MIT License.
import os
import json
import asyncio
from typing import Callable
from semantic_kernel.agents import AzureAIAgent, GroupChatOrchestration, GroupChatManager, BooleanResult, StringResult, MessageResult
from semantic_kernel.contents import ChatMessageContent, ChatHistory, AuthorRole
from semantic_kernel.agents.runtime import InProcessRuntime
from agents.order_status_plugin import OrderStatusPlugin
from agents.order_refund_plugin import OrderRefundPlugin
from agents.order_cancel_plugin import OrderCancellationPlugin
from azure.ai.projects import AIProjectClient

# Define the confidence threshold for CLU intent recognition
confidence_threshold = float(os.environ.get("CLU_CONFIDENCE_THRESHOLD", "0.5"))

class CustomGroupChatManager(GroupChatManager):
    # Custom logic for filtering results in the group chat
    async def filter_results(self, chat_history: ChatHistory) -> MessageResult:
        if not chat_history:
            return MessageResult(
                result=ChatMessageContent(role="assistant", content="No messages in chat history."),
                reason="Chat history is empty."
            )

        # Get the last message from the chat history
        last_message = chat_history[-1]

        return MessageResult(
            result=ChatMessageContent(role="assistant", content=last_message.content),
            reason="Returning the last agent's response."
        )
    
    # Custom logic to decide if user input is needed
    async def should_request_user_input(self, chat_history: ChatHistory) -> BooleanResult:
        return BooleanResult(result=False, reason="No user input required.")

    # Function to create custom agent selection methods
    async def select_next_agent(self, chat_history, participant_descriptions):
        """
        Multi-agent orchestration method for Semantic Kernel Agent Group Chat.
        This method decides how to select the next agent based on the current message and agent with custom logic based on agent responses.
        """
        last_message = chat_history[-1] if chat_history else None
        format_agent_response(last_message)

        # Process user messages
        if not last_message or last_message.role == AuthorRole.USER:

            if len(chat_history) == 1:
                print("[SYSTEM]: Last message is from the USER, routing to TriageAgent for initial triage...")
                
                try:
                    return StringResult(
                    result=next((agent for agent in participant_descriptions.keys() if agent == "TriageAgent"), None),
                    reason="Routing to TriageAgent for initial triage."
                    )
                except Exception as e:
                    print(f"[SYSTEM]: Error routing to TriageAgent, returning None. Exception: {e}")
                    return StringResult(
                        result=None,
                        reason="Error routing to TriageAgent."
                    )
            else:
                print("[SYSTEM]: Last message is from the USER, routing back to custom agent...")
                
                # If the last message is from the user, route to the last agent that responded
                last_agent = chat_history[-2].name if len(chat_history) > 1 else None
                if last_agent and last_agent in participant_descriptions:
                    print(f"[SYSTEM]: Routing back to last agent: {last_agent}")
                    return StringResult(
                        result=last_agent,
                        reason=f"Routing back to last agent: {last_agent}."
                    )
                else:
                    print("[SYSTEM]: No valid last agent found, returning None.")
                    return StringResult(
                        result=None,
                        reason="No valid last agent found."
                    )
    
        # Process triage agent messages
        elif last_message.name == "TriageAgent":
            print("[SYSTEM]: Last message is from TriageAgent, checking if agent returned a CQA or CLU result...")
            try:
                parsed = json.loads(last_message.content)
    
                # Handle CQA results
                if parsed.get("type") == "cqa_result":
                    print("[SYSTEM]: CQA result received, determining final response...")
                    return StringResult(
                        result=None,
                        reason="CQA result received, terminating chat."
                    )
    
                # Handle CLU results
                if parsed.get("type") == "clu_result":
                    print("[SYSTEM]: CLU result received, checking intent, entities, and confidence...")
                    intent = parsed["response"]["result"]["conversations"][0]["intents"][0]["name"]
                    print("[TriageAgent]: Detected Intent:", intent)
                    print("[TriageAgent]: Identified Intent and Entities, routing to HeadSupportAgent for custom agent selection...")
                    return StringResult(
                        result=next((agent for agent in participant_descriptions.keys() if agent == "HeadSupportAgent"), None),
                        reason="Routing to HeadSupportAgent for custom agent selection."
                    )

            except Exception as e:
                print(f"[SYSTEM]: Error processing TriageAgent message: {e}")
                return StringResult(
                    result=None,
                    reason="Error processing TriageAgent message."
                )
    
        # Process head support agent messages
        elif last_message.name == "HeadSupportAgent":
            print("[SYSTEM]: Last message is from HeadSupportAgent, choosing custom agent...")
            try:
                parsed = json.loads(last_message.content)
    
                # Grab the target agent from the parsed content
                route = parsed.get("target_agent")
                print("[HeadSupportAgent] Routing to target custom agent:", route)
                return StringResult(
                    result=next((agent for agent in participant_descriptions.keys() if agent == route), None),
                    reason=f"Routing to target custom agent: {route}."
                )
            except Exception as e:
                print(f"[SYSTEM]: Error processing HeadSupportAgent message: {e}")
                return StringResult(
                    result=None,
                    reason="Error processing HeadSupportAgent message."
                )
    
        # Default case
        print("[SYSTEM]: No valid routing logic found, returning None.")
        return StringResult(
            result=None,
            reason="No valid routing logic found."
        )

    # Function to check for termination
    async def should_terminate(self, chat_history):
        """
        Custom termination logic for the agent group chat.
        Ends the chat if the last message indicates termination or requires more information.
        """
        last_message = chat_history[-1] if chat_history else None
        # If history is empty, return False
        if not last_message:
            return BooleanResult(
                result=False,
                reason="No messages in chat history."
            )
        
        # Check if the last message contains termination or need_more_info flags
        try:
            parsed_content = json.loads(last_message.content)
            terminated = parsed_content.get("terminated") == "True"
            need_more_info = parsed_content.get("need_more_info") == "True"
    
            if terminated or need_more_info:
                return BooleanResult(
                    result=True,
                    reason="Chat terminated due to agent response."
                )
        except json.JSONDecodeError:
            return BooleanResult(
                result=False,
                reason="Failed to parse last message content."
            )
    
        # Default case: no termination
        return BooleanResult(
            result=False,
            reason="No termination flags found in last message."
        )
        
# Custom multi-agent semantic kernel orchestrator
class SemanticKernelOrchestrator:
    def __init__(
        self,
        client: AIProjectClient,
        model_name: str,
        project_endpoint: str,
        agent_ids: dict,
        fallback_function: Callable[[str, str, str], dict],
        max_retries: int = 3
    ):
        """
        Initialize the semantic kernel orchestrator with the AI Project client, model name, project endpoint,
        agent IDs, fallback function, and maximum retries.
        """
        self.client = client
        self.model_name = model_name
        self.project_endpoint = project_endpoint
        self.agent_ids = agent_ids
        self.fallback_function = fallback_function
        self.max_retries = max_retries

        # Initialize plugins for custom agents
        self.order_status_plugin = OrderStatusPlugin()
        self.order_refund_plugin = OrderRefundPlugin()
        self.order_cancel_plugin = OrderCancellationPlugin()

    async def initialize_agents(self) -> list:
        """
        Initialize the Semantic Kernel Azure AI agents for the semantic kernel orchestrator.
        This method retrieves the agent definitions from AI Foundry and creates AzureAIAgent instances for each foundry agent.
        """
        # Grab the agent definition from AI Foundry
        triage_agent_definition = await self.client.agents.get_agent(self.agent_ids["TRIAGE_AGENT_ID"])
        triage_agent = AzureAIAgent(
            client=self.client,
            definition=triage_agent_definition,
            description="A triage agent that routes inquiries to the proper custom agent."
        )

        order_status_agent_definition = await self.client.agents.get_agent(self.agent_ids["ORDER_STATUS_AGENT_ID"])
        order_status_agent = AzureAIAgent(
            client=self.client,
            definition=order_status_agent_definition,
            description="An agent that checks order status and it must use the OrderStatusPlugin to check the status of an order. If you need more information from the user, you must return a response with 'need_more_info': 'True', otherwise you must return 'need_more_info': 'False'. You must return the response in the following valid JSON format: {'response': <OrderStatusResponse>, 'terminated': 'True', 'need_more_info': <'True' or 'False'>}",
            plugins=[OrderStatusPlugin()],
        )

        order_cancel_agent_definition = await self.client.agents.get_agent(self.agent_ids["ORDER_CANCEL_AGENT_ID"])
        order_cancel_agent = AzureAIAgent(
            client=self.client,
            definition=order_cancel_agent_definition,
            description="An agent that checks on cancellations and it must use the OrderCancellationPlugin to handle order cancellation requests. If you need more information from the user, you must return a response with 'need_more_info': 'True', otherwise you must return 'need_more_info': 'False'. You must return the response in the following valid JSON format: {'response': <OrderCancellationResponse>, 'terminated': 'True', 'need_more_info': <'True' or 'False'>}",
            plugins=[OrderCancellationPlugin()],
        )

        order_refund_agent_definition = await self.client.agents.get_agent(self.agent_ids["ORDER_REFUND_AGENT_ID"])
        order_refund_agent = AzureAIAgent(
            client=self.client,
            definition=order_refund_agent_definition,
            description="An agent that checks on refunds and it must use the OrderRefundPlugin to handle order refund requests. If you need more information from the user, you must return a response with 'need_more_info': 'True', otherwise you must return 'need_more_info': 'False'. You must return the response in the following valid JSON format: {'response': <OrderRefundResponse>, 'terminated': 'True', 'need_more_info': <'True' or 'False'>}",
            plugins=[OrderRefundPlugin()],
        )

        head_support_agent_definition = await self.client.agents.get_agent(self.agent_ids["HEAD_SUPPORT_AGENT_ID"])
        head_support_agent = AzureAIAgent(
            client=self.client,
            definition=head_support_agent_definition,
            description="A head support agent that routes inquiries to the proper custom agent. Ensure you do not use any special characters in the JSON response, as this will cause the agent to fail. The response must be a valid JSON object.",
        )

        print("Agents initialized successfully.")
        print(f"Triage Agent ID: {triage_agent.id}")
        print(f"Head Support Agent ID: {head_support_agent.id}")
        print(f"Order Status Agent ID: {order_status_agent.id}")
        print(f"Order Cancel Agent ID: {order_cancel_agent.id}")
        print(f"Order Refund Agent ID: {order_refund_agent.id}")

        return [triage_agent, head_support_agent, order_status_agent, order_cancel_agent, order_refund_agent]

    async def create_agent_group_chat(self) -> None:
        """
        Create an agent group chat with the specified chat ID after all agents have been initialized.
        This method initializes the agents and sets up the agent group chat with custom selection and termination strategies
        """
        created_agents = await self.initialize_agents()
        print("Agents initialized:", [agent.name for agent in created_agents])

        self.orchestration = GroupChatOrchestration(
            members=created_agents,
            manager=CustomGroupChatManager(),
        )

        print("Agent group chat created successfully.")

    async def process_message(self, message_content: str) -> str:
        """
        Process a message in the agent group chat.
        This method creates a new agent group chat and processes the message.
        """
        retry_count = 0
        last_exception = None

        # Use retry logic to handle potential errors during chat invocation
        while retry_count < self.max_retries:
            print(f"\n[RETRY ATTEMPT {retry_count}] Starting new runtime...")
            runtime = InProcessRuntime()
            runtime.start()

            try:
                orchestration_result = await self.orchestration.invoke(
                    task=message_content,
                    runtime=runtime,
                )

                try:
                    # Timeout to avoid indefinite hangs
                    value = await orchestration_result.get(timeout=35)
                    print(f"\n***** Result *****\n{value.content}")

                    final_response = json.loads(value.content)

                    # if CQA
                    if final_response.get("type") == "cqa_result":
                        print("[SYSTEM]: Final CQA result received, terminating chat.")
                        final_response = final_response['response']['answers'][0]['answer']
                        print("[SYSTEM]: Final response is ", final_response)
                        return final_response
                    
                    # if CLU
                    else:
                        print("[SYSTEM]: Final CLU result received, printing custom agent response...")
                        print("[SYSTEM]: Final response is ", final_response['response'])
                        return final_response['response']

                except Exception as e:
                    print(f"[EXCEPTION]: Orchestration failed with exception: {e}")
                    last_exception = {"type": "exception", "message": str(e)}
                    retry_count += 1

            finally:
                try:
                    await runtime.stop_when_idle()
                except Exception as e:
                    print(f"[SHUTDOWN ERROR]: Runtime failed to shut down cleanly: {e}")

            # Short delay before retry
            await asyncio.sleep(1)

        if last_exception:
            return {
                "error": f"An error occurred: {last_exception}"
            }

def format_agent_response(response):
    try:
        # Pretty print the JSON response
        formatted_content = json.dumps(json.loads(response.content), indent=2)
        print(f"[{response.name if response.name else 'USER'}]: \n{formatted_content}\n")
    except json.JSONDecodeError:
        # Fallback to regular print if content is not JSON
        print(f"[{response.name}]: {response.content}\n")
    return response.content
