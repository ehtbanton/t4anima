/**
 * Patched VoIP Client for baileys-caller
 * Adds support for sending audio mid-call via a FIFO-based approach
 *
 * The trick: instead of passing a static file, we pass a long-running ffmpeg
 * process that reads from a FIFO, allowing us to pipe new audio dynamically.
 */

import { VoipClient } from "baileys-caller";
import { spawn, execSync } from "child_process";
import { existsSync, unlinkSync, writeFileSync, createWriteStream, openSync, writeSync, closeSync } from "fs";
import { tmpdir } from "os";
import { join } from "path";

/**
 * Create a FIFO-based audio source that allows streaming audio mid-call.
 *
 * How it works:
 * 1. Create a FIFO (named pipe)
 * 2. Start a long-running ffmpeg that reads from the FIFO
 * 3. Write PCM audio to the FIFO whenever we want to "speak"
 * 4. ffmpeg converts and streams to baileys-caller
 */
export class StreamingAudioSource {
    constructor(callId) {
        this.callId = callId;
        this.fifoPath = join(tmpdir(), `wa_stream_${callId}.fifo`);
        this.audioQueue = [];
        this.isWriting = false;
        this.isActive = false;
        this.fifoFd = null;
    }

    async init() {
        // Create FIFO
        if (existsSync(this.fifoPath)) {
            unlinkSync(this.fifoPath);
        }
        execSync(`mkfifo "${this.fifoPath}"`);
        console.log(`[StreamingAudio] Created FIFO: ${this.fifoPath}`);

        // Start background writer that keeps FIFO alive with silence
        this.isActive = true;
        this._startSilenceWriter();

        return this.fifoPath;
    }

    // Keep the FIFO fed with silence when not speaking
    async _startSilenceWriter() {
        // Generate 20ms of silence (16kHz * 2 bytes * 0.02s = 640 bytes)
        const silenceChunk = Buffer.alloc(640, 0);

        const writeLoop = async () => {
            if (!this.isActive) return;

            try {
                // Non-blocking open - will wait for reader
                if (!this.fifoFd) {
                    // Open in write mode (will block until reader connects)
                    this.fifoFd = openSync(this.fifoPath, 'w');
                }

                // Write queued audio or silence
                if (this.audioQueue.length > 0) {
                    const audio = this.audioQueue.shift();
                    writeSync(this.fifoFd, audio);
                } else {
                    writeSync(this.fifoFd, silenceChunk);
                }
            } catch (e) {
                // Pipe broken, reader closed
                this.fifoFd = null;
            }

            // 20ms interval
            setTimeout(writeLoop, 20);
        };

        // Start after a delay to let the call connect
        setTimeout(writeLoop, 1000);
    }

    // Queue audio data to be played
    async queueAudio(pcmBuffer) {
        if (!pcmBuffer || pcmBuffer.length === 0) return;

        // pcmBuffer should be 16-bit 16kHz mono PCM
        // Split into 20ms chunks (640 bytes each)
        const chunkSize = 640;
        for (let i = 0; i < pcmBuffer.length; i += chunkSize) {
            const chunk = pcmBuffer.subarray(i, Math.min(i + chunkSize, pcmBuffer.length));
            this.audioQueue.push(chunk);
        }

        console.log(`[StreamingAudio] Queued ${Math.ceil(pcmBuffer.length / chunkSize)} audio chunks`);
    }

    cleanup() {
        this.isActive = false;
        if (this.fifoFd) {
            try { closeSync(this.fifoFd); } catch {}
            this.fifoFd = null;
        }
        if (existsSync(this.fifoPath)) {
            try { unlinkSync(this.fifoPath); } catch {}
        }
    }
}

/**
 * Alternative approach: Pre-concatenate all audio and use lavfi for looping
 * This creates a long silent stream that we can't modify, but it's simpler.
 */
export function createSilentAudioSource(durationSeconds = 300) {
    // lavfi filter that generates silence for the duration
    return `lavfi:aevalsrc=0:d=${durationSeconds}:s=16000`;
}

/**
 * Wrapper around VoipClient that adds streaming audio support
 */
export class PatchedVoipClient extends VoipClient {
    constructor(config) {
        super(config);
        this.streamingSources = new Map();
    }

    async callWithStreaming(phoneNumber, opts = {}) {
        const callId = `call_${Date.now()}`;

        // If we have initial audio, we need a different approach
        // Option 1: Play initial audio, then switch to silence (current limitation)
        // Option 2: Use a FIFO from the start

        if (opts.useStreaming) {
            // FIFO approach - more complex but allows mid-call audio
            const streamSource = new StreamingAudioSource(callId);
            const fifoPath = await streamSource.init();

            // Queue the initial greeting if provided
            if (opts.initialPcm) {
                streamSource.queueAudio(opts.initialPcm);
            }

            const call = await this.call(phoneNumber, {
                audioSource: fifoPath,
                durationMs: opts.durationMs || 300000,
            });

            this.streamingSources.set(callId, streamSource);

            // Add method to send audio mid-call
            call.sendAudio = async (pcmBuffer) => {
                const source = this.streamingSources.get(callId);
                if (source) {
                    await source.queueAudio(pcmBuffer);
                }
            };

            // Cleanup on end
            const originalEnd = call.end.bind(call);
            call.end = () => {
                const source = this.streamingSources.get(callId);
                if (source) {
                    source.cleanup();
                    this.streamingSources.delete(callId);
                }
                originalEnd();
            };

            call.on("ended", () => {
                const source = this.streamingSources.get(callId);
                if (source) {
                    source.cleanup();
                    this.streamingSources.delete(callId);
                }
            });

            return { call, callId };
        }

        // Standard approach - just use the regular call method
        return {
            call: await this.call(phoneNumber, opts),
            callId
        };
    }
}

export default PatchedVoipClient;
