import os
import json
from google.oauth2 import service_account
import json
from werkzeug.middleware.proxy_fix import ProxyFix
import requests
import pathlib
import time
import base64
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from flask import Flask, render_template, request, session, redirect, url_for
from dotenv import load_dotenv
from authlib.integrations.flask_client import OAuth
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

# Flask setup
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1) 
app.secret_key = os.getenv("FLASK_SECRET_KEY", "a-truly-secret-key-for-production")

# LinkedIn OAuth Setup
# Explanation: This configures Authlib to handle the LinkedIn OAuth2 flow.
# We define the URLs and the permissions (scopes) our app needs.
oauth = OAuth(app)

linkedin = oauth.register(
    name='linkedin',
    client_id=os.getenv("LI_CLIENT_ID"),
    client_secret=os.getenv("LI_CLIENT_SECRET"),
    access_token_url='https://www.linkedin.com/oauth/v2/accessToken',
    access_token_params=None,
    authorize_url='https://www.linkedin.com/oauth/v2/authorization',
    api_base_url='https://api.linkedin.com/v2/',
    client_kwargs={'scope': 'openid profile email w_member_social'},
    # This next line is the critical fix.
    # It explicitly tells authlib to send the secret in the request body.
    client_auth_method='client_secret_post')


print("✅ LinkedIn OAuth client initialized")


# SCOPES to include Calendar
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/calendar"
]

def get_linkedin_credentials():
    """Gets the current user's LinkedIn credentials from their session."""
    # Note: We are not handling token refresh here for simplicity,
    # as LinkedIn tokens are long-lived. A full production app would add refresh logic.
    if "linkedin_token" not in session:
        return None, None
    
    token_data = session["linkedin_token"]
    user_data = session["linkedin_user"]
    
    # We pass back the whole token object for the API calls
    return token_data, user_data

def post_to_linkedin_api(access_token: str, author_urn: str, text: str, asset_urn: str = None) -> dict:
    """Makes the API call to create a post on LinkedIn using the LEGACY v2 API."""
    url = "https://api.linkedin.com/v2/ugcPosts"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }
    body = {
        "author": author_urn,
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {
                    "text": text
                },
                "shareMediaCategory": "NONE"
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        }
    }

    if asset_urn:
        body["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"] = "IMAGE"
        body["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [
            {
                "status": "READY",
                "media": asset_urn
            }
        ]
    
    response = requests.post(url, headers=headers, json=body)
    
    if response.status_code != 201:
        print(f"❌ LinkedIn API Error. Status: {response.status_code}, Body: {response.text}")
    response.raise_for_status()
    
    post_urn = response.json().get('id')
    print(f"✅ Successfully posted to LinkedIn! Post URN: {post_urn}")
    return {"urn": post_urn, "data": response.json()}

def register_linkedin_image_upload(access_token: str, author_urn: str) -> dict:
    """Step 1: Tells LinkedIn we want to upload an image using the v2 API and correctly parses the response."""
    url = "https://api.linkedin.com/v2/assets?action=registerUpload"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }
    body = {
        "registerUploadRequest": {
            "owner": author_urn,
            "recipes": ["urn:li:digitalmediaRecipe:feedshare-image"],
            "serviceRelationships": [{"relationshipType": "OWNER", "identifier": "urn:li:userGeneratedContent"}]
        }
    }
    
    response = requests.post(url, headers=headers, json=body)
    response.raise_for_status()
    data = response.json()
    
    # --- THIS IS THE FINAL FIX ---
    # The entire response is the upload_info object.
    upload_info = data.get('value', {})
    
    try:
        # Correctly navigate the nested dictionary to find the uploadUrl
        upload_url = upload_info['uploadMechanism']['com.linkedin.digitalmedia.uploading.MediaUploadHttpRequest']['uploadUrl']
    except KeyError:
        raise Exception(f"Could not find the nested 'uploadUrl' in the v2 API response. Full Response: {upload_info}")

    # The 'asset' key is at the top level of the 'upload_info' dictionary
    asset_urn = upload_info.get('asset')
    # --- END OF FIX ---

    if not upload_url or not asset_urn:
        raise Exception(f"Critical error: Failed to extract 'uploadUrl' or 'asset' from v2 API response. Full Response: {upload_info}")
        
    print("✅ Successfully got Upload URL and Asset URN from legacy v2 endpoint.")
    return {"upload_url": upload_url, "asset_urn": asset_urn}

def upload_image_to_linkedin(upload_url: str, image_data: bytes, mimetype: str):
    """Step 2: Uploads the actual image bytes to the provided special URL."""
    headers = {'Content-Type': mimetype}
    response = requests.put(upload_url, headers=headers, data=image_data)
    response.raise_for_status()
    time.sleep(6)
    print("✅ Image bytes successfully uploaded to LinkedIn's server.")

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

            event_info = f"📅 {title}\n    🕐 {formatted_time}"
            if location:
                event_info += f"\n    📍 {location}"
            if description:
                event_info += f"\n    📝 {description[:100]}{'...' if len(description) > 100 else ''}"

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
def post_on_linkedin(action_input: str) -> str:
    """
    Use this tool to publish a post on LinkedIn using the current user's account.
    The action_input should be a JSON string like: '{"text": "Hello world!"}'.
    If the user has uploaded an image, it will be attached automatically.
    """
    try:
        # 1. Get credentials from the current user's session
        token, user = get_linkedin_credentials()
        if not token or not user:
            return "❌ LinkedIn account not connected. Please use the 'Connect' button first."

        access_token = token['access_token']
        author_urn = user['urn']

        # 2. Parse input and get asset from session
        post_data = json.loads(action_input)
        text = post_data.get("text")
        if not text:
            return "❌ Error: The JSON input must contain a 'text' field."
        
        asset_urn = session.get('uploaded_linkedin_asset_urn', None)
        
        # 3. Call the API
        result = post_to_linkedin_api(access_token, author_urn, text, asset_urn=asset_urn)
        
        # 4. Clear the asset from the session after use
        if asset_urn:
            session.pop('uploaded_linkedin_asset_urn', None)
        
        return f"✅ Successfully posted to LinkedIn on your behalf! Post URN: {result['urn']}"

    except json.JSONDecodeError:
        return f"❌ Error: Invalid JSON format. Please provide input like: {{\"text\": \"Your content\"}}"
    except Exception as e:
        return f"❌ An unexpected error occurred: {str(e)}"
    
# Initialize tools
search = DuckDuckGoSearchRun()

@tool
def search_the_web(query: str) -> str:
    """
    Use this tool to search the internet for general knowledge, facts, definitions,
    or information about places. For example, use it to find the capital of a state or country.
    """
    return search.run(query)
tools = [search_the_web, get_weather_data, get_today_date, send_email, create_calendar_event, get_calendar_events, delete_calendar_event,post_on_linkedin]

# ==============================================
# LLM SETUP
# ==============================================
# ===CODE FOR RENDER DEPLOYMENT ===
credentials_json_str = os.getenv("GOOGLE_CREDENTIALS_JSON")
if not credentials_json_str:
    raise ValueError("The GOOGLE_CREDENTIALS_JSON environment variable is not set.")

credentials_info = json.loads(credentials_json_str)
credentials = service_account.Credentials.from_service_account_info(credentials_info)
# ======================================
llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.4, credentials=credentials)
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

FOR LINKEDIN OPERATIONS:
- When using `post_on_linkedin`, provide Action Input as a valid JSON object with: "text".

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
    max_iterations=8,
    early_stopping_method="force"
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

    session_id = session["session_id"]
    response = None

    if request.method == "POST":
        user_input = request.form["user_input"]
        config = {"configurable": {"session_id": session_id}}
        try:
            result = agent_with_chat_history.invoke({"input": user_input}, config=config)
            response = result["output"]
        except Exception as e:
            response = f"❌ Error: {str(e)}"

    conversation_history = get_session_history(session_id).messages[-10:]
     
    # Check if an image is ready in the session and tell the template
    image_ready_in_session = 'uploaded_linkedin_asset_urn' in session

    image_is_ready = 'uploaded_linkedin_asset_urn' in session

    return render_template(
    "index.html",
    response=response,
    conversation_history=conversation_history,
    session_id=session_id,
    image_is_ready=image_is_ready  # Add this line
)

@app.route('/clear_linkedin_image', methods=['POST'])
def clear_linkedin_image():
    session.pop('uploaded_linkedin_asset_urn', None)
    return redirect(url_for('home'))

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
    client_secrets_str = os.getenv("OAUTH_CLIENT_SECRETS_JSON")
    if not client_secrets_str:
        raise ValueError("OAUTH_CLIENT_SECRETS_JSON environment variable not set.")
    client_config = json.loads(client_secrets_str)

    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
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
    client_secrets_str = os.getenv("OAUTH_CLIENT_SECRETS_JSON")
    if not client_secrets_str:
       raise ValueError("OAUTH_CLIENT_SECRETS_JSON environment variable not set.")
    client_config = json.loads(client_secrets_str)
    
    # THIS LINE IS WRONG
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        state=state,
        redirect_uri=url_for("oauth2callback", _external=True),
    )
    ...

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



@app.route("/gmail-status")
def gmail_status():
    return google_status()

@app.route('/auth/linkedin/start')
def linkedin_start_auth():
    """Redirects the user to LinkedIn for authentication."""
    redirect_uri = url_for('linkedin_callback', _external=True)
    return linkedin.authorize_redirect(redirect_uri)

@app.route('/auth/linkedin/callback')
def linkedin_callback():
    try:
        # Step 1: Manually get the authorization code from the URL
        code = request.args.get('code')
        if not code:
            return "Authentication failed: No code returned from LinkedIn.", 400

        # Step 2: Manually prepare the request to exchange the code for a token
        token_url = 'https://www.linkedin.com/oauth/v2/accessToken'
        params = {
            'grant_type': 'authorization_code',
            'code': code,
            'redirect_uri': url_for('linkedin_callback', _external=True),
            'client_id': os.getenv('LI_CLIENT_ID'),
            'client_secret': os.getenv('LI_CLIENT_SECRET')
        }

        # Step 3: Manually make the POST request, bypassing the broken library function
        response = requests.post(token_url, data=params)
        response.raise_for_status()
        token = response.json()

        # Step 4: Use the token to fetch the user's info
        user_info_resp = oauth.linkedin.get('userinfo', token=token)
        user_info_resp.raise_for_status()
        user_info = user_info_resp.json()

        # Step 5: Save the token and user data to the user's personal session
        session['linkedin_token'] = token
        session['linkedin_user'] = {
            'urn': f"urn:li:person:{user_info['sub']}",
            'name': f"{user_info.get('given_name', '')} {user_info.get('family_name', '')}".strip()
        }
        
        print("✅ LinkedIn authorization successful (manual exchange), credentials saved to session.")
        return redirect(url_for("home"))

    except Exception as e:
        error_details = e.response.json() if hasattr(e, 'response') else str(e)
        print(f"❌ LinkedIn OAuth callback error: {error_details}")
        return f"Authentication failed: {error_details}", 500
    
@app.route("/linkedin-status")
def linkedin_status():
    """Checks if the CURRENT user has a LinkedIn account connected in their session."""
    # Check for user info in the session, not the database
    if 'linkedin_user' in session:
        user_name = session['linkedin_user'].get('name', 'N/A')
        return {"status": "authorized", "name": user_name}
    else:
        return {"status": "not_authorized"}

@app.route('/upload/linkedin/image', methods=['POST'])
def upload_linkedin_image():
    if 'image' not in request.files:
        return {"error": "No image file provided"}, 400

    file = request.files['image']
    if file.filename == '':
        return {"error": "No selected file"}, 400
    
    try:
        # --- THIS IS THE FIX ---
        # 1. Get credentials from the current user's session
        token, user = get_linkedin_credentials()
        if not token or not user:
            return {"error": "LinkedIn account not connected. Please connect first."}, 401
        
        access_token = token['access_token']
        author_urn = user['urn']
        # --- END OF FIX ---

        # 2. Register the upload with LinkedIn
        upload_info = register_linkedin_image_upload(access_token, author_urn)
        
        # 3. Upload the image bytes to the special URL
        image_data = file.read()
        upload_image_to_linkedin(upload_info['upload_url'], image_data, file.mimetype)

        # 4. Save the final asset URN to the session for the agent to use
        session['uploaded_linkedin_asset_urn'] = upload_info['asset_urn']
        
        print(f"✅ Image uploaded. Asset URN {upload_info['asset_urn']} stored in session.")
        return {"success": True, "message": "Image uploaded and ready to be posted."}

    except Exception as e:
        print(f"❌ Image upload failed: {e}")
        return {"error": f"An unexpected error occurred: {str(e)}"}, 500

# ==============================================
# MAIN
# ==============================================
if __name__ == "__main__":
   app.run(host='0.0.0.0', port=8000, debug=True)
