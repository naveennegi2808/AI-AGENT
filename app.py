import os
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"  # For local testing only
import json
import requests
import pathlib
import base64
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from flask import Flask, render_template, request, session, redirect, url_for
from dotenv import load_dotenv

# --- Pydantic Import ---
from pydantic import BaseModel, Field

# --- LangChain Imports ---
from langchain.agents import AgentExecutor, create_react_agent
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import tool
from langchain import hub
from langchain_community.tools import DuckDuckGoSearchRun
from langchain_community.chat_message_histories import ChatMessageHistory
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.exceptions import OutputParserException

# --- Google API Imports ---
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from google.auth.transport.requests import Request

# ==============================================
# ENV SETUP
# ==============================================
load_dotenv()
# Instagram OAuth Configuration (UPDATED FOR GRAPH API)
INSTAGRAM_CLIENT_ID = os.getenv("INSTAGRAM_CLIENT_ID")
INSTAGRAM_CLIENT_SECRET = os.getenv("INSTAGRAM_CLIENT_SECRET")
INSTAGRAM_REDIRECT_URI = "http://localhost:5000/instagram/callback"
# THESE URLS HAVE CHANGED
INSTAGRAM_AUTH_URL = "https://www.facebook.com/v18.0/dialog/oauth" 
INSTAGRAM_TOKEN_URL = "https://graph.facebook.com/v18.0/oauth/access_token"
# THIS BASE URL HAS CHANGED
INSTAGRAM_API_BASE = "https://graph.facebook.com/v18.0"
# Flask setup
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "a-truly-secret-key-for-production")

# Path for OAuth credentials
CLIENT_SECRETS_FILE = "credentials.json"
# Updated SCOPES to include Calendar
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar"
]

# ==============================================
@tool
def get_weather_data(city: str) -> str:
    """Fetches current weather data for a given city."""
    try:
        url = f'https://api.weatherstack.com/current?access_key=9b550a2551e099dcd25e15757411be81&query={city}'
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()

        if 'error' in data:
            return f"Weather API error: {data['error'].get('info', 'Unknown error')}"

        if 'current' in data:
            current = data['current']
            location = data.get('location', {})
            return (
                f"Weather for {location.get('name', city)}, {location.get('country', '')}:\n"
                f"Temperature: {current.get('temperature', 'N/A')}°C\n"
                f"Weather: {current.get('weather_descriptions', ['N/A'])[0]}\n"
                f"Humidity: {current.get('humidity', 'N/A')}%\n"
                f"Wind Speed: {current.get('wind_speed', 'N/A')} km/h"
            )
        return f"No weather data found for {city}"

    except Exception as e:
        return f"Error fetching weather data: {str(e)}"

def get_google_credentials():
    """Get Google credentials from session (works for both Gmail and Calendar)"""
    if "credentials" not in session:
        return None
    
    creds_data = session["credentials"]
    creds = Credentials.from_authorized_user_info(creds_data, SCOPES)
    
    # Check if credentials are expired and refresh if needed
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        # Update session with refreshed credentials
        session["credentials"] = json.loads(creds.to_json())
    
    return creds

# Keeping the old function name for backward compatibility
def get_gmail_credentials():
    """Get Gmail credentials from session (alias for get_google_credentials)"""
    return get_google_credentials()
def get_instagram_credentials():
    """Get Instagram access token from session"""
    return session.get("instagram_access_token")
@tool
def get_today_date() -> str:
    """Returns today's date in YYYY-MM-DD format"""
    return datetime.today().strftime("%Y-%m-%d")

@tool
def send_email(action_input) -> str:
    """
    Send an email using Gmail API. The action_input is expected to be a
    JSON string containing the email details ('recipient', 'subject', 'body').
    """
    try:
        email_data = json.loads(action_input)
        
        recipient = email_data.get("recipient")
        subject = email_data.get("subject")
        body = email_data.get("body")

        if not all([recipient, subject, body]):
            return f"❌ Error: Missing 'recipient', 'subject', or 'body' in the parsed data: {email_data}"

        creds = get_google_credentials()
        if not creds:
            return "❌ Google services not authorized. Please visit /authorize to authenticate first."
        
        service = build("gmail", "v1", credentials=creds)
        
        message = MIMEText(body)
        message["to"] = recipient
        message["subject"] = subject
        
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        send_message = {"raw": raw}
        
        result = service.users().messages().send(userId="me", body=send_message).execute()
        return f"✅ Email successfully sent to {recipient} with subject '{subject}'"
        
    except json.JSONDecodeError:
        return f"❌ Error: Failed to decode the JSON string from the agent's input. The tool received: {action_input}"
    except Exception as e:
        return f"❌ An unexpected error occurred in the send_email tool: {str(e)}"

@tool
def create_calendar_event(action_input) -> str:
    """
    Create a calendar event using Google Calendar API. The action_input should be a
    JSON string containing event details ('title', 'start_datetime', 'end_datetime', 'description', 'location').
    DateTime format should be ISO format like '2024-01-15T10:00:00' or '2024-01-15T10:00:00-05:00'
    """
    try:
        event_data = json.loads(action_input)
        
        title = event_data.get("title")
        start_datetime = event_data.get("start_datetime")
        end_datetime = event_data.get("end_datetime")
        description = event_data.get("description", "")
        location = event_data.get("location", "")

        if not all([title, start_datetime, end_datetime]):
            return f"❌ Error: Missing required fields 'title', 'start_datetime', or 'end_datetime' in: {event_data}"

        creds = get_google_credentials()
        if not creds:
            return "❌ Google services not authorized. Please visit /authorize to authenticate first."
        
        service = build("calendar", "v3", credentials=creds)
        
        # Parse datetime strings and ensure timezone info
        try:
            if 'T' not in start_datetime:
                start_datetime += 'T00:00:00'
            if 'T' not in end_datetime:
                end_datetime += 'T23:59:59'
            
            # If no timezone specified, assume local timezone
            if '+' not in start_datetime and 'Z' not in start_datetime and start_datetime.count(':') == 2:
                start_datetime += '+00:00'  # You might want to use user's timezone here
            if '+' not in end_datetime and 'Z' not in end_datetime and end_datetime.count(':') == 2:
                end_datetime += '+00:00'
                
        except Exception as dt_error:
            return f"❌ Error parsing datetime format. Use ISO format like '2024-01-15T10:00:00' or '2024-01-15T10:00:00+05:30': {dt_error}"

        event = {
            'summary': title,
            'description': description,
            'location': location,
            'start': {
                'dateTime': start_datetime,
            },
            'end': {
                'dateTime': end_datetime,
            },
        }
        
        created_event = service.events().insert(calendarId='primary', body=event).execute()
        
        return f"✅ Calendar event '{title}' created successfully! Event ID: {created_event.get('id')}\n📅 Start: {start_datetime}\n📅 End: {end_datetime}"
        
    except json.JSONDecodeError:
        return f"❌ Error: Failed to decode the JSON string from the agent's input. The tool received: {action_input}"
    except Exception as e:
        return f"❌ An unexpected error occurred while creating calendar event: {str(e)}"

@tool
def get_calendar_events(action_input) -> str:
    """
    Get upcoming calendar events. The action_input should be a JSON string with optional parameters:
    - 'days_ahead': number of days to look ahead (default: 7)
    - 'max_results': maximum number of events to return (default: 10)
    """
    try:
        if action_input.strip():
            params = json.loads(action_input)
        else:
            params = {}
        
        days_ahead = params.get("days_ahead", 7)
        max_results = params.get("max_results", 10)

        creds = get_google_credentials()
        if not creds:
            return "❌ Google services not authorized. Please visit /authorize to authenticate first."
        
        service = build("calendar", "v3", credentials=creds)
        
        # Get events from now to specified days ahead
        now = datetime.utcnow()
        time_max = now + timedelta(days=days_ahead)
        
        events_result = service.events().list(
            calendarId='primary',
            timeMin=now.isoformat() + 'Z',
            timeMax=time_max.isoformat() + 'Z',
            maxResults=max_results,
            singleEvents=True,
            orderBy='startTime'
        ).execute()
        
        events = events_result.get('items', [])
        
        if not events:
            return f"📅 No upcoming events found in the next {days_ahead} days."
        
        event_list = []
        for event in events:
            start = event['start'].get('dateTime', event['start'].get('date'))
            title = event.get('summary', 'No title')
            location = event.get('location', '')
            description = event.get('description', '')
            
            # Format the start time nicely
            try:
                if 'T' in start:
                    dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                    formatted_time = dt.strftime('%Y-%m-%d %I:%M %p')
                else:
                    formatted_time = start + " (All day)"
            except:
                formatted_time = start
            
            event_info = f"📅 {title}\n   🕐 {formatted_time}"
            if location:
                event_info += f"\n   📍 {location}"
            if description:
                event_info += f"\n   📝 {description[:100]}{'...' if len(description) > 100 else ''}"
            
            event_list.append(event_info)
        
        return f"📅 Upcoming Events ({len(events)} found):\n\n" + "\n\n".join(event_list)
        
    except json.JSONDecodeError:
        return f"❌ Error: Failed to decode the JSON string. The tool received: {action_input}"
    except Exception as e:
        return f"❌ An unexpected error occurred while fetching calendar events: {str(e)}"

@tool
def delete_calendar_event(action_input) -> str:
    """
    Delete a calendar event by its ID or title. The action_input should be a JSON string containing:
    - 'event_id': The Google Calendar event ID, OR
    - 'title': The title of the event to search for and delete (will delete the first match)
    """
    try:
        params = json.loads(action_input)
        
        event_id = params.get("event_id")
        title = params.get("title")

        if not event_id and not title:
            return "❌ Error: Must provide either 'event_id' or 'title' to delete an event"

        creds = get_google_credentials()
        if not creds:
            return "❌ Google services not authorized. Please visit /authorize to authenticate first."
        
        service = build("calendar", "v3", credentials=creds)
        
        # If title provided but no event_id, search for the event
        if not event_id and title:
            # Search for events with matching title
            now = datetime.utcnow()
            future = now + timedelta(days=365)  # Search in next year
            
            events_result = service.events().list(
                calendarId='primary',
                timeMin=now.isoformat() + 'Z',
                timeMax=future.isoformat() + 'Z',
                q=title,
                singleEvents=True
            ).execute()
            
            events = events_result.get('items', [])
            matching_events = [e for e in events if title.lower() in e.get('summary', '').lower()]
            
            if not matching_events:
                return f"❌ No events found with title containing '{title}'"
            
            event_id = matching_events[0]['id']
            actual_title = matching_events[0].get('summary', 'No title')
        else:
            actual_title = "Event"
        
        # Delete the event
        service.events().delete(calendarId='primary', eventId=event_id).execute()
        
        return f"✅ Successfully deleted calendar event: {actual_title}"
        
    except json.JSONDecodeError:
        return f"❌ Error: Failed to decode the JSON string. The tool received: {action_input}"
    except Exception as e:
        return f"❌ An unexpected error occurred while deleting calendar event: {str(e)}"
@tool
def get_instagram_profile() -> str:
    """Get Instagram user profile information"""
    try:
        access_token = session.get("instagram_access_token")
        instagram_user_id = session.get("instagram_user_id")

        if not access_token or not instagram_user_id:
            return "❌ Instagram not authorized. Please visit /instagram/authorize to authenticate first."
        
        url = f"{INSTAGRAM_API_BASE}/{instagram_user_id}"
        params = {
            'fields': 'username,media_count,followers_count,follows_count,biography',
            'access_token': access_token
        }
        
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        return (f"📱 Instagram Profile:\n"
                f"👤 Username: {data.get('username')}\n"
                f"📸 Media Count: {data.get('media_count')}\n"
                f"👥 Followers: {data.get('followers_count')}\n"
                f"bio: {data.get('biography')}")
        
    except Exception as e:
        return f"❌ Error fetching Instagram profile: {str(e)}"

@tool
def get_instagram_media(action_input) -> str:
    """Get Instagram media posts"""
    try:
        if action_input.strip():
            params = json.loads(action_input)
        else:
            params = {}
        
        limit = min(params.get("limit", 10), 25)
        
        access_token = session.get("instagram_access_token")
        instagram_user_id = session.get("instagram_user_id")

        if not access_token or not instagram_user_id:
            return "❌ Instagram not authorized. Please visit /instagram/authorize to authenticate first."
        
        url = f"{INSTAGRAM_API_BASE}/{instagram_user_id}/media"
        request_params = {
            'fields': 'caption,media_type,media_url,timestamp,like_count,comments_count',
            'limit': limit,
            'access_token': access_token
        }
        
        response = requests.get(url, params=request_params)
        response.raise_for_status()
        data = response.json()
        
        if not data.get('data'):
            return "📸 No Instagram media found."
        
        media_list = []
        for media in data['data']:
            media_type = media.get('media_type', 'UNKNOWN')
            caption = media.get('caption', 'No caption')[:100]
            likes = media.get('like_count', 0)
            media_list.append(f"📸 {media_type}: {caption} (❤️ {likes})")
        
        return f"📱 Instagram Media ({len(media_list)} posts):\n\n" + "\n".join(media_list)
        
    except Exception as e:
        return f"❌ Error fetching Instagram media: {str(e)}"

@tool
def post_to_instagram(action_input) -> str:
    """
    Posts a photo to Instagram. The action_input must be a JSON string containing an 'image_url' and a 'caption'.
    The image_url must be a direct link to a publicly accessible JPG or PNG image.
    """
    try:
        post_data = json.loads(action_input)
        image_url = post_data.get("image_url")
        caption = post_data.get("caption")

        if not image_url or not caption:
            return "❌ Error: Missing 'image_url' or 'caption' in the input."

        access_token = session.get("instagram_access_token")
        instagram_user_id = session.get("instagram_user_id")

        if not access_token or not instagram_user_id:
            return "❌ Instagram not authorized. Please visit /instagram/authorize to authenticate first."

        # --- Step 1: Create a media container ---
        print("🔄 Step 1: Creating Instagram media container...")
        container_url = f"{INSTAGRAM_API_BASE}/{instagram_user_id}/media"
        container_params = {
            'image_url': image_url,
            'caption': caption,
            'access_token': access_token
        }
        container_response = requests.post(container_url, params=container_params)
        container_response.raise_for_status() # Will raise an error for bad responses
        container_data = container_response.json()
        creation_id = container_data.get('id')

        if not creation_id:
            return f"❌ Error: Failed to create media container. Response: {container_data}"
        print(f"✅ Container created with ID: {creation_id}")

        # --- Step 2: Publish the media container ---
        print("🔄 Step 2: Publishing the container...")
        publish_url = f"{INSTAGRAM_API_BASE}/{instagram_user_id}/media_publish"
        publish_params = {
            'creation_id': creation_id,
            'access_token': access_token
        }
        publish_response = requests.post(publish_url, params=publish_params)
        publish_response.raise_for_status()
        
        return f"✅ Successfully posted the image to Instagram with the caption: '{caption}'"

    except json.JSONDecodeError:
        return f"❌ Error: Failed to decode the JSON string from the agent's input. Received: {action_input}"
    except requests.exceptions.HTTPError as http_err:
        # Try to get more specific error info from the response
        error_details = http_err.response.json().get('error', {})
        error_message = error_details.get('message', 'No specific message.')
        return f"❌ An HTTP error occurred: {error_message}"
    except Exception as e:
        return f"❌ An unexpected error occurred in the post_to_instagram tool: {str(e)}"

# Initialize tools - Add the new calendar tools
search_tool = DuckDuckGoSearchRun()
tools = [search_tool, get_weather_data, get_today_date, send_email, create_calendar_event, get_calendar_events, delete_calendar_event, get_instagram_profile, get_instagram_media, post_to_instagram]
# ==============================================
# LLM SETUP
# ==============================================
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.4)
print("✅ LLM initialized")

# ==============================================
# MEMORY SETUP
# ==============================================
user_conversations = {}

def get_session_history(session_id: str) -> ChatMessageHistory:
    if session_id not in user_conversations:
        user_conversations[session_id] = ChatMessageHistory()
        print(f"🆕 Created new conversation history for session: {session_id}")
    return user_conversations[session_id]

# ==============================================
# AGENT SETUP
# ==============================================
react_prompt = hub.pull("hwchase17/react")

memory_aware_prompt = PromptTemplate.from_template(
    """You are a helpful assistant with access to tools for searching the web, 
    getting weather info, sending emails, managing calendar events, and fetching dates.

Previous conversation history:
{chat_history}

You have access to the following tools:
{tools}

IMPORTANT INSTRUCTIONS:
- If the user input is casual conversation (like "hi", "hello", "how are you", "thanks", etc.), respond politely in natural language without using any tools.
- Only use tools if the query explicitly requires information or actions (like sending an email, searching the web, calendar operations, or fetching weather/date).

FOR EMAIL OPERATIONS:
- When using the `send_email` tool, provide Action Input as a valid JSON object with: "recipient", "subject", and "body".

FOR CALENDAR OPERATIONS:
- When using `create_calendar_event`, provide JSON with: "title", "start_datetime", "end_datetime", and optionally "description", "location"
- DateTime format: Use ISO format like '2024-01-15T10:00:00' or '2024-01-15T14:30:00+05:30'
- When using `get_calendar_events`, provide JSON with optional: "days_ahead" (default: 7), "max_results" (default: 10)
- When using `delete_calendar_event`, provide JSON with either "event_id" or "title"

✅ Correct Examples:
Action: send_email
Action Input: {{"recipient": "test@example.com", "subject": "Meeting Update", "body": "The meeting is rescheduled to 3 PM"}}

Action: create_calendar_event
Action Input: {{"title": "Team Meeting", "start_datetime": "2024-01-15T10:00:00", "end_datetime": "2024-01-15T11:00:00", "description": "Weekly team sync", "location": "Conference Room A"}}

Action: get_calendar_events
Action Input: {{"days_ahead": 7, "max_results": 5}}

Action: delete_calendar_event
Action Input: {{"title": "Old Meeting"}}

If the user doesn't provide required information, ask for clarification first.

FOR INSTAGRAM OPERATIONS:
- When using `get_instagram_profile`, no parameters needed
- When using `get_instagram_media`, provide JSON with optional: "limit" (default: 10, max: 25)
- When using `post_to_instagram`, provide JSON with "image_url" and "caption"

✅ Instagram Examples:
Action: get_instagram_profile
Action Input: 

Action: get_instagram_media
Action Input: {{"limit": 5}}

Action: post_to_instagram
Action Input: {{"image_url": "https://some-public-image.com/photo.jpg", "caption": "This is a test post!"}}

Use this format for reasoning and actions:

Question: the input question
Thought: reasoning about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: input to the action
Observation: result of the action
... (repeat as needed)
Thought: I now know the final answer
Final Answer: the final answer to the user

Begin!

Question: {input}
Thought: {agent_scratchpad}"""
)
agent = create_react_agent(llm, tools, memory_aware_prompt)

agent_executor = AgentExecutor(
    agent=agent,
    tools=tools,
    verbose=True,
    handle_parsing_errors=True,
    max_iterations=20,
    early_stopping_method="generate"
)

agent_with_chat_history = RunnableWithMessageHistory(
    agent_executor,
    get_session_history,
    input_messages_key="input",
    history_messages_key="chat_history",
)

print("✅ Agent with memory initialized")

# ==============================================
# SAFE INVOKE (Fallback)
# ==============================================
def safe_invoke(agent_with_chat_history, inputs, config):
    try:
        return agent_with_chat_history.invoke(inputs, config=config)
    except Exception as e:
        if "iteration limit" in str(e).lower():
            print("⚠️ Iteration limit hit, falling back to plain LLM.")
            return {"output": llm.invoke(inputs["input"]).content}
        print(f"An unexpected error occurred: {e}")
        raise

# ==============================================
# HELPERS
# ==============================================
def format_chat_history(history: ChatMessageHistory) -> str:
    if not history.messages:
        return "No previous conversation."
    formatted_history = []
    for message in history.messages[-10:]:
        if hasattr(message, 'type'):
            if message.type == 'human':
                formatted_history.append(f"Human: {message.content}")
            elif message.type == 'ai':
                formatted_history.append(f"Assistant: {message.content}")
    return "\n".join(formatted_history) if formatted_history else "No previous conversation."

# ==============================================
# ROUTES
# ==============================================
@app.route("/", methods=["GET", "POST"])
def home():
    if "session_id" not in session:
        session["session_id"] = os.urandom(24).hex()
        print(f"🆕 Created new session: {session['session_id']}")

    session_id = session["session_id"]
    response = None

    if request.method == "POST":
        user_input = request.form["user_input"]
        print(f"\n🗣️ [{session_id}] User: {user_input}")

        try:
            history = get_session_history(session_id)
            chat_history = format_chat_history(history)
            config = {"configurable": {"session_id": session_id}}

            result = safe_invoke(agent_with_chat_history, {
                "input": user_input,
                "chat_history": chat_history
            }, config=config)

            response = result["output"]
            print(f"✅ [{session_id}] Agent Response: {response}")

        except Exception as e:
            response = f"❌ Error: {str(e)}"
            print(response)

    conversation_history = (
        user_conversations[session_id].messages[-6:]
        if session_id in user_conversations else []
    )

    return render_template(
        "index.html",
        response=response,
        conversation_history=conversation_history,
        session_id=session_id,
    )

@app.route("/clear", methods=["POST"])
def clear_conversation():
    if "session_id" in session:
        session_id = session["session_id"]
        if session_id in user_conversations:
            user_conversations[session_id].clear()
            print(f"🗑️ Cleared history for session: {session_id}")
    return redirect(url_for('home'))

@app.route("/authorize")
def authorize():
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,  # Now includes both Gmail and Calendar scopes
        redirect_uri=url_for("oauth2callback", _external=True),
    )
    
    authorization_url, state = flow.authorization_url(
        access_type="offline", 
        include_granted_scopes="true",
        prompt='consent'
    )
    
    session["state"] = state
    return redirect(authorization_url)

@app.route("/oauth2callback")
def oauth2callback():
    state = session["state"]
    flow = Flow.from_client_secrets_file(
        CLIENT_SECRETS_FILE,
        scopes=SCOPES,
        state=state,
        redirect_uri=url_for("oauth2callback", _external=True),
    )
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials
    session["credentials"] = json.loads(creds.to_json())
    print("✅ Google authorization successful (Gmail + Calendar), credentials saved")
    return redirect(url_for("home"))

@app.route("/status")
def google_status():
    creds = get_google_credentials()
    if creds:
        return {
            "status": "authorized", 
            "message": "Google services are connected and ready",
            "services": ["Gmail", "Calendar"]
        }
    else:
        return {
            "status": "not_authorized", 
            "message": "Google services not authorized. Visit /authorize to connect",
            "services": []
        }

# Keep the old endpoint for backward compatibility
@app.route("/gmail-status")
def gmail_status():
    return google_status()
@app.route("/instagram/authorize")
def instagram_authorize():
    # Scopes needed for the Graph API to get profile and media
    scopes = "instagram_basic,pages_show_list,instagram_manage_insights,pages_read_engagement,instagram_content_publish"
    auth_url = f"{INSTAGRAM_AUTH_URL}?client_id={INSTAGRAM_CLIENT_ID}&redirect_uri={INSTAGRAM_REDIRECT_URI}&scope={scopes}&response_type=code"
    return redirect(auth_url)

@app.route("/instagram/callback")
def instagram_callback():
    print("\n--- INSTAGRAM CALLBACK INITIATED ---")
    
    # This is to catch any high-level exceptions
    try:
        code = request.args.get('code')
        if not code:
            print("❌ ERROR: No authorization code received from Facebook.")
            return "Authorization failed: No code provided.", 400
        
        print(f"✅ Received authorization code starting with: {code[:30]}...")

        # --- Step 1: Exchange code for a short-lived user access token ---
        token_data = {
            'client_id': INSTAGRAM_CLIENT_ID,
            'client_secret': INSTAGRAM_CLIENT_SECRET,
            'redirect_uri': INSTAGRAM_REDIRECT_URI,
            'code': code
        }
        
        print("🔄 Exchanging code for access token...")
        print(f"POSTing to URL: {INSTAGRAM_TOKEN_URL}")
        
        response = requests.post(INSTAGRAM_TOKEN_URL, data=token_data)
        print(f"✅ Token exchange response status code: {response.status_code}")
        
        token_response = response.json()
        print(f"✅ Token exchange response JSON: {token_response}")

        if 'access_token' not in token_response:
            print(f"❌ ERROR: 'access_token' not in response. Full response: {token_response}")
            return f"Token exchange failed: {token_response}", 400
        
        user_access_token = token_response['access_token']
        print("✅ Successfully obtained user access token.")

        # --- Step 2: Get the user's Facebook Pages ---
        print("🔄 Fetching user's Facebook pages...")
        pages_url = f"{INSTAGRAM_API_BASE}/me/accounts"
        pages_params = {'access_token': user_access_token}
        pages_response = requests.get(pages_url, params=pages_params).json()
        print(f"✅ Pages response JSON: {pages_response}")

        if not pages_response.get('data'):
            print("❌ ERROR: No Facebook Pages found for this user.")
            return "Error: No Facebook Pages found for this user. Please ensure you have a Page linked to your Instagram account.", 400
        
        print(f"✅ Found {len(pages_response.get('data', []))} Facebook page(s).")

        # --- Step 3: Find the Page linked to an Instagram account and get the IG User ID ---
        print("🔄 Searching for linked Instagram account...")
        page_id = None
        instagram_id = None
        for page in pages_response['data']:
            page_id_to_check = page['id']
            print(f"   - Checking Page ID: {page_id_to_check}")
            ig_url = f"{INSTAGRAM_API_BASE}/{page_id_to_check}"
            ig_params = {
                'fields': 'instagram_business_account',
                'access_token': user_access_token
            }
            ig_response = requests.get(ig_url, params=ig_params).json()
            print(f"   - Instagram account check response: {ig_response}")
            if 'instagram_business_account' in ig_response:
                page_id = page_id_to_check
                instagram_id = ig_response['instagram_business_account']['id']
                print(f"✅ Found linked Instagram Business Account ID: {instagram_id}")
                break
                
        if not instagram_id:
            print("❌ ERROR: Could not find an Instagram Business Account linked to any Facebook Pages.")
            return "Error: Could not find an Instagram Business Account linked to any of your Facebook Pages.", 400

        # --- Step 4: Store the necessary info in the session ---
        session['instagram_access_token'] = user_access_token
        session['instagram_user_id'] = instagram_id
        print("✅ Storing credentials in session and redirecting to home.")
        print("--- INSTAGRAM CALLBACK COMPLETED SUCCESSFULLY ---\n")
        return redirect(url_for('home'))

    except Exception as e:
        # This will catch any unexpected errors and print them
        print(f"💥 AN UNEXPECTED ERROR OCCURRED: {e}")
        import traceback
        traceback.print_exc()
        return f"An unexpected error occurred during the callback process: {e}", 500
@app.route("/instagram/status")
def instagram_status():
    token = get_instagram_credentials()
    if token:
        return {
            "status": "authorized",
            "message": "Instagram is connected and ready"
        }
    else:
        return {
            "status": "not_authorized",
            "message": "Instagram not authorized. Visit /instagram/authorize to connect"
        }
# ==============================================
# MAIN
# ==============================================
if __name__ == "__main__":
    app.run(host='0.0.0.0', debug=True)