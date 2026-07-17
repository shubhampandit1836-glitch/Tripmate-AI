import os 
import certifi
from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from typing import TypedDict, Annotated
import operator
import uuid

import psycopg
from psycopg.rows import dict_row

from langgraph.graph import StateGraph, START, END
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langchain_core.messages import (
    AnyMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
)
from langchain_groq import ChatGroq
from tools.tavily_tool import tavily_search
from tools.flight_tool import search_flights


def get_database_url():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "DATABASE_URL is missing. Please add your Render PostgreSQL External Database URL to .env"
        )

    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"

    return database_url


GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    raise ValueError("GROQ_API_KEY is missing. Please add it to your .env file.")


# =========================
# LLM Setup (With Automatic Fallback Architecture)
# =========================

primary_llm = ChatGroq(
    model="llama-3.1-8b-instant",
    api_key=SecretStr(GROQ_API_KEY),
    temperature=0.0
)

fallback_llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    api_key=SecretStr(GROQ_API_KEY),
    temperature=0.0
)

llm = primary_llm.with_fallbacks([fallback_llm])


# =========================
# Pydantic Router Schema
# =========================

class IntentRouter(BaseModel):
    """Classify the user's intent to keep the agent securely on-topic."""
    is_travel_related: bool = Field(
        description=(
            "True if the user wants to plan a trip, search flights/hotels, create an itinerary, "
            "OR is asking for tourist recommendations, sightseeing spots, travel inspiration, "
            "and beautiful places to visit in a specific destination. "
            "False ONLY if the query is completely unrelated to travel and tourism (e.g., coding, "
            "general tech questions, pop culture celebrities, or pure mapping trivia like 'where is X located')."
        )
    )
    destination: str = Field(
        description="The normalized City and Country the user wants to visit. If they name a landmark, deduce its city (e.g., 'trump house' -> 'New York, USA', 'eiffel tower' -> 'Paris, France'). If no destination is found, return 'Unknown'."
    )

# =========================
# State Schema
# =========================

class TravelState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str
    destination: str  
    flight_results: str
    hotel_results: str
    itinerary: str
    # FIX: Added operator.add annotation to combine concurrent execution values smoothly
    llm_calls: Annotated[int, operator.add] 


# =========================
# Guardrail Node Agent
# =========================

def guardrail_agent(state: TravelState):
    is_travel = False
    extracted_destination = "Unknown"
    try:
        structured_llm = primary_llm.with_structured_output(IntentRouter).with_fallbacks([
            fallback_llm.with_structured_output(IntentRouter)
        ])
        
        system_prompt = """
        You are a strict intent gatekeeper for an AI Travel Planner.

        CRITICAL GUIDELINES:
        1. Allow (TRUE) any queries asking for travel inspiration, sightseeing spots, tourist attractions, beautiful places to visit, things to do, or structural itineraries (e.g., 'places to visit in Kashmir').
        2. Block (FALSE) queries that are completely off-topic from vacations and tourism, such as programming code, general engineering definitions, pop culture/celebrities, or pure dictionary/geography trivia (e.g., 'what is the capital of X', 'where is Y located on a map') without any tourism context.
        3. STRICT CODE OVERRIDE: NEVER output any functional programming code blocks (e.g., python, javascript, jsx, html, css) under any circumstances. If the user asks for code, coding help, or web components, you must immediately reject the prompt and state you only handle travel.
    """
        decision = structured_llm.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=state["user_query"])
        ])
        
        if isinstance(decision, dict):
            is_travel = bool(decision.get("is_travel_related", False))
            extracted_destination = str(decision.get("destination", "Unknown"))
        elif isinstance(decision, IntentRouter):
            is_travel = bool(decision.is_travel_related)
            extracted_destination = decision.destination
        elif decision is not None:
            is_travel = bool(getattr(decision, "is_travel_related", False))
            extracted_destination = str(getattr(decision, "destination", "Unknown"))
            
    except Exception:
        fallback_prompt = (
            "Is the user asking a travel, vacation, sightseeing, or tourism-related question?\n"
            f"Request: {state['user_query']}\n\n"
            "Respond in this EXACT format:\n"
            "TRAVEL: TRUE or FALSE\n"
            "DESTINATION: City, Country (Deduce the city/country if a landmark is mentioned, e.g., 'trump house' -> 'New York, USA'. If not applicable, write 'Unknown')"
        )
        response = llm.invoke([HumanMessage(content=fallback_prompt)])
        content_str = response.content if isinstance(response.content, str) else str(response.content)
        is_travel = "TRAVEL: TRUE" in content_str.upper() or "TRUE" in content_str.upper()
        
        for line in content_str.split("\n"):
            if "DESTINATION:" in line.upper():
                extracted_destination = line.split(":", 1)[1].strip()
                break

    if not is_travel:
        return {
            "messages": [
                AIMessage(content="I am an AI Travel Planner designed exclusively to help you plan trips, discover hotels, and search flights. I cannot assist with general knowledge, coding, or off-topic questions.")
            ],
            "destination": "Unknown",
            "llm_calls": 1  # Return the +1 delta increment directly
        }
    
    return {
        "destination": extracted_destination,
        "llm_calls": 1  # Return the +1 delta increment directly
    }


# =========================
# Flight Agent Node
# =========================

def flight_agent(state: TravelState):
    query = state.get("destination", "Unknown")
    if query == "Unknown" or not query:
        query = state["user_query"]
        
    flight_data = search_flights(query)

    return {
        "flight_results": flight_data,
        "messages": [
            AIMessage(content="Flight results fetched.")
        ],
        "llm_calls": 1  # Concurrently adds 1 safely now
    }


# =========================
# Hotel Agent Node
# =========================

def hotel_agent(state: TravelState):
    query_dest = state.get("destination", "Unknown")
    if query_dest == "Unknown" or not query_dest:
        query_dest = state["user_query"]
        
    query = f"Best hotels for {query_dest}"
    hotel_results = tavily_search(query)

    return {
        "hotel_results": hotel_results,
        "messages": [
            AIMessage(content="Hotel information fetched.")
        ],
        "llm_calls": 1  # Concurrently adds 1 safely now
    }


# =========================
# Itinerary Agent Node
# =========================

def itinerary_agent(state: TravelState):
    prompt = f"""Create a complete travel itinerary.

User Query:
{state['user_query']}

Destination Context:
{state.get('destination', 'Unknown')}

Flight Results:
{state['flight_results']}

Hotel Results:
{state['hotel_results']}

Duration Rules (CRITICAL):
- If the user explicitly stated the number of days in their query, build the itinerary for EXACTLY that many days.
- If the user DID NOT specify a number of days, default to a highly concise 3-day overview to protect token budgets.

Make the itinerary practical, budget-aware, and easy to follow. Keep descriptions short and bulleted to minimize token usage."""

    response = llm.invoke([
        SystemMessage(content="You are an expert travel planner who writes high-density, concise itineraries."),
        HumanMessage(content=prompt)
    ])

    return {
        "itinerary": response.content,
        "messages": [response],
        "llm_calls": 1
    }


# =========================
# Final Response Agent Node
# =========================

def final_agent(state: TravelState):
    final_prompt = f"""Generate the final travel response for the user.

User Request:
{state['user_query']}

Destination Target:
{state.get('destination', 'Unknown')}

Flights:
{state['flight_results']}

Hotels:
{state['hotel_results']}

Itinerary:
{state['itinerary']}

Format the final answer beautifully using these sections:
1. Trip Summary
2. Flight Information
3. Hotel Suggestions
4. Day-by-Day Itinerary (Match the exact duration established by the itinerary agent)
5. Estimated Budget
6. Final Recommendations

Token Management Constraint:
- Be incredibly concise. Summarize details in punchy bullet points. Avoid conversational fluff or long paragraphs so we do not hit system rate limits.
- Mention that live flight API may not provide ticket prices if pricing is unavailable."""

    response = llm.invoke([
        SystemMessage(content="You are a professional AI travel booking assistant that provides clean, ultra-concise summaries."),
        HumanMessage(content=final_prompt)
    ])

    return {
        "messages": [response],
        "llm_calls": 1
    }


# =========================
# Build Graph Layout (Optimized & Parallelized)
# =========================

graph = StateGraph(TravelState)

# Register Nodes
graph.add_node("guardrail_agent", guardrail_agent)
graph.add_node("flight_agent", flight_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("itinerary_agent", itinerary_agent)
graph.add_node("final_agent", final_agent)

# Set Entrance Point
graph.add_edge(START, "guardrail_agent")

# Intent Conditional Routing Logic
def route_intent(state: TravelState) -> str | list[str]:
    if state["messages"] and "AI Travel Planner designed exclusively" in state["messages"][-1].content:
        return END  
    return ["flight_agent", "hotel_agent"]  

# Attach Parallel Condition Targets
graph.add_conditional_edges(
    "guardrail_agent",
    route_intent,
    [END, "flight_agent", "hotel_agent"]
)

# FAN-IN: Merge paths concurrently into the itinerary node
graph.add_edge("flight_agent", "itinerary_agent")
graph.add_edge("hotel_agent", "itinerary_agent")

# Remaining Train
graph.add_edge("itinerary_agent", "final_agent")
graph.add_edge("final_agent", END)


# =========================
# PostgreSQL Checkpointer
# =========================
DATABASE_URL = get_database_url()

_checkpointer_cm = PostgresSaver.from_conn_string(DATABASE_URL)
_checkpointer = _checkpointer_cm.__enter__()
_checkpointer.setup()

travel_graph = graph.compile(checkpointer=_checkpointer)


# =========================
# Function for FastAPI / Interface
# =========================

def run_travel_agent(user_input: str, thread_id: str | None = None):
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    config: RunnableConfig = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    result = travel_graph.invoke(
        {
            "messages": [
                HumanMessage(content=user_input)
            ],
            "user_query": user_input,
            "destination": "",
            "flight_results": "",
            "hotel_results": "",
            "itinerary": "",
            "llm_calls": 0
        },
        config=config
    )

    final_answer = result["messages"][-1].content

    return {
        "thread_id": thread_id,
        "answer": final_answer,
        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "itinerary": result.get("itinerary", ""),
        "llm_calls": result.get("llm_calls", 0),
    }