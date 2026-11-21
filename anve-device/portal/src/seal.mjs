// The setup page's side of the box's sealed message: the same construction as the phone
// app (anve-app/src/lib/crypto.ts) and the box (anve_device/pairing.py):
//   X25519 (one-time key) + HKDF-SHA256 + ChaCha20-Poly1305
//
// Why a library and not the browser's own crypto: the page is served over plain http on
// the box's hotspot, and browsers only offer crypto.subtle on https pages. These are the
// audited "noble" libraries the app uses, bundled into ../assets/seal.js by
// tools/build_portal.sh. Random numbers still come from the browser (crypto.getRandomValues,
// which works on http pages).
import { chacha20poly1305 } from '@noble/ciphers/chacha.js';
import { x25519 } from '@noble/curves/ed25519.js';
import { hkdf } from '@noble/hashes/hkdf.js';
import { sha256 } from '@noble/hashes/sha2.js';

const INFO = 'anve-setup-v1';
const enc = new TextEncoder();
const dec = new TextDecoder();

function b64(bytes) {
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

function unb64(text) {
  const s = atob(String(text));
  const out = new Uint8Array(s.length);
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
  return out;
}

function random(n) {
  const out = new Uint8Array(n);
  globalThis.crypto.getRandomValues(out);
  return out;
}

function concat(a, b) {
  const out = new Uint8Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

/** Seal `obj` for the box whose public key is `boxPub` (base64, from /api/status). */
function seal(boxPub, obj) {
  const pub = unb64(boxPub);
  if (pub.length !== 32) throw new Error('bad box key');
  const sk = random(32);
  const epk = x25519.getPublicKey(sk);
  const key = hkdf(sha256, x25519.getSharedSecret(sk, pub), concat(pub, epk), enc.encode(INFO), 32);
  const n = random(12);
  const c = chacha20poly1305(key, n, enc.encode(INFO + '|req')).encrypt(enc.encode(JSON.stringify(obj)));
  return { message: { k: b64(epk), n: b64(n), c: b64(c) }, key };
}

/** Open the box's sealed answer to a message made with `seal`. Throws if it is not genuine. */
function openReply(key, msg) {
  if (!msg || typeof msg.n !== 'string' || typeof msg.c !== 'string') throw new Error('bad reply');
  return JSON.parse(dec.decode(chacha20poly1305(key, unb64(msg.n), enc.encode(INFO + '|rep')).decrypt(unb64(msg.c))));
}

globalThis.AnveSeal = { seal, openReply };
