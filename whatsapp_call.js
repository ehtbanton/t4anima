/**
 * WhatsApp Voice Call via Baileys
 * Connects to WhatsApp using the multi-device protocol and initiates calls.
 *
 * Usage:
 *   node whatsapp_call.js          # First run: shows QR code to scan
 *   node whatsapp_call.js          # Subsequent runs: uses saved session
 *   node whatsapp_call.js +1234... # Call a specific number
 */

const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const { Boom } = require('@hapi/boom');
const pino = require('pino');
const qrcode = require('qrcode-terminal');
const crypto = require('crypto');

// Hardcoded test number
const DEFAULT_NUMBER = '79854311122'; // Without + prefix

// Session storage directory
const AUTH_DIR = './whatsapp_session';

function generateCallId() {
    return crypto.randomBytes(16).toString('hex').toUpperCase();
}

async function initiateCall(sock, jid) {
    const callId = generateCallId();
    const myJid = sock.user.id.split(':')[0] + '@s.whatsapp.net';

    console.log(`\n📞 Call ID: ${callId}`);
    console.log(`   From: ${myJid}`);
    console.log(`   To: ${jid}\n`);

    // WhatsApp call offer structure
    const callNode = {
        tag: 'call',
        attrs: {
            to: jid,
            id: `${Date.now()}`,
        },
        content: [{
            tag: 'offer',
            attrs: {
                'call-id': callId,
                'call-creator': myJid,
            },
            content: [
                {
                    tag: 'audio',
                    attrs: {
                        enc: 'opus',
                        rate: '16000'
                    }
                },
                {
                    tag: 'net',
                    attrs: {
                        medium: '3'
                    }
                },
                {
                    tag: 'encopt',
                    attrs: {
                        keygen: '2'
                    }
                },
                {
                    tag: 'capability',
                    attrs: {
                        ver: '1'
                    },
                    content: Buffer.from([1, 4, 255, 1, 255, 4, 1, 0])
                }
            ]
        }]
    };

    try {
        const result = await sock.query(callNode);
        console.log('✅ Call offer sent!');
        console.log('   Response:', JSON.stringify(result, null, 2));
        return { callId, result };
    } catch (err) {
        // Try alternative: sendNode without expecting response
        console.log('Query failed, trying sendNode...');
        try {
            await sock.sendNode(callNode);
            console.log('✅ Call signal sent via sendNode!');
            return { callId, result: 'sent' };
        } catch (e2) {
            console.error('❌ Both methods failed:', e2.message);
            throw e2;
        }
    }
}

async function startWhatsApp() {
    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);
    const { version, isLatest } = await fetchLatestBaileysVersion();

    console.log(`Using WA v${version.join('.')}, isLatest: ${isLatest}`);

    const sock = makeWASocket({
        version,
        logger: pino({ level: 'silent' }),
        printQRInTerminal: true,
        auth: state,
        browser: ['StrokeGuard', 'Desktop', '1.0.0'],
    });

    sock.ev.on('connection.update', async (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            console.log('\n📱 Scan this QR code with WhatsApp on your phone:');
            console.log('   Settings → Linked Devices → Link a Device\n');
            qrcode.generate(qr, { small: true });
        }

        if (connection === 'close') {
            const reason = new Boom(lastDisconnect?.error)?.output?.statusCode;
            if (reason === DisconnectReason.loggedOut) {
                console.log('Logged out. Delete ./whatsapp_session and restart.');
                process.exit(1);
            } else {
                console.log('Connection closed, reconnecting...');
                startWhatsApp();
            }
        } else if (connection === 'open') {
            console.log('\n✅ Connected to WhatsApp!');
            console.log(`   Logged in as: ${sock.user.id}\n`);

            const targetNumber = process.argv[2]
                ? process.argv[2].replace(/[^0-9]/g, '')
                : DEFAULT_NUMBER;

            const jid = `${targetNumber}@s.whatsapp.net`;

            // Small delay to ensure connection is stable
            await new Promise(r => setTimeout(r, 1000));

            try {
                const { callId } = await initiateCall(sock, jid);

                console.log('\n🎤 Call initiated!');
                console.log('   The recipient should see an incoming call.');
                console.log('   Press Ctrl+C to cancel.\n');

                // Keep connection alive
                setInterval(() => {}, 1000);

            } catch (err) {
                console.error('\n❌ Failed to initiate call:', err.message);
                console.log('\nNote: WhatsApp calling requires WebRTC for actual audio.');
                console.log('The signal was sent but full call support needs additional work.');
            }
        }
    });

    sock.ev.on('creds.update', saveCreds);

    // Handle call events (incoming calls, call updates)
    sock.ev.on('call', async (calls) => {
        for (const call of calls) {
            console.log('\n📲 Call event:', JSON.stringify(call, null, 2));

            if (call.status === 'ringing') {
                console.log('   📳 Ringing on recipient device!');
            } else if (call.status === 'accept') {
                console.log('   ✅ Call accepted!');
            } else if (call.status === 'reject') {
                console.log('   ❌ Call rejected');
            } else if (call.status === 'timeout') {
                console.log('   ⏱️ Call timed out (no answer)');
            }
        }
    });

    // Listen for all incoming messages (for debugging)
    sock.ws.on('CB:call', (node) => {
        console.log('\n📨 Raw call node received:', JSON.stringify(node, null, 2));
    });

    return sock;
}

console.log('═'.repeat(50));
console.log('  STROKEGUARD WHATSAPP CALLER');
console.log('  Using Baileys WhatsApp Protocol');
console.log('═'.repeat(50));
console.log();

startWhatsApp().catch(err => {
    console.error('Fatal error:', err);
    process.exit(1);
});
