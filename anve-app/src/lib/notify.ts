// Notifications for things that wait for the person's approval.
//
// How they reach the phone (the ANVE platform has no push service, and must not be
// changed):
//   * app open or recently used: the box tells the app the moment something waits
//     (its /api/events), and the app also asks ANVE every few seconds
//   * app closed: Android wakes this task about every 15 minutes, it asks ANVE and
//     notifies about anything new. A read-back that expires in 5 minutes can be missed
//     this way; an agent step that waits for approval is not, it keeps waiting.
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';
import { Anve, Pending, pendingId } from './anve';
import { load, save } from './store';

// The check that runs while the app is closed needs two native parts that the installed
// app has and Expo Go (used to try the app on an iPhone) may not. Without them the app
// simply does not check in the background; everything else works.
let BackgroundTask: typeof import('expo-background-task') | null = null;
let TaskManager: typeof import('expo-task-manager') | null = null;
try {
  BackgroundTask = require('expo-background-task');
  TaskManager = require('expo-task-manager');
} catch {
  BackgroundTask = TaskManager = null;
}

const TASK = 'anve-pending-check';
const CHANNEL = 'approvals';

export function describe(p: Pending): { title: string; body: string } {
  if (p.kind === 'approval') {
    return { title: `${p.agent || 'An agent'} needs your approval`, body: p.message || p.what || 'A step is waiting for you.' };
  }
  return { title: 'ANVE needs your approval', body: p.readback || p.action || 'Something is waiting for you.' };
}

export async function initNotifications(): Promise<void> {
  Notifications.setNotificationHandler({
    handleNotification: async () => ({ shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: false }),
  });
  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync(CHANNEL, {
      name: 'Approvals',
      description: 'When ANVE or one of your agents waits for your yes or no',
      importance: Notifications.AndroidImportance.HIGH,
      vibrationPattern: [0, 180, 120, 180],
      lightColor: '#5B6CFF',
    });
  }
}

export async function askNotificationPermission(): Promise<boolean> {
  const cur = await Notifications.getPermissionsAsync();
  if (cur.granted) return true;
  if (!cur.canAskAgain) return false;
  return (await Notifications.requestPermissionsAsync()).granted;
}

export async function announce(p: Pending): Promise<void> {
  const { title, body } = describe(p);
  await Notifications.scheduleNotificationAsync({
    content: { title, body: body.slice(0, 220), data: { open: 'approvals', id: pendingId(p) }, sound: true },
    trigger: Platform.OS === 'android' ? ({ channelId: CHANNEL } as any) : null,
  });
}

export async function announceNotice(text: string): Promise<void> {
  await Notifications.scheduleNotificationAsync({
    content: { title: 'ANVE', body: text.slice(0, 220), data: { open: 'home' } },
    trigger: Platform.OS === 'android' ? ({ channelId: CHANNEL } as any) : null,
  });
}

/** Announce what has not been announced yet. -> the new list of announced ids. */
export async function announceNew(pending: Pending[], seen: string[], enabled: boolean): Promise<string[]> {
  const ids = pending.map(pendingId);
  const fresh = pending.filter((p) => !seen.includes(pendingId(p)));
  if (enabled) for (const p of fresh.slice(0, 4)) await announce(p).catch(() => {});
  // forget ids that are no longer waiting, so the list stays short
  return [...seen.filter((s) => ids.includes(s)), ...fresh.map(pendingId)].slice(-30);
}

try {
  TaskManager?.defineTask(TASK, async () => {
    const result = BackgroundTask!.BackgroundTaskResult;
    try {
      const s = await load();
      if (!s.user) return result.Success;
      const pending = await new Anve(s.server).pending();
      const seen = await announceNew(pending, s.seen, s.notify);
      await save({ ...(await load()), seen });
      return result.Success;
    } catch {
      return result.Failed;
    }
  });
} catch {}

export async function startBackgroundCheck(): Promise<void> {
  try {
    await BackgroundTask?.registerTaskAsync(TASK, { minimumInterval: 15 });
  } catch {}
}
