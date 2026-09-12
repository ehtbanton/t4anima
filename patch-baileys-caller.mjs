#!/usr/bin/env node
/**
 * Patches baileys-caller to support mid-call audio injection.
 *
 * Adds:
 *   - call.sendAudio(pcmFloat32Array) - Send audio during the call
 *   - call.queueAudioFile(filePath) - Queue an audio file to play
 *
 * Run this after npm install:
 *   node patch-baileys-caller.mjs
 */

import { readFileSync, writeFileSync, existsSync } from "fs";
import { join } from "path";

const INDEX_PATH = join(process.cwd(), "node_modules/baileys-caller/dist/index.mjs");
const FEEDER_PATH = join(process.cwd(), "node_modules/baileys-caller/dist/audio-feeder.mjs");

function patchIndex() {
    if (!existsSync(INDEX_PATH)) {
        console.error("❌ baileys-caller not found at", INDEX_PATH);
        process.exit(1);
    }

    let content = readFileSync(INDEX_PATH, "utf-8");

    // Check if already patched
    if (content.includes("// PATCHED: sendAudio support")) {
        console.log("✅ index.mjs already patched");
        return;
    }

    // Find the ActiveCall class and add sendAudio method
    // Add after the mute method
    const mutePattern = /mute = \(muted\) => \{[\s\S]*?\};/;
    const muteMatch = content.match(mutePattern);

    if (!muteMatch) {
        console.error("❌ Could not find mute method in ActiveCall");
        process.exit(1);
    }

    const sendAudioMethod = `
    // PATCHED: sendAudio support
    /**
     * Send audio data during the call.
     * @param {Float32Array} pcmData - 16kHz mono float32 PCM audio
     */
    sendAudio = (pcmData) => {
        if (!this._audioQueue) this._audioQueue = [];
        // Queue the audio to be played
        this._audioQueue.push(pcmData);
    };
    /**
     * Check if there's queued audio to play
     */
    hasQueuedAudio = () => {
        return this._audioQueue && this._audioQueue.length > 0;
    };
    /**
     * Get next audio chunk from queue
     */
    getNextAudio = () => {
        if (!this._audioQueue || this._audioQueue.length === 0) return null;
        return this._audioQueue.shift();
    };`;

    content = content.replace(
        mutePattern,
        muteMatch[0] + sendAudioMethod
    );

    // Modify #handleAudioCaptureStart to use queued audio
    // Find the onChunk callback in #handleAudioCaptureStart
    const chunkCallbackPattern = /\(chunk\) => \{[\s\n]*if \(this\.#engine && this\.#capturePtr\)[\s\n]*this\.#engine\.sendAudioData\(chunk, this\.#capturePtr\);[\s\n]*\}/;

    const newChunkCallback = `(chunk) => {
            if (this.#engine && this.#capturePtr) {
                // PATCHED: Check for queued audio from sendAudio()
                const call = this.#activeCall;
                if (call && call.hasQueuedAudio && call.hasQueuedAudio()) {
                    const queuedAudio = call.getNextAudio();
                    if (queuedAudio && queuedAudio.length > 0) {
                        // Use queued audio instead of feeder audio
                        this.#engine.sendAudioData(queuedAudio, this.#capturePtr);
                        return;
                    }
                }
                this.#engine.sendAudioData(chunk, this.#capturePtr);
            }
        }`;

    if (content.match(chunkCallbackPattern)) {
        content = content.replace(chunkCallbackPattern, newChunkCallback);
        console.log("✅ Patched chunk callback to use queued audio");
    } else {
        console.warn("⚠️ Could not patch chunk callback - looking for alternative pattern");

        // Try alternative pattern (might have different formatting)
        const altPattern = /\(chunk\) => \{[^}]*sendAudioData[^}]*\}/;
        if (content.match(altPattern)) {
            content = content.replace(altPattern, newChunkCallback);
            console.log("✅ Patched chunk callback (alternative pattern)");
        } else {
            console.error("❌ Could not find chunk callback pattern");
        }
    }

    writeFileSync(INDEX_PATH, content);
    console.log("✅ Patched", INDEX_PATH);
}

function patchAudioFeeder() {
    if (!existsSync(FEEDER_PATH)) {
        console.error("❌ audio-feeder.mjs not found");
        return;
    }

    let content = readFileSync(FEEDER_PATH, "utf-8");

    // Check if already patched
    if (content.includes("// PATCHED: dynamic source")) {
        console.log("✅ audio-feeder.mjs already patched");
        return;
    }

    // Add method to change audio source
    const classEnd = content.lastIndexOf("}");
    const setSourceMethod = `
    // PATCHED: dynamic source
    setSource = (newSource) => {
        this.stop();
        this.source = newSource;
        this.start();
    };
`;

    content = content.slice(0, classEnd) + setSourceMethod + content.slice(classEnd);

    writeFileSync(FEEDER_PATH, content);
    console.log("✅ Patched", FEEDER_PATH);
}

console.log("\n🔧 Patching baileys-caller for mid-call audio support...\n");
patchIndex();
patchAudioFeeder();
console.log("\n✅ Patching complete!\n");
console.log("You can now use call.sendAudio(float32Array) during a call.\n");
