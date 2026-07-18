#!/bin/bash
# Railway Start Script

echo "🚀 Starting MEME HUNTER Bot..."
echo "================================"

# Paper mode is safe by default. A private key is required only for live mode.
PAPER_TRADING="${PAPER_TRADING:-true}"
if [ "$PAPER_TRADING" = "false" ] && [ -z "$WALLET_PRIVATE_KEY" ]; then
    echo "❌ ERROR: WALLET_PRIVATE_KEY is required when PAPER_TRADING=false"
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
