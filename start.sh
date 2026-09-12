#!/bin/bash
# StrokeGuard Quick Start Script

echo "═══════════════════════════════════════════════════════════"
echo "  NHS STROKEGUARD AI - Hackathon Demo"
echo "═══════════════════════════════════════════════════════════"
echo

# Check for required environment variables
if [ -z "$GEMINI_API_KEY" ] && [ ! -f "$HOME/.env" ]; then
    echo "⚠️  No GEMINI_API_KEY found. Codex agent will be unavailable."
    echo "   Set it: export GEMINI_API_KEY=your-key"
    echo
fi

# Activate Python venv
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# Kill any existing servers
pkill -f "api_server.py" 2>/dev/null
pkill -f "whatsapp_voice_server" 2>/dev/null
sleep 1

echo "🚀 Starting API server on http://localhost:8000..."
python3 api_server.py &
API_PID=$!
sleep 2

# Check if API server started
if ! curl -s http://127.0.0.1:8000/ > /dev/null; then
    echo "❌ API server failed to start"
    exit 1
fi

echo "✅ API server running"
echo

echo "═══════════════════════════════════════════════════════════"
echo "  STROKEGUARD IS READY"
echo "═══════════════════════════════════════════════════════════"
echo
echo "📊 Dashboard:     http://localhost:8000"
echo
echo "DEMO OPTIONS:"
echo "  1. Click 'Demo Mode' for a scripted presentation flow"
echo "  2. Click 'Browser Call' to test VAPI voice agent in-browser"
echo "  3. Click 'WhatsApp Call' for real phone calls (requires linked device)"
echo
echo "Press Ctrl+C to stop servers"

# Wait for Ctrl+C
trap "echo; echo 'Stopping servers...'; kill $API_PID 2>/dev/null; pkill -f whatsapp_voice_server 2>/dev/null; echo 'Done'; exit 0" INT
wait
