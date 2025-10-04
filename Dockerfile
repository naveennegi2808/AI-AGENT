# Start with an official Python runtime as a parent image
FROM python:3.11-slim

# Set an environment variable to prevent interactive prompts during package installation
ENV DEBIAN_FRONTEND=noninteractive

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

# Set the working directory
WORKDIR /app

# Copy and install Python requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose the port Render will use
EXPOSE 10000

# Run the application
CMD ["gunicorn", "--bind", "0.0.0.0:$PORT", "app:app"]