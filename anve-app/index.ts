import { registerRootComponent } from 'expo';
import { getRandomValues } from 'expo-crypto';

// The encryption code (src/lib/crypto.ts) needs the system's secure random numbers.
const g = globalThis as any;
if (!g.crypto?.getRandomValues) g.crypto = { ...(g.crypto ?? {}), getRandomValues };

// eslint-disable-next-line import/first
import App from './App';

registerRootComponent(App);
