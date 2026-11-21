// Used by tools/provision_test.py: runs the setup page's own encryption files in Node,
// so the test can prove the page and the box (pairing.py) understand each other.
//
//   node portal_seal.mjs seal  <box public key, base64>   < JSON on stdin
//        -> {"message": {...}, "key": "<base64>"}
//   node portal_seal.mjs open  <key, base64>              < the box's sealed answer on stdin
//        -> the opened answer as JSON
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
await import(pathToFileURL(join(here, '..', 'portal', 'assets', 'seal', 'seal.mjs')).href);
const { seal, openReply } = globalThis.AnveSeal;
const [what, arg] = process.argv.slice(2);
const input = JSON.parse(readFileSync(0, 'utf8'));
if (what === 'seal') {
  const { message, key } = seal(arg, input);
  process.stdout.write(JSON.stringify({ message, key: Buffer.from(key).toString('base64') }));
} else if (what === 'open') {
  process.stdout.write(JSON.stringify(openReply(new Uint8Array(Buffer.from(arg, 'base64')), input)));
} else {
  process.exit(2);
}
