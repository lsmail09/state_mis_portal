# Use the official Python image
FROM python:3.14-slim

# Install system dependencies and tools needed to download keys safely
RUN apt-get update && apt-get install -y \
    build-essential \
    unixodbc-dev \
    curl \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Fix: Securely download the Microsoft GPG key and add the proper repository list
RUN curl -fsSL https://packages.microsoft.com/keys/microsoft.asc | gpg --dearmor -o /usr/share/keyrings/microsoft-prod.gpg \
    && curl -fsSL https://packages.microsoft.com/config/debian/12/prod.list | tee /etc/apt/sources.list.d/mssql-release.list

# Update package lists and install the MS SQL Server ODBC driver
RUN apt-get update && ACCEPT_EULA=Y apt-get install -y msodbcsql18

# Set up the application directory
WORKDIR /app
COPY . /app

# Install Python packages
RUN pip install --no-cache-dir -r requirements.txt

# Expose Streamlit's default port
EXPOSE 8501

# Run the Streamlit application
CMD ["streamlit", "run", "state_officer_portal.py", "--server.port=8501", "--server.address=0.0.0.0"]
