# Use the official Python image
FROM python:3.14-slim

# Install system dependencies required for pyodbc and MS SQL
RUN apt-get update && apt-get install -y \
    build-essential \
    unixodbc-dev \
    curl \
    gnupg2 \
    && rm -rf /var/lib/apt/lists/*

# Add Microsoft SQL Server ODBC Driver repository and install it
RUN curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://microsoft.com > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y msodbcsql18

# Set up the application directory
WORKDIR /app
COPY . /app

# Install Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Expose Streamlit's default port
EXPOSE 8501

# Run the Streamlit application
CMD ["streamlit", "run", "state_officer_portal.py", "--server.port=8501", "--server.address=0.0.0.0"]