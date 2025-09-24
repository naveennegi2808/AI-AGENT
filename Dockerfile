# Start with an official Python runtime as a parent image
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code into the container at /app
COPY . .

# Make port 8000 available to the world outside this container
# (If your app runs on a different port, change it here)
EXPOSE 8000

# Define environment variables if needed (can also be passed at runtime)
# ENV NAME World

# Run app.py when the container launches
# Use gunicorn for production deployments for a more robust server
# CMD ["gunicorn", "--bind", "0.0.0.0:8000", "app:app"]
# For development/simpler cases, you can use:
CMD ["python", "app.py"]