# Start with an official Python runtime as a parent image
FROM python:3.11-slim

# Set an environment variable to prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

# Copy requirements FIRST to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Update, install prerequisites, add Chrome repo, install Chrome, and clean up in one layer
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       gnupg \
       curl \
       unzip \
    && curl -sS -o - https://dl-ssl.google.com/linux/linux_signing_key.pub | gpg --dearmor -o /etc/apt/keyrings/google-chrome.gpg \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable --no-install-recommends \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*

# --- THIS IS THE FIX ---
# Pre-download and install the correct chromedriver during the build
# It will be automatically available in the system's PATH
RUN python -c "from webdriver_manager.chrome import ChromeDriverManager; ChromeDriverManager().install()"
# --- END OF FIX ---

# Set the working directory
WORKDIR /app

# Copy the rest of the application
COPY . .

# Expose the port Render will use
EXPOSE 10000

# Run the application using the shell form to parse the $PORT variable
CMD gunicorn --bind 0.0.0.0:$PORT --workers 1 --timeout 300 app:app