#!/bin/bash

# Configuration
APP_NAME="tg2"
DOCKER_COMPOSE_FILE="docker-compose.yml"

echo "🚀 Starting deployment of $APP_NAME..."

# Check if .env exists
if [ ! -f .env ]; then
    echo "⚠️  .env file not found! Copying from .env.example..."
    cp .env.example .env
    echo "❗ Please edit .env with your production credentials."
    exit 1
fi

# Pull latest changes (if in git)
if [ -d .git ]; then
    echo "📥 Pulling latest changes from git..."
    git pull
fi

# Build and restart containers
echo "🏗️  Building and starting containers..."
docker-compose -f $DOCKER_COMPOSE_FILE up -d --build

# Clean up old images
echo "🧹 Cleaning up unused Docker images..."
docker image prune -f

echo "✅ Deployment finished successfully!"
echo "📡 API is running on port 8000"
echo "🤖 Bot worker is running in the background"
echo "📄 Check logs with: docker-compose logs -f"
