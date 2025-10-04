# Start with an official Python runtime as a parent image
FROM python:3.11-slim

# Install system dependencies needed for Chrome and webdriver-manager
RUN apt-get update && apt-get install -y \
    curl \
    unzip \
    libglib2.0-0 \
    libnss3 \
    libgconf-2-4 \
    libfontconfig1 \
    --no-install-recommends

# Download and install the latest stable version of Google Chrome
RUN curl -sS -o - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google-chrome.list \
    && apt-get -y update \
    && apt-get -y install google-chrome-stable

# Set the working directory
WORKDIR /app

# Copy and install requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application
COPY . .

# Expose the port Render will use
EXPOSE 10000

# Run the application
CMD ["gunicorn", "--bind", "0.0.0.0:$PORT", "app:app"]