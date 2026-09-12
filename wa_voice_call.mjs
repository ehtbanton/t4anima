/**
 * WhatsApp Voice Call using baileys-caller
 * Makes actual voice calls with audio streaming
 *
 * Usage:
 *   node wa_voice_call.mjs                    # Call default number
 *   node wa_voice_call.mjs +79854311122       # Call specific number
 */

import { VoipClient } from "baileys-caller";

const DEFAULT_NUMBER = "79854311122";
const AUTH_DIR = "./whatsapp_session";

async function main() {
    const targetNumber = process.argv[2]?.replace(/[^0-9]/g, '') || DEFAULT_NUMBER;

    console.log("═".repeat(50));
    console.log("  STROKEGUARD WHATSAPP VOICE CALL");
    console.log("  Using baileys-caller VoIP Stack");
    console.log("═".repeat(50));
    console.log();

    const client = new VoipClient({ authDir: AUTH_DIR });

    console.log("Connecting to WhatsApp...");
    await client.connect();
    console.log("✅ Connected!\n");

    console.log(`📞 Calling +${targetNumber}...`);

    try {
        const call = await client.call(targetNumber, {
            audioSource: "silence", // Start with silence, can stream audio later
            durationMs: 120000,     // 2 minute max
        });

        call.on("ringing", () => {
            console.log("📳 Ringing...");
        });

        call.on("connected", () => {
            console.log("✅ Connected! Call is live.");
            console.log("   Press Ctrl+C to hang up.\n");
        });

        call.on("audio", (pcm) => {
            // Received audio from the other side (Float32Array 16kHz mono)
            // Could pipe this to speech recognition
        });

        call.on("ended", (reason) => {
            console.log(`\n📴 Call ended: ${reason}`);
        });

        // Handle Ctrl+C
        process.on("SIGINT", () => {
            console.log("\n\nHanging up...");
            call.hangup();
        });

        await call.waitForEnd();

    } catch (err) {
        console.error("❌ Call failed:", err.message);
    }

    client.disconnect();
    console.log("\nDisconnected.");
}

main().catch(console.error);
