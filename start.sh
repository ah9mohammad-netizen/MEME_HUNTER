#!/bin/bash
# Railway Start Script

echo "🚀 Starting MEME HUNTER Bot..."
echo "================================"

# Check for required environment variables
if [ -z "$WALLET_PRIVATE_KEY" ]; then
    echo "❌ ERROR: WALLET_PRIVATE_KEY not set"
    exit 1
fi

if [ -z "$TELEGRAM_BOT_TOKEN" ]; then
    echo "❌ ERROR: TELEGRAM_BOT_TOKEN not set"
    exit 1
fi

if [ -z "$TELEGRAM_CHAT_ID" ]; then
    echo "❌ ERROR: TELEGRAM_CHAT_ID not set"
    exit 1
fi

echo "✅ Configuration validated"

# Set Python path
export PYTHONPATH=/app:$PYTHONPATH

# Run the bot
echo "🐍 Starting Python bot..."
python meme_hunter_bot.py
