// The app's side of the box's two secure channels. The box's side, and the description
// of both, is anve-device/anve_device/pairing.py; the two must stay in step.
//
//  1. sealed message (first-time setup, pairing): X25519 + HKDF-SHA256 + ChaCha20-Poly1305
//  2. paired channel (everything after): HMAC-SHA256 request signature + ChaCha20-Poly1305
//     bodies, both from the 32-byte token the box hands over at setup or pairing
import { chacha20poly1305 } from '@noble/ciphers/chacha.js';
import { x25519 } from '@noble/curves/ed25519.js';
import { hkdf } from '@noble/hashes/hkdf.js';
import { hmac } from '@noble/hashes/hmac.js';
import { sha256 } from '@noble/hashes/sha2.js';

const INFO = 'anve-setup-v1';
const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';

export function utf8(s: string): Uint8Array {
  return new TextEncoder().encode(s);
}

export function fromUtf8(b: Uint8Array): string {
  return new TextDecoder().decode(b);
}

export function b64(bytes: Uint8Array): string {
  let out = '';
  for (let i = 0; i < bytes.length; i += 3) {
    const a = bytes[i], b = bytes[i + 1], c = bytes[i + 2];
    out += B64[a >> 2] + B64[((a & 3) << 4) | ((b ?? 0) >> 4)];
    out += i + 1 < bytes.length ? B64[((b & 15) << 2) | ((c ?? 0) >> 6)] : '=';
    out += i + 2 < bytes.length ? B64[c & 63] : '=';
  }
  return out;
}

export function unb64(s: string): Uint8Array {
  const clean = s.replace(/[^A-Za-z0-9+/]/g, '');
  const out = new Uint8Array(Math.floor((clean.length * 3) / 4));
  let n = 0, bits = 0, acc = 0;
  for (const ch of clean) {
    acc = (acc << 6) | B64.indexOf(ch);
    bits += 6;
    if (bits >= 8) {
      bits -= 8;
      out[n++] = (acc >> bits) & 0xff;
    }
  }
  return out.subarray(0, n);
}

export function hex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

export function unhex(s: string): Uint8Array {
  const out = new Uint8Array(s.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.substr(i * 2, 2), 16);
  return out;
}

export function randomBytes(n: number): Uint8Array {
  const out = new Uint8Array(n);
  (globalThis as any).crypto.getRandomValues(out);
  return out;
}

function concat(...parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
}

type Sealed = { n: string; c: string };

function aeadSeal(key: Uint8Array, obj: unknown, aad: string): Sealed {
  const n = randomBytes(12);
  return { n: b64(n), c: b64(chacha20poly1305(key, n, utf8(aad)).encrypt(utf8(JSON.stringify(obj)))) };
}

function aeadOpen(key: Uint8Array, msg: any, aad: string): any {
  if (!msg || typeof msg.n !== 'string' || typeof msg.c !== 'string') throw new Error('bad reply');
  return JSON.parse(fromUtf8(chacha20poly1305(key, unb64(msg.n), utf8(aad)).decrypt(unb64(msg.c))));
}

/** Seal `obj` for the box whose public key is `boxPub` (base64, from /api/hello). */
export function seal(boxPub: string, obj: unknown): { message: Sealed & { k: string }; key: Uint8Array } {
  const pub = unb64(boxPub);
  const sk = randomBytes(32);
  const epk = x25519.getPublicKey(sk);
  const key = hkdf(sha256, x25519.getSharedSecret(sk, pub), concat(pub, epk), utf8(INFO), 32);
  return { message: { k: b64(epk), ...aeadSeal(key, obj, INFO + '|req') }, key };
}

/** Open the box's sealed answer to a message made with `seal`. */
export function openReply(key: Uint8Array, msg: unknown): any {
  return aeadOpen(key, msg, INFO + '|rep');
}

/** The paired channel: signs requests, seals and opens bodies. */
export class Channel {
  private token: Uint8Array;
  private key: Uint8Array;

  constructor(tokenHex: string) {
    this.token = unhex(tokenHex);
    this.key = sha256(concat(utf8('anve-app-enc-v1'), this.token));
  }

  /** -> the request's nonce, the body to send and the X-ANVE-Auth header */
  request(method: string, target: string, obj: unknown | undefined, clockOffsetS = 0) {
    const nonce = hex(randomBytes(9));
    const body = obj === undefined ? '' : JSON.stringify(aeadSeal(this.key, obj, 'req|' + nonce));
    const ts = String(Math.floor(Date.now() / 1000) + clockOffsetS);
    const canonical = `${method}\n${target}\n${ts}\n${nonce}\n${hex(sha256(utf8(body)))}`;
    const mac = hex(hmac(sha256, this.token, utf8(canonical)));
    return { nonce, body, auth: `v1 ${ts} ${nonce} ${mac}` };
  }

  open(msg: unknown, nonce: string): any {
    return aeadOpen(this.key, msg, 'rep|' + nonce);
  }
}
