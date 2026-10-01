// send-reminders - Supabase Edge Function that sends checklist reminders to
// phones as Web Push notifications, so they arrive even when the phone app
// is closed. A pg_cron job (supabase/push_reminders.sql) calls it every
// minute; due_push_reminders() in schema.sql decides what's due (and marks
// it, so each reminder goes out once a day). Calling it more often only
// finds nothing new to send.
//
// Secrets (supabase secrets set ...): VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY,
// a pair from `npx web-push generate-vapid-keys`; the public half is also
// VAPID_PUBLIC_KEY in docs/app.js. SUPABASE_URL and
// SUPABASE_SERVICE_ROLE_KEY are provided by Supabase itself.
import webpush from "npm:web-push@3.6.7";
import { createClient } from "npm:@supabase/supabase-js@2";

const SUBJECT = "https://github.com/beeftcg-eng/pawmodoro";

Deno.serve(async () => {
  const publicKey = Deno.env.get("VAPID_PUBLIC_KEY");
  const privateKey = Deno.env.get("VAPID_PRIVATE_KEY");
  if (!publicKey || !privateKey) {
    return Response.json({ error: "VAPID keys aren't set" }, { status: 500 });
  }
  webpush.setVapidDetails(SUBJECT, publicKey, privateKey);
  const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);

  const { data: due, error } = await db.rpc("due_push_reminders");
  if (error) return Response.json({ error: error.message }, { status: 500 });

  let sent = 0, dropped = 0, failed = 0;
  await Promise.all((due ?? []).map(async (row: Record<string, string>) => {
    const subscription = { endpoint: row.endpoint, keys: { p256dh: row.p256dh, auth: row.auth } };
    const payload = JSON.stringify({ title: row.title, body: row.body, tag: `task-${row.task_id}` });
    try {
      await webpush.sendNotification(subscription, payload, { TTL: 60 * 60, urgency: "high" });
      sent++;
    } catch (err) {
      const status = (err as { statusCode?: number }).statusCode;
      if (status === 404 || status === 410) {
        // That phone unsubscribed or the app was removed: forget it.
        await db.rpc("drop_push_subscription", { p_endpoint: row.endpoint });
        dropped++;
      } else {
        console.error("push failed", status, err);
        failed++;
      }
    }
  }));
  return Response.json({ sent, dropped, failed });
});
