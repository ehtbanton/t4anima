/**
 * Shim — the real server is whatsapp_voice_server_v2.mjs (VAPI bridge).
 * Kept so anything that launches the old filename (e.g. a running Flask
 * api_server with the old spawn path in memory) gets the current server.
 */
import "./whatsapp_voice_server_v2.mjs";
