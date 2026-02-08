#!/bin/bash

# Port to tunnel (default to 8501, but users might use 8502 etc)
PORT=8501

echo "🚀 Starting ngrok tunnel for port $PORT..."
echo "📋 Access the URL displayed below from your iPhone (Safari/Chrome)"
echo "---------------------------------------------------"

ngrok http $PORT
