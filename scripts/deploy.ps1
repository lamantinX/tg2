Write-Host "🚀 Starting deployment of tg2..." -ForegroundColor Cyan

# Check if .env exists
if (-not (Test-Path ".env")) {
    Write-Host "⚠️  .env file not found! Copying from .env.example..." -ForegroundColor Yellow
    Copy-Item ".env.example" ".env"
    Write-Host "❗ Please edit .env with your production credentials." -ForegroundColor Red
    exit
}

# Pull latest changes (if in git)
if (Test-Path ".git") {
    Write-Host "📥 Pulling latest changes from git..." -ForegroundColor Cyan
    git pull
}

# Build and restart containers
Write-Host "🏗️  Building and starting containers..." -ForegroundColor Cyan
docker-compose up -d --build

# Clean up old images
Write-Host "🧹 Cleaning up unused Docker images..." -ForegroundColor Cyan
docker image prune -f

Write-Host "✅ Deployment finished successfully!" -ForegroundColor Green
Write-Host "📡 API is running on port 8000"
Write-Host "🤖 Bot worker is running in the background"
Write-Host "📄 Check logs with: docker-compose logs -f"
